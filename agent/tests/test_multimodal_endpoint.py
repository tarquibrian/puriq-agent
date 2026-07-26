"""Pruebas del Chat_Endpoint multimodal (`POST /api/chat`) del spec multimodal-ingest.

Cubre las tareas OPCIONALES 10.2 y 10.3 del plan (Fase 3, canal web con binarios):

- **10.2 (Property 15):** todo error de ingesta o visión se entrega traducido por
  `wizard_error_response` (forma ``{causa, accion}`` o
  ``{documento, campo, sugerencia}``) y redactado, sin trazas crudas
  (``Traceback``) ni valores de secretos. Se ejercita haciendo que el
  `Chat_Agent` (doble) lance distintas excepciones con un valor de secreto
  incrustado, cubriendo **ambos** caminos del endpoint: JSON (Hito 2) y multipart
  (binarios, esta fase). (Validates: Requirements 11.4).
- **10.3 (integración/smoke):** con `TestClient` y un `Chat_Agent` doble,
  `POST` multipart con `mensaje` + un archivo en `binarios` responde 200 con
  exactamente ``{respuesta, estado}`` y el `ChatRequest` capturado lleva un
  `IncomingFile` con `filename`/`content` correctos y `archivos` como referencias
  (6.1, 6.2); `POST` JSON con `archivos` mantiene el comportamiento del Hito 2 con
  `binarios=[]` (6.3); `serve()` liga el servidor a `127.0.0.1` (11.1); y
  `pyproject.toml` declara **exactamente una** PDF_Library con pin exacto bajo el
  extra `pdf` (9.1, 9.2).

Aislamiento: el LLM y la ingesta real se aíslan por completo sustituyendo
(`monkeypatch`) `server.ChatAgent` por un doble programable, de modo que el turno
sea determinista y no toque ni red ni proveedor ni disco. `PURIQ_PROJECT` apunta a
un `tmp_path` con un contrato base sembrado (mismo patrón del Hito 1 y del
`test_web_chat_endpoint.py` del Hito 2, que aquí se REPLICA).

Nota de multipart (httpx/TestClient): para enviar `multipart/form-data` hay que
pasar `files=` para los binarios y `data=` para los campos de texto (con una lista
para los campos repetidos como `archivos`); httpx codifica una lista en `data`
como campos de formulario repetidos, que el endpoint lee con `form.getlist(...)`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
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
    (`contracts._doc_path`), para tener un proyecto de partida coherente con el
    patrón del Hito 1. En estas pruebas el `Chat_Agent` está mockeado, así que el
    contrato solo asegura un `PURIQ_PROJECT` bien formado.
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

_NAME_PREFIX = "PURIQ_TEST_MULTIMODAL_SECRET_"
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
# Doble programable del Chat_Agent + fixture del TestClient
# ---------------------------------------------------------------------------
class _FakeAgent:
    """Doble de `ChatAgent` cuyo comportamiento por turno se programa por afuera.

    El endpoint construye `ChatAgent(project)` y llama `run_turn(request)`. Este
    doble lee de un `holder` compartido (dict) qué hacer en cada turno:
      - `holder["raise"]`: una excepción a lanzar (para Property 15), o None.
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


# Un binario de imagen mínimo (firma PNG) para el camino multipart. En las
# pruebas de error el `Chat_Agent` está mockeado y NO corre la ingesta real, así
# que el contenido solo tiene que viajar por el multipart.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"binario-de-prueba-multimodal"


def _post_turn(client, channel: str, mensaje: str, archivos: list[str]):
    """Envía un turno por el canal indicado (`json` o `multipart`).

    - `json`: cuerpo `{mensaje, archivos[]}` (Hito 2).
    - `multipart`: campos `mensaje` (str), `archivos` (lista repetida de
      referencias, vía `data=`) y un `binarios` real (vía `files=`), forzando el
      `Content-Type: multipart/form-data` que el endpoint distingue (DD-M8).
    """
    if channel == "json":
        return client.post("/api/chat", json={"mensaje": mensaje, "archivos": archivos})
    # multipart: `data=` para los campos de texto (lista repetida para archivos),
    # `files=` para el binario. httpx codifica la lista como campos repetidos.
    return client.post(
        "/api/chat",
        data={"mensaje": mensaje, "archivos": archivos},
        files=[("binarios", ("cerro.jpg", _PNG_BYTES, "image/jpeg"))],
    )


# ---------------------------------------------------------------------------
# 10.2 — Property 15: los errores de ingesta o visión se entregan traducidos y redactados
# ---------------------------------------------------------------------------
# Feature: multimodal-ingest, Property 15: Los errores de ingesta o visión se
# entregan traducidos y redactados. Para toda excepción que se produce durante la
# ingesta o la visión (que el turno del Chat_Agent propaga con un valor de secreto
# incrustado), la respuesta de la superficie es el error traducido por
# wizard_error_response (forma {causa,acción} o {documento,campo,sugerencia}),
# status 422/500, sin trazas crudas ni valores de secretos, tanto por el camino
# JSON como por el multipart.
# Validates: Requirements 11.4
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
    channel=st.sampled_from(["json", "multipart"]),
)
def test_chat_endpoint_translates_and_redacts_ingest_errors(
    chat_client, secret_suffix, secret_value, noise, kind, channel
):
    """Toda excepción de ingesta/visión se traduce y redacta en ambos canales.

    Para cada tipo de excepción que aborta `run_turn` con un valor de secreto
    incrustado en su mensaje, el endpoint responde un cuerpo accionable
    (``{causa, accion}`` o ``{documento, campo, sugerencia}``) con status 422/500
    que NO contiene el valor crudo del secreto ni una traza (`Traceback`), tanto
    por el camino JSON (Hito 2) como por el multipart (binarios, esta fase).
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
        else:  # "network": clasificado por nombre de clase (Req 11.4)
            exc = type("FakeConnectError", (Exception,), {})(mensaje_exc)

        holder["raise"] = exc
        holder["response"] = None

        resp = _post_turn(client, channel, "analizá esta imagen", [])

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
    # ... y sin trazas crudas.
    assert "Traceback" not in texto


# ---------------------------------------------------------------------------
# 10.3 — Integración: POST multipart con binarios (6.1, 6.2)
# ---------------------------------------------------------------------------
def test_chat_endpoint_accepts_multipart_binaries(chat_client):
    """`POST` multipart con `mensaje` + un archivo en `binarios` responde 200.

    Verifica el cableado del canal multipart (Req 6.1, 6.2): el endpoint lee el
    `UploadFile` a bytes y lo envuelve en un `IncomingFile(filename, content)`
    para el Ingest_Router, mientras `archivos` viaja como lista de referencias.
    La respuesta conserva exactamente ``{respuesta, estado}``.
    """
    client, holder = chat_client
    estado = {
        "tourism-data": {"site": {"name": "Potosí"}},
        "site-config": {},
        "theme-tokens": {},
        "missing": [{"piece": "places", "field": None}],
    }
    holder["raise"] = None
    holder["response"] = _chat_response("Vi la foto del cerro que mandaste.", estado)

    contenido = b"\x89PNG\r\n\x1a\n bytes-reales-de-la-imagen-del-cerro"
    resp = client.post(
        "/api/chat",
        data={"mensaje": "te mando el cerro", "archivos": ["assets/plaza.png"]},
        files=[("binarios", ("cerro-rico.jpg", contenido, "image/jpeg"))],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"respuesta", "estado"}
    assert body["respuesta"] == "Vi la foto del cerro que mandaste."
    assert body["estado"] == estado

    # El turno recibió el mensaje, las referencias y el binario envuelto.
    captured = holder["captured"]
    assert captured.mensaje == "te mando el cerro"
    assert captured.archivos == ["assets/plaza.png"]
    assert len(captured.binarios) == 1
    incoming = captured.binarios[0]
    assert incoming.filename == "cerro-rico.jpg"
    assert incoming.content == contenido


def test_chat_endpoint_multipart_supports_multiple_binaries(chat_client):
    """Un `POST` multipart con varios `binarios` los entrega todos al turno (6.1, 6.2).

    Confirma que los campos `binarios` repetidos se leen con `form.getlist(...)`
    y que cada `UploadFile` se envuelve en su `IncomingFile`, preservando el
    orden, el nombre y los bytes de cada Archivo_Entrante.
    """
    client, holder = chat_client
    estado = {"tourism-data": {}, "site-config": {}, "theme-tokens": {}, "missing": []}
    holder["raise"] = None
    holder["response"] = _chat_response("Recibí tus dos archivos.", estado)

    img = b"\x89PNG\r\n\x1a\n imagen-del-lugar"
    pdf = b"%PDF-1.7\n contenido-del-folleto"
    resp = client.post(
        "/api/chat",
        data={"mensaje": "adjunto foto y folleto"},
        files=[
            ("binarios", ("plaza.png", img, "image/png")),
            ("binarios", ("folleto.pdf", pdf, "application/pdf")),
        ],
    )

    assert resp.status_code == 200, resp.text
    captured = holder["captured"]
    # `archivos` es opcional: sin el campo, queda como lista vacía.
    assert captured.archivos == []
    assert [b.filename for b in captured.binarios] == ["plaza.png", "folleto.pdf"]
    assert [b.content for b in captured.binarios] == [img, pdf]


# ---------------------------------------------------------------------------
# 10.3 — Integración: POST JSON mantiene el comportamiento del Hito 2 (6.3)
# ---------------------------------------------------------------------------
def test_chat_endpoint_json_keeps_milestone2_behavior(chat_client):
    """`POST` JSON con `archivos` mantiene el comportamiento del Hito 2 (6.3).

    El camino `application/json` sigue aceptando `{mensaje, archivos[]}` con las
    referencias a assets ya subidos y, por diseño (DD-M8), construye el turno con
    `binarios=[]` (sin binarios reales). La respuesta conserva ``{respuesta,
    estado}``.
    """
    client, holder = chat_client
    estado = {
        "tourism-data": {},
        "site-config": {},
        "theme-tokens": {},
        "missing": [],
    }
    holder["raise"] = None
    holder["response"] = _chat_response("Vi las fotos que ya subiste.", estado)

    archivos = ["assets/cerro-rico.jpg", "assets/plaza.png"]
    resp = client.post(
        "/api/chat", json={"mensaje": "adjunté fotos", "archivos": archivos}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"respuesta", "estado"}
    assert body["respuesta"] == "Vi las fotos que ya subiste."

    captured = holder["captured"]
    assert captured.mensaje == "adjunté fotos"
    assert captured.archivos == archivos
    # El camino JSON no transporta binarios (DD-M8): binarios=[].
    assert captured.binarios == []


# ---------------------------------------------------------------------------
# 10.3 — Smoke: serve() liga el servidor a 127.0.0.1 (11.1)
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


# ---------------------------------------------------------------------------
# 10.3 — Smoke: pyproject.toml declara exactamente una PDF_Library con pin exacto
# ---------------------------------------------------------------------------
# Pin exacto de la forma `nombre==X.Y[.Z...]` (sin rangos ni comparadores laxos).
_EXACT_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[0-9]+(?:\.[0-9]+)*)$")


def test_pyproject_declares_single_pinned_pdf_library():
    """El extra `pdf` declara EXACTAMENTE una PDF_Library con versión fijada (9.1, 9.2).

    Parsea `agent/pyproject.toml` con `tomllib` y verifica que
    `[project.optional-dependencies]` define un extra `pdf` con **una sola**
    dependencia, fijada a una versión exacta (`pypdf==X.Y.Z`), sin rangos.
    """
    pyproject_path = _AGENT_DIR / "pyproject.toml"
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)

    opt_deps = data["project"]["optional-dependencies"]
    assert "pdf" in opt_deps, "Falta el extra opcional 'pdf' en pyproject.toml"

    pdf_reqs = opt_deps["pdf"]
    # Exactamente una PDF_Library declarada (Req 9.1).
    assert len(pdf_reqs) == 1, f"Se esperaba una sola dependencia PDF, hay: {pdf_reqs}"

    match = _EXACT_PIN_RE.match(pdf_reqs[0].strip())
    # Versión fijada de forma exacta (Req 9.2): `nombre==X.Y.Z`, sin rangos.
    assert match is not None, f"La PDF_Library no está fijada a una versión exacta: {pdf_reqs[0]!r}"
    assert match.group("name").lower() == "pypdf"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
