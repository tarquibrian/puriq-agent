"""Pruebas del Chat_Agent multimodal (spec multimodal-ingest, tareas 7.2-7.5).

Implementa, en un único archivo, las propiedades de correctitud del nivel del
**Chat_Agent** para la ingesta multimodal (Hitos 3-4) y los ejemplos de la tarea
7.5:

  - Property 6 (7.2): el Texto_Extraido entra en el contexto del turno.
  - Property 9 (7.3): el agente envía la imagen a un proveedor con visión.
  - Property 10 (7.4): el agente inyecta los bytes de la imagen por nombre.
  - Ejemplos (7.5): despacho de escrituras tras confirmación, contrato intacto
    sin escrituras, y degradación sin visión (nota que nombra PURIQ_LLM_MODE).

Cada prueba:
  - opera sobre un **proyecto temporal** aislado por ejemplo (subdirectorio único
    bajo `tmp_path`) con los tres JSON del contrato,
  - inyecta un **proveedor mock** programable en `ChatAgent(provider=...)`, con un
    atributo `supports_vision` configurable y un `complete_chat` que captura los
    `messages` recibidos,
  - las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones
    y llevan el comentario `# Feature: multimodal-ingest, Property {N}: ...`.

El comportamiento del núcleo de intake y del Ingest_Router ya está cubierto por
otras pruebas del spec; aquí solo se prueban las invariantes que aporta el
Chat_Agent como superficie multimodal (contexto del turno, envío de imágenes con
visión, inyección de bytes por nombre y degradación sin visión). Replica los
patrones del Hito 2 (`test_web_chat_agent.py`): MockProvider, siembra del proyecto
temporal y `_Recorder`/patch de `run_intake_tool`.
"""
from __future__ import annotations

import base64
import json
import string
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import puriq.intake.agent as agent_mod
from puriq.intake.agent import ChatAgent, ChatRequest
from puriq.intake.ingest import IncomingFile, IngestResult
from puriq.intake.tools import get_state
from puriq.tools.generate_content import ChatResult, ImageContent, Message, ToolCall
from puriq.wizard import contracts
from puriq.wizard.assets import IMAGE_EXTS, normalize_asset_name

#: Configuración común de PBT: >=100 iteraciones, sin deadline (E/S en tmp) y se
#: suprime el health-check de fixture de función (usamos `tmp_path` como raíz y
#: creamos un subdirectorio único por ejemplo, así que el aislamiento es real).
pbt = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

#: Extensiones raster que un modelo de visión acepta y el mapa a su media type
#: (coherente con `_VISION_MEDIA_TYPES` del Ingest_Router).
_RASTER_MEDIA = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


# --- Proveedor mock programable ----------------------------------------------
class MockProvider:
    """Proveedor de LLM determinista para el bucle del Chat_Agent.

    Se programa con un `script`: una lista de pasos. Cada paso es una tupla:
      - ``("tools", [ToolCall, ...])`` -> el modelo pide esas herramientas.
      - ``("text", "respuesta")``      -> el modelo responde texto final.

    Captura en `received` los `messages` de cada llamada (para inspeccionar el
    contexto del turno) y en `tools_received` la lista de tools recibida. El
    atributo `supports_vision` es configurable (DD-M7): el Chat_Agent lo consulta
    para decidir si adjunta las imágenes al modelo. Si el script se agota,
    termina el turno con un texto.
    """

    def __init__(self, script, *, supports_vision: bool = True):
        self.script = list(script)
        self.supports_vision = supports_vision
        self.received: list[list] = []
        self.tools_received: list = []
        self._i = 0

    def complete(self, prompt: str) -> str:  # pragma: no cover - no usado aquí
        raise NotImplementedError("MockProvider no implementa complete()")

    def complete_chat(self, messages, tools=None) -> ChatResult:
        # Capturar la lista de mensajes de esta llamada para inspeccionarla
        # (el `content` y las `images` del mensaje de usuario son lo que se
        # verifica en las pruebas).
        self.received.append(list(messages))
        self.tools_received.append(tools)

        if self._i < len(self.script):
            kind, payload = self.script[self._i]
            self._i += 1
        else:
            kind, payload = ("text", "fin del turno")

        if kind == "tools":
            tool_calls = list(payload)
            assistant = Message(
                role="assistant", content=None, tool_calls=tool_calls or None
            )
            return ChatResult(
                text=None, tool_calls=tool_calls, assistant_message=assistant
            )
        text = payload
        assistant = Message(role="assistant", content=text)
        return ChatResult(text=text, tool_calls=[], assistant_message=assistant)


# --- Helpers de proyecto temporal --------------------------------------------
_CONTRACT_FILES = (contracts.DATA, contracts.CONFIG, contracts.THEME)


def _init_project(project: Path) -> Path:
    """Escribe un contrato base válido (los 3 JSON) en `project`.

    Identidad de sitio válida y sin lugares; `site-config` sin módulos; colores
    marcadores por defecto en `theme-tokens` (mismo punto de partida que las
    pruebas del Hito 2).
    """
    tourism = {
        "site": {
            "name": "Potosí",
            "region": "Potosí",
            "defaultLocale": "es",
            "center": {"lat": -19.58, "lng": -65.75},
        },
        "places": [],
    }
    site_config = {"layout": "clasico", "modules": {}}
    theme = {
        "colors": {"primary": "#000000", "background": "#ffffff", "text": "#111111"},
        "typography": {"headingFont": "Inter", "bodyFont": "Inter"},
    }
    (project / contracts.DATA).write_text(
        json.dumps(tourism, ensure_ascii=False), encoding="utf-8"
    )
    (project / contracts.CONFIG).write_text(
        json.dumps(site_config, ensure_ascii=False), encoding="utf-8"
    )
    (project / contracts.THEME).write_text(
        json.dumps(theme, ensure_ascii=False), encoding="utf-8"
    )
    return project


def _new_project(tmp_path: Path) -> Path:
    """Crea un subdirectorio único bajo `tmp_path` con un contrato base inicial."""
    project = Path(tempfile.mkdtemp(dir=tmp_path))
    return _init_project(project)


def _snapshot_contract(project: Path) -> dict[str, bytes | None]:
    """Bytes crudos de los 3 archivos del contrato (o None si faltan)."""
    snap: dict[str, bytes | None] = {}
    for fname in _CONTRACT_FILES:
        path = project / fname
        snap[fname] = path.read_bytes() if path.exists() else None
    return snap


def _last_user_message(messages) -> Message:
    """Devuelve el último mensaje de rol `user` de una lista (el turno actual)."""
    user_msgs = [m for m in messages if getattr(m, "role", None) == "user"]
    assert user_msgs, "debe existir al menos un mensaje de usuario en el contexto"
    return user_msgs[-1]


class _Recorder:
    """Reemplazo instrumentado de `run_intake_tool` que registra los despachos.

    Registra `(name, args)` de cada despacho (patrón del Hito 2). Si `delegate`
    es una función, la invoca; si no, devuelve un resultado benigno sin efectos,
    útil cuando solo interesa inspeccionar los argumentos (incluida la inyección
    de `content_base64`).
    """

    def __init__(self, delegate=None):
        self.calls: list[tuple[str, dict]] = []
        self._delegate = delegate

    def __call__(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self._delegate is not None:
            return self._delegate(name, arguments)
        return {"ok": True}


def _patch_run_intake_tool(recorder) -> callable:
    """Instala `recorder` como `run_intake_tool` del módulo agent; devuelve restaurador."""
    original = agent_mod.run_intake_tool
    agent_mod.run_intake_tool = recorder

    def restore():
        agent_mod.run_intake_tool = original

    return restore


# --- Estrategias reutilizables -----------------------------------------------
_ascii_lower = string.ascii_lowercase
_stem_st = st.text(alphabet=_ascii_lower, min_size=1, max_size=10)
_raster_ext_st = st.sampled_from(sorted(_RASTER_MEDIA))
_img_bytes_st = st.binary(min_size=1, max_size=2048)
#: Texto extraído arbitrario, sin espacios ni secretos, para que aparezca
#: verbatim en el contexto (strip = identidad, no vacío).
_pdf_text_st = st.text(
    alphabet=string.ascii_letters + string.digits, min_size=1, max_size=60
)


# =============================================================================
# Property 6 (task 7.2)
# =============================================================================
# Feature: multimodal-ingest, Property 6: El Texto_Extraido entra en el contexto del turno
@pbt
@given(text=_pdf_text_st, mensaje=st.text(alphabet=_ascii_lower + " ", max_size=20))
def test_p6_pdf_text_enters_turn_context(tmp_path, text, mensaje):
    """Para todo texto que el PDF_Extractor devuelve (inyectado vía
    `prepare_incoming` -> `pdf_texts`), el `content` del mensaje de usuario que el
    Chat_Agent pasa a `complete_chat` contiene ese texto.

    Validates: Requirements 3.2
    """
    project = _new_project(tmp_path)
    mock = MockProvider([("text", "listo")])

    # Se parchea `prepare_incoming` (visto por el agente) para inyectar el
    # Texto_Extraido en `pdf_texts`, aislando la propiedad del backend de pypdf.
    original = agent_mod.prepare_incoming
    agent_mod.prepare_incoming = (
        lambda files, *, supports_vision: IngestResult(pdf_texts=[text])
    )
    try:
        ChatAgent(project, provider=mock).run_turn(
            ChatRequest(
                mensaje=mensaje,
                binarios=[IncomingFile(filename="folleto.pdf", content=b"%PDF-1.4")],
            )
        )
    finally:
        agent_mod.prepare_incoming = original

    user_content = _last_user_message(mock.received[0]).content or ""
    assert text in user_content


# =============================================================================
# Property 9 (task 7.3)
# =============================================================================
# Feature: multimodal-ingest, Property 9: El agente envía la imagen a un proveedor con visión
@pbt
@given(stem=_stem_st, ext=_raster_ext_st, content=_img_bytes_st)
def test_p9_image_sent_to_vision_provider(tmp_path, stem, ext, content):
    """Para todo binario de imagen válido del turno, con `supports_vision=True`, el
    `Message` de usuario que el Chat_Agent pasa a `complete_chat` incluye esa
    imagen en `images` (ImageContent con media_type + data base64).

    Validates: Requirements 2.2
    """
    project = _new_project(tmp_path)
    filename = f"{stem}.{ext}"
    mock = MockProvider([("text", "ok")], supports_vision=True)

    ChatAgent(project, provider=mock).run_turn(
        ChatRequest(
            mensaje="mirá esta foto",
            binarios=[IncomingFile(filename=filename, content=content)],
        )
    )

    user_msg = _last_user_message(mock.received[0])
    assert user_msg.images, "el mensaje de usuario debe llevar la imagen adjunta"
    # Exactamente una imagen raster, con su media type y sus bytes en base64.
    assert len(user_msg.images) == 1
    image = user_msg.images[0]
    assert isinstance(image, ImageContent)
    assert image.media_type == _RASTER_MEDIA[ext]
    assert image.data == base64.b64encode(content).decode("ascii")


# =============================================================================
# Property 10 (task 7.4)
# =============================================================================
@st.composite
def _unique_images(draw) -> dict[str, bytes]:
    """Genera un mapeo filename -> bytes con nombres normalizados únicos.

    Los stems son ASCII en minúsculas y únicos, de modo que sus nombres
    normalizados (`slug.ext`) también lo son y el mapeo bytes<->nombre es
    biyectivo (sin colisiones al inyectar por nombre).
    """
    n = draw(st.integers(min_value=1, max_value=3))
    stems = draw(st.lists(_stem_st, min_size=n, max_size=n, unique=True))
    files: dict[str, bytes] = {}
    for stem in stems:
        ext = draw(_raster_ext_st)
        content = draw(_img_bytes_st)
        files[f"{stem}.{ext}"] = content
    return files


# Feature: multimodal-ingest, Property 10: El agente inyecta los bytes de la imagen por nombre de archivo
@pbt
@given(images=_unique_images())
def test_p10_agent_injects_image_bytes_by_filename(tmp_path, images):
    """Para toda tool-call `attach_asset` cuyo `filename` coincide con un binario
    de imagen del turno, los argumentos entregados a `run_intake_tool` contienen
    `content_base64 = base64(bytes)` y `project = Project_Root`, de modo que los
    bytes NO transitan por el modelo.

    Validates: Requirements 2.1
    """
    project = _new_project(tmp_path)

    # El nombre normalizado es la clave con que el agente retiene los bytes; se
    # indexa el contenido esperado por esa clave para verificar la inyección.
    key_to_content = {
        normalize_asset_name(fname, IMAGE_EXTS): content
        for fname, content in images.items()
    }
    # El modelo emite un attach_asset por imagen, nombrándola tal cual la vio.
    calls = [
        ToolCall(
            id=f"tc_{i}",
            name="attach_asset",
            arguments={"filename": fname, "target": "place", "id": "cerro-rico"},
        )
        for i, fname in enumerate(images)
    ]
    mock = MockProvider([("tools", calls), ("text", "asociadas")])

    recorder = _Recorder()  # no delega: solo inspecciona los argumentos
    restore = _patch_run_intake_tool(recorder)
    try:
        ChatAgent(project, provider=mock).run_turn(
            ChatRequest(
                mensaje="adjunto fotos",
                binarios=[
                    IncomingFile(filename=fname, content=content)
                    for fname, content in images.items()
                ],
            )
        )
    finally:
        restore()

    assert len(recorder.calls) == len(images)
    for name, args in recorder.calls:
        assert name == "attach_asset"
        # project = Project_Root (los bytes no viajan por el modelo).
        assert args.get("project") == str(project)
        key = normalize_asset_name(args["filename"], IMAGE_EXTS)
        expected = base64.b64encode(key_to_content[key]).decode("ascii")
        assert args.get("content_base64") == expected


# =============================================================================
# Ejemplos (task 7.5)
# =============================================================================
def test_example_write_tools_dispatch_after_confirmation(tmp_path):
    """Un mock que emite `edit_item`/`add_qa` (tras la "confirmación" del usuario)
    despacha por `run_intake_tool` con el Project_Root inyectado.

    Requirements: 2.4, 3.3
    """
    project = _new_project(tmp_path)
    edit = ToolCall(
        id="tc_edit",
        name="edit_item",
        arguments={
            "target": "place",
            "id": "cerro-rico",
            "patch": {"description": "Vista del cerro"},
        },
    )
    add_qa = ToolCall(
        id="tc_qa",
        name="add_qa",
        arguments={"question": "¿Horario?", "answer": "9 a 18h"},
    )
    # El usuario ya confirmó en el turno previo; ahora el modelo escribe.
    mock = MockProvider([("tools", [edit, add_qa]), ("text", "hecho")])

    recorder = _Recorder()  # solo verifica el despacho
    restore = _patch_run_intake_tool(recorder)
    try:
        ChatAgent(project, provider=mock).run_turn(ChatRequest(mensaje="sí, confirmo"))
    finally:
        restore()

    assert [name for name, _ in recorder.calls] == ["edit_item", "add_qa"]
    for _name, args in recorder.calls:
        assert args.get("project") == str(project)


def test_example_no_write_tools_leaves_contract_unchanged(tmp_path):
    """Un mock sin tool-calls de escritura deja el contrato byte a byte idéntico.

    Requirements: 2.5, 3.4, 8.4
    """
    project = _new_project(tmp_path)
    mock = MockProvider([("text", "te propongo esto; ¿confirmás?")])

    before = _snapshot_contract(project)
    ChatAgent(project, provider=mock).run_turn(
        ChatRequest(
            mensaje="acá va info",
            binarios=[IncomingFile(filename="cerro.png", content=b"\x89PNG\r\n")],
        )
    )
    after = _snapshot_contract(project)

    assert before == after


def test_example_no_vision_provider_saves_asset_and_notes_mode(tmp_path):
    """DD-M7: con `supports_vision=False` y una imagen adjunta, la imagen NO se
    adjunta como `Message.images`, el asset igual es guardable (los bytes se
    inyectan en `attach_asset`) y el contexto lleva una nota que nombra
    `PURIQ_LLM_MODE`.

    Requirements: 2.4, 2.5 (degradación accionable, DD-M7)
    """
    project = _new_project(tmp_path)
    content = b"\x89PNG\r\n\x1a\n-datos-"
    attach = ToolCall(
        id="tc_a",
        name="attach_asset",
        arguments={"filename": "cerro.png", "target": "place", "id": "cerro-rico"},
    )
    mock = MockProvider(
        [("tools", [attach]), ("text", "guardada")], supports_vision=False
    )

    recorder = _Recorder()  # verifica que los bytes se inyectan igual
    restore = _patch_run_intake_tool(recorder)
    try:
        ChatAgent(project, provider=mock).run_turn(
            ChatRequest(
                mensaje="subo esta foto",
                binarios=[IncomingFile(filename="cerro.png", content=content)],
            )
        )
    finally:
        restore()

    # 1) Sin visión, la imagen NO se envía al modelo como Message.images.
    user_msg = _last_user_message(mock.received[0])
    assert not user_msg.images

    # 2) El contexto lleva una nota accionable que nombra PURIQ_LLM_MODE.
    assert "PURIQ_LLM_MODE" in (user_msg.content or "")

    # 3) El asset igual es guardable: los bytes se inyectan en attach_asset.
    assert len(recorder.calls) == 1
    name, args = recorder.calls[0]
    assert name == "attach_asset"
    assert args.get("project") == str(project)
    assert args.get("content_base64") == base64.b64encode(content).decode("ascii")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
