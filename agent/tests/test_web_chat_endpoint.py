"""Pruebas del Chat_Endpoint (`POST /api/chat`) del spec conversational-web-chat.

Cubre las tareas OPCIONALES 7.2, 7.3 y 7.4 del plan (Pieza 6, canal web):

- **7.2 (Property 5):** todo error de tool-call o de turno se entrega traducido
  por `wizard_error_response` (forma ``{causa, accion}`` o
  ``{documento, campo, sugerencia}``) y redactado, sin trazas crudas ni valores
  de secretos. Se ejercita haciendo que el `Chat_Agent` lance distintas
  excepciones (Validates: Requirements 5.4, 6.4, 11.4).
- **7.3 (Property 15):** toda `Chat_Response` se devuelve redactada: si el estado
  o la respuesta del turno contuvieran un valor registrado como secreto, la
  respuesta del endpoint no lo expone en crudo (Validates: Requirements 11.2).
- **7.4 (integración/smoke):** con `TestClient` y un `Chat_Agent` doble,
  `POST /api/chat` con y sin `archivos` responde 200 con exactamente
  ``{respuesta, estado}`` (6.1, 6.2, 6.3); y `serve()` liga el servidor a
  `127.0.0.1` (11.1) sin arrancarlo de verdad.

Aislamiento: el LLM se aísla por completo sustituyendo (`monkeypatch`)
`server.ChatAgent` por un doble programable, de modo que el turno sea
determinista y no toque ni red ni proveedor. `PURIQ_PROJECT` apunta a un
`tmp_path` con un contrato base sembrado (mismo patrón que el Hito 1:
`contracts._base_document` + `contracts._doc_path`). No se corre build ni LLM.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio (mismo patrón que las pruebas existentes).
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq import config  # noqa: E402
from puriq.wizard import contracts, server  # noqa: E402


# ---------------------------------------------------------------------------
# Utilidades: siembra del contrato base y registro de secretos
# ---------------------------------------------------------------------------
_CONTRACT_DOCS = ("tourism-data", "site-config", "theme-tokens")


def _seed_base_contract(project: Path) -> None:
    """Siembra los 3 documentos base del contrato en `project`.

    Escribe el JSON base de cada documento directamente en su ruta
    (`contracts._doc_path`), sin pasar por la validación de escritura, para tener
    un proyecto de partida coherente con el patrón del Hito 1. En estas pruebas
    el `Chat_Agent` está mockeado, así que el contrato solo asegura un
    `PURIQ_PROJECT` bien formado.
    """
    for doc in _CONTRACT_DOCS:
        path = contracts._doc_path(project, doc)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(contracts._base_document(doc), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# Alfabeto realista de credenciales/tokens: letras, dígitos y símbolos que las
# APIs suelen usar. Se EXCLUYE '*' porque la máscara de `redact` está compuesta
# por '*' y un secreto con '*' podría "reaparecer" dentro de la propia máscara y
# producir un falso negativo ajeno a la propiedad.
_secret_char = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/+=-_"
)
# Longitud mínima realista (>=16, como una credencial AWS) para no colisionar con
# el "chrome" estático en español que `wizard_error_response` agrega (p. ej.
# "Entrada invalida", "Revisa el documento...") y que no pasa por `redact`.
_secret_value = st.text(alphabet=_secret_char, min_size=16, max_size=48)

_NAME_PREFIX = "PURIQ_TEST_WEBCHAT_SECRET_"
_name_suffix = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_", min_size=1, max_size=10
)


class _register_secret:
    """Context manager que registra un secreto y restaura el estado al salir.

    Fija el valor en `os.environ`, lo registra como secreto vía
    `config.get_env(name, secret=True)` (misma vía que el código real) y, al
    salir, restaura el entorno y el registro interno `config._secret_names` para
    no filtrar estado entre ejemplos de Hypothesis.
    """

    def __init__(self, name: str, value: str):
        self._name = name
        self._value = value
        self._saved_env: str | None = None
        self._saved_names: set[str] = set()

    def __enter__(self) -> "_register_secret":
        self._saved_names = set(config._secret_names)
        self._saved_env = os.environ.get(self._name)
        os.environ[self._name] = self._value
        assert config.get_env(self._name, secret=True) == self._value
        return self

    def __exit__(self, *exc) -> bool:
        config._secret_names = self._saved_names
        if self._saved_env is None:
            os.environ.pop(self._name, None)
        else:
            os.environ[self._name] = self._saved_env
        return False


# ---------------------------------------------------------------------------
# Doble programable del Chat_Agent + fixtures del TestClient
# ---------------------------------------------------------------------------
class _FakeAgent:
    """Doble de `ChatAgent` cuyo comportamiento por turno se programa por afuera.

    El endpoint construye `ChatAgent(project)` y llama `run_turn(request)`. Este
    doble lee de un `holder` compartido (dict) qué hacer en cada turno:
      - `holder["raise"]`: una excepción a lanzar (para Property 5), o None.
      - `holder["response"]`: el objeto a devolver (con `.respuesta`/`.estado`).
      - `holder["captured"]`: se rellena con el `ChatRequest` recibido.
    """

    holder: dict = {}

    def __init__(self, project, *args, **kwargs):
        self.project = project

    def run_turn(self, request):
        type(self).holder["captured"] = request
        exc = type(self).holder.get("raise")
        if exc is not None:
            raise exc
        return type(self).holder["response"]


@pytest.fixture
def chat_client(tmp_path, monkeypatch):
    """TestClient con `server.ChatAgent` mockeado y `PURIQ_PROJECT` sembrado.

    Devuelve `(client, holder)`; el `holder` programa el turno del doble
    (`raise`/`response`) y expone el `ChatRequest` capturado.
    """
    _seed_base_contract(tmp_path)
    monkeypatch.setenv(server.PROJECT_ENV_VAR, str(tmp_path))

    holder: dict = {"raise": None, "response": None, "captured": None}
    _FakeAgent.holder = holder
    monkeypatch.setattr(server, "ChatAgent", _FakeAgent)

    client = TestClient(server.app)
    return client, holder


def _chat_response(respuesta: str, estado: dict):
    """Construye un objeto de respuesta con la forma que consume el endpoint.

    El endpoint solo lee `resp.respuesta` y `resp.estado`, de modo que un
    `SimpleNamespace` es suficiente y evita acoplar la prueba a la firma exacta
    del `ChatResponse` de producción.
    """
    return types.SimpleNamespace(respuesta=respuesta, estado=estado)


# ---------------------------------------------------------------------------
# 7.2 — Property 5: todo error de turno se entrega traducido y redactado
# ---------------------------------------------------------------------------
# Feature: conversational-web-chat, Property 5: Todo error de Tool_Call o de
# turno se entrega traducido y redactado. Para toda excepción que aborta el
# procesamiento del turno, el Chat_Endpoint responde el error traducido por
# wizard_error_response (forma {causa,acción} o {documento,campo,sugerencia}),
# sin trazas crudas ni valores de secretos.
# Validates: Requirements 5.4, 6.4, 11.4
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    secret_suffix=_name_suffix,
    secret_value=_secret_value,
    noise=st.text(alphabet=_secret_char, max_size=40),
    kind=st.sampled_from(["value", "schema", "runtime", "file", "network"]),
)
def test_chat_endpoint_translates_and_redacts_turn_errors(
    chat_client, secret_suffix, secret_value, noise, kind
):
    """Toda excepción del turno se traduce y redacta, sin trazas ni secretos.

    Para cada tipo de excepción que aborta `run_turn` con un valor de secreto
    incrustado en su mensaje, el endpoint responde un cuerpo accionable
    (``{causa, accion}`` o ``{documento, campo, sugerencia}``) que NO contiene el
    valor crudo del secreto ni una traza (`Traceback`).
    """
    client, holder = chat_client
    name = _NAME_PREFIX + secret_suffix

    with _register_secret(name, secret_value):
        mensaje_exc = f"{noise} fuga={secret_value} fin"
        if kind == "value":
            exc: Exception = ValueError(mensaje_exc)
        elif kind == "schema":
            exc = jsonschema.ValidationError(mensaje_exc)
        elif kind == "runtime":
            exc = RuntimeError(mensaje_exc)
        elif kind == "file":
            exc = FileNotFoundError(mensaje_exc)
        else:  # "network": clasificado por nombre de clase (Req 6.4)
            exc = type("FakeConnectError", (Exception,), {})(mensaje_exc)

        holder["raise"] = exc
        holder["response"] = None

        resp = client.post("/api/chat", json={"mensaje": "hola", "archivos": []})

    # El error se traduce a un código accionable del wizard (422/500), nunca 200.
    assert resp.status_code in (422, 500), resp.text

    body = resp.json()
    assert isinstance(body, dict)

    # La forma es una de las dos que produce `wizard_error_response`.
    keys = set(body.keys())
    forma_causa = {"causa", "accion"}
    forma_schema = {"documento", "campo", "sugerencia"}
    assert keys in (forma_causa, forma_schema), keys

    texto = json.dumps(body, ensure_ascii=False)
    # Sin valor de secreto en crudo (Req 11.4) ...
    assert secret_value not in texto
    # ... y sin trazas crudas (Req 6.4).
    assert "Traceback" not in texto


# ---------------------------------------------------------------------------
# 7.3 — Property 15: toda Chat_Response se devuelve redactada
# ---------------------------------------------------------------------------
# Feature: conversational-web-chat, Property 15: Toda Chat_Response se devuelve
# redactada. Para todo turno cuyo estado o respuesta contendría un valor
# registrado como secreto, la Chat_Response del endpoint no contiene el valor
# crudo.
# Validates: Requirements 11.2
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    secret_suffix=_name_suffix,
    secret_value=_secret_value,
    in_respuesta=st.booleans(),
    in_estado=st.booleans(),
)
def test_chat_endpoint_redacts_chat_response(
    chat_client, secret_suffix, secret_value, in_respuesta, in_estado
):
    """Un secreto inyectado en la respuesta o el estado del turno no se expone.

    El doble del `Chat_Agent` devuelve una `Chat_Response` con un valor de
    secreto incrustado en `respuesta` y/o `estado`; el endpoint aplica
    `redact_value` de modo que el valor crudo no aparece en la salida HTTP.
    """
    client, holder = chat_client
    name = _NAME_PREFIX + secret_suffix

    # Al menos uno de los dos lugares lleva el secreto, para que el ejemplo sea
    # informativo (si ambos son False, se fuerza en la respuesta).
    if not in_respuesta and not in_estado:
        in_respuesta = True

    with _register_secret(name, secret_value):
        respuesta = (
            f"Guardé la credencial {secret_value} en el estado."
            if in_respuesta
            else "Listo, registré tus datos."
        )
        estado = {
            "tourism-data": {
                "site": {"name": "Potosí"},
                # El secreto puede colarse en cualquier string del estado.
                "note": secret_value if in_estado else "sin novedad",
            },
            "site-config": {},
            "theme-tokens": {},
            "missing": [],
        }
        holder["raise"] = None
        holder["response"] = _chat_response(respuesta, estado)

        resp = client.post(
            "/api/chat", json={"mensaje": "registrá mi token", "archivos": []}
        )

    assert resp.status_code == 200, resp.text
    texto = resp.text
    assert secret_value not in texto

    # La respuesta conserva la forma {respuesta, estado} aun tras redactar.
    body = resp.json()
    assert set(body.keys()) == {"respuesta", "estado"}


# ---------------------------------------------------------------------------
# 7.4 — Integración: POST /api/chat con y sin `archivos` (6.1, 6.2, 6.3)
# ---------------------------------------------------------------------------
def test_chat_endpoint_returns_respuesta_and_estado_without_archivos(chat_client):
    """`POST /api/chat` sin `archivos` responde 200 con exactamente {respuesta, estado}.

    Verifica el cableado del endpoint (Req 6.1, 6.3): corre un turno del
    `Chat_Agent` (doble) y devuelve su `{respuesta, estado}`; `archivos` es
    opcional y por defecto vacío (Req 6.2).
    """
    client, holder = chat_client
    estado = {
        "tourism-data": {"site": {"name": "Uyuni"}},
        "site-config": {},
        "theme-tokens": {},
        "missing": [{"piece": "places", "field": None}],
    }
    holder["raise"] = None
    holder["response"] = _chat_response("¡Hola! ¿Cómo se llama tu pueblo?", estado)

    resp = client.post("/api/chat", json={"mensaje": "hola"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"respuesta", "estado"}
    assert body["respuesta"] == "¡Hola! ¿Cómo se llama tu pueblo?"
    assert body["estado"] == estado

    # El turno recibió el mensaje y una lista de archivos vacía por defecto.
    captured = holder["captured"]
    assert captured.mensaje == "hola"
    assert captured.archivos == []


def test_chat_endpoint_accepts_archivos_references(chat_client):
    """`POST /api/chat` con `archivos` los pasa al turno y responde {respuesta, estado}.

    `archivos` es una lista de referencias a assets ya subidos (Req 6.2, 8.1);
    el endpoint las traslada al `ChatRequest` del turno sin transportar binarios.
    """
    client, holder = chat_client
    estado = {
        "tourism-data": {},
        "site-config": {},
        "theme-tokens": {},
        "missing": [],
    }
    holder["raise"] = None
    holder["response"] = _chat_response("Vi las fotos que subiste.", estado)

    archivos = ["assets/cerro-rico.jpg", "assets/plaza.png"]
    resp = client.post(
        "/api/chat", json={"mensaje": "adjunté fotos", "archivos": archivos}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"respuesta", "estado"}
    assert body["respuesta"] == "Vi las fotos que subiste."

    captured = holder["captured"]
    assert captured.mensaje == "adjunté fotos"
    assert captured.archivos == archivos


# ---------------------------------------------------------------------------
# 7.4 — Smoke: serve() liga el servidor a 127.0.0.1 (11.1)
# ---------------------------------------------------------------------------
def test_serve_binds_to_localhost(monkeypatch):
    """`serve()` liga el servidor a `127.0.0.1` sin arrancarlo de verdad (Req 11.1).

    Se parchea `uvicorn.run` con un espía que captura los argumentos, de modo que
    la prueba verifique el host de bind sin abrir un socket ni bloquear.
    """
    import uvicorn

    captured: dict = {}

    def _fake_run(app, *args, **kwargs):
        captured["app"] = app
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", _fake_run)

    server.serve(port=4321)

    assert captured, "uvicorn.run no fue invocado por serve()"
    assert captured["kwargs"].get("host") == "127.0.0.1"
    # Se sirve la app del wizard en el puerto solicitado.
    assert captured["app"] is server.app
    assert captured["kwargs"].get("port") == 4321


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
