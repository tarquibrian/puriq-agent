"""Pruebas del Chat_Agent de la superficie web (spec conversational-web-chat).

Implementa las propiedades de correctitud del nivel de la **superficie web**
(bucle por turno, inyección de `project`, cota de rondas, tool inexistente,
contexto del turno, archivos como texto y derivación de faltantes del contrato)
y los ejemplos de la tarea 5.9, en un único archivo.

Cada prueba:
  - opera sobre un **proyecto temporal** aislado por ejemplo (subdirectorio único
    bajo `tmp_path`) con los tres JSON del contrato,
  - inyecta un **proveedor mock** programable en `ChatAgent(provider=...)` que
    emite tool-calls y/o texto de forma determinista y sin coste externo,
  - las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones
    y llevan el comentario `# Feature: conversational-web-chat, Property {N}: ...`.

El comportamiento del núcleo de intake (validación, atomicidad, `missing`, etc.)
ya está cubierto por las propiedades del Hito 1; aquí solo se prueban las
invariantes que aporta el Chat_Agent como superficie.
"""
from __future__ import annotations

import json
import string
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import puriq.intake.agent as agent_mod
from puriq.intake.agent import (
    _LIMIT_MESSAGE,
    ChatAgent,
    ChatRequest,
)
from puriq.intake.prompt import build_system_prompt
from puriq.intake.session import save_session
from puriq.intake.tools import (
    INTAKE_TOOL_NAMES,
    INTAKE_TOOL_SPECS,
    get_state,
)
from puriq.tools.generate_content import ChatResult, Message, ToolCall
from puriq.wizard import contracts
from puriq.wizard.modules import MODULE_CATALOG

# --- Claves y archivos del contrato ------------------------------------------
_TOURISM = "tourism-data"
_CONFIG = "site-config"
_THEME = "theme-tokens"
_CONTRACT_FILES = (contracts.DATA, contracts.CONFIG, contracts.THEME)

#: Configuración común de PBT: >=100 iteraciones, sin deadline (E/S en tmp) y se
#: suprime el health-check de fixture de función (usamos `tmp_path` como raíz y
#: creamos un subdirectorio único por ejemplo, así que el aislamiento es real).
pbt = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- Proveedor mock programable ----------------------------------------------
class MockProvider:
    """Proveedor de LLM determinista para el bucle del Chat_Agent.

    Se programa con un `script`: una lista de pasos. Cada paso es una tupla:
      - ``("tools", [ToolCall, ...])`` -> el modelo pide esas herramientas.
      - ``("text", "respuesta")``      -> el modelo responde texto final.

    Captura en `received` los `messages` de cada llamada y en `tools_received`
    la lista de tools recibida, para inspeccionar el contexto del turno. Si el
    script se agota, por defecto termina el turno con un texto; con
    ``repeat_last=True`` repite el último paso (útil para forzar tool-calls
    indefinidas y ejercitar la cota de rondas).
    """

    def __init__(self, script, *, repeat_last: bool = False):
        self.script = list(script)
        self.repeat_last = repeat_last
        self.received: list[list] = []
        self.tools_received: list = []
        self._i = 0

    def complete(self, prompt: str) -> str:  # pragma: no cover - no usado aquí
        raise NotImplementedError("MockProvider no implementa complete()")

    def complete_chat(self, messages, tools=None) -> ChatResult:
        # Capturar una copia superficial de la lista de mensajes de esta llamada
        # (los objetos Message son inmutables en su `content`, que es lo que se
        # inspecciona en las pruebas).
        self.received.append(list(messages))
        self.tools_received.append(tools)

        if self._i < len(self.script):
            kind, payload = self.script[self._i]
            self._i += 1
        elif self.repeat_last and self.script:
            kind, payload = self.script[-1]
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
def _init_project(project: Path) -> Path:
    """Escribe un contrato base válido (los 3 JSON) en `project`.

    Identidad de sitio válida y sin lugares; `site-config` sin módulos; colores
    marcadores por defecto en `theme-tokens` (mismo punto de partida que las
    pruebas del Hito 1).
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


def _tool_messages(messages) -> list:
    """Devuelve los mensajes de rol `tool` (con `tool_result`) de una lista."""
    return [
        m
        for m in messages
        if getattr(m, "role", None) == "tool" and getattr(m, "tool_result", None)
    ]


def _is_error_response(result: object) -> bool:
    """Indica si `result` es una respuesta de error accionable del núcleo.

    `wizard_error_response` devuelve `{causa, accion}` (errores generales) o
    `{documento, campo, sugerencia}` (errores de esquema).
    """
    return isinstance(result, dict) and (
        "causa" in result or "sugerencia" in result or "campo" in result
    )


class _Recorder:
    """Reemplazo instrumentado de `run_intake_tool` que registra los despachos.

    Registra `(name, args)` de cada despacho. Si `delegate` es una función, la
    invoca (para ejecutar el núcleo real); si no, devuelve un resultado benigno
    sin efectos, útil cuando solo interesa inspeccionar los argumentos.
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
_name_st = st.text(alphabet=_ascii_lower, min_size=1, max_size=8)
_valid_lat = st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False)
_valid_lng = st.floats(
    min_value=-180, max_value=180, allow_nan=False, allow_infinity=False
)
_text_st = st.text(alphabet=string.ascii_letters + " ", min_size=1, max_size=20)


@st.composite
def _valid_toolcall(draw) -> ToolCall:
    """Genera una `ToolCall` de una operación de intake VÁLIDA (sin `project`).

    El Chat_Agent inyecta `project` al despachar, de modo que las tool-calls que
    emite el modelo no lo incluyen (DD-2). Se cubren tanto escrituras como una
    lectura (`get_state`).
    """
    kind = draw(
        st.sampled_from(
            ["add_place", "add_event", "configure_modules", "set_brand", "get_state"]
        )
    )
    if kind == "add_place":
        args = {
            "name": draw(_name_st),
            "category": draw(_name_st),
            "lat": draw(_valid_lat),
            "lng": draw(_valid_lng),
        }
    elif kind == "add_event":
        args = {"name": draw(_name_st), "start_date": "2024-01-01"}
    elif kind == "configure_modules":
        keys = draw(
            st.lists(
                st.sampled_from(list(MODULE_CATALOG)),
                min_size=1,
                max_size=len(MODULE_CATALOG),
                unique=True,
            )
        )
        args = {"selection": [{"key": k} for k in keys]}
    elif kind == "set_brand":
        args = {
            "colors": {
                "primary": "#123456",
                "background": "#abcdef",
                "text": "#0f0f0f",
            }
        }
    else:  # get_state
        args = {}
    return ToolCall(id="", name=kind, arguments=args)


@st.composite
def _arbitrary_call(draw) -> ToolCall:
    """Genera una `ToolCall` con nombre y argumentos arbitrarios.

    A veces inyecta un `project` falso en los argumentos, para comprobar que el
    Chat_Agent lo sobreescribe con el Project_Root real (Req 1.8).
    """
    name = draw(st.text(min_size=1, max_size=10))
    args = draw(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=6),
            values=st.one_of(st.text(max_size=10), st.integers(), st.booleans()),
            max_size=4,
        )
    )
    if draw(st.booleans()):
        # Un `project` distinto/falso provisto por el modelo debe ser ignorado.
        args["project"] = draw(st.text(max_size=15))
    return ToolCall(id="tc" + draw(st.integers(min_value=0, max_value=9999).map(str)),
                    name=name, arguments=args)


@st.composite
def _history(draw) -> list[dict]:
    """Genera un historial previo arbitrario de mensajes user/assistant."""
    n = draw(st.integers(min_value=0, max_value=4))
    msgs: list[dict] = []
    for _ in range(n):
        role = draw(st.sampled_from(["user", "assistant"]))
        msgs.append({"role": role, "content": draw(st.text(max_size=20))})
    return msgs


# =============================================================================
# Property 1 (task 5.2)
# =============================================================================
# Feature: conversational-web-chat, Property 1: El turno despacha por el núcleo y devuelve el estado de get_state
@pbt
@given(calls=st.lists(_valid_toolcall(), min_size=1, max_size=4), final_text=_text_st)
def test_p1_turn_dispatches_via_core_and_returns_get_state(tmp_path, calls, final_text):
    """Cada tool-call se despacha por run_intake_tool, se anexa su ToolResult antes
    de la siguiente llamada, el turno termina con el texto y estado == get_state.

    Validates: Requirements 1.3, 1.4, 1.5, 3.5, 5.1, 5.5
    """
    project = _new_project(tmp_path)
    # Ids únicos por claridad (no afecta la lógica del despacho).
    calls = [ToolCall(id=f"tc_{i}", name=c.name, arguments=c.arguments)
             for i, c in enumerate(calls)]

    # El mock emite cada tool-call en su propia ronda y cierra con texto.
    script = [("tools", [c]) for c in calls] + [("text", final_text)]
    mock = MockProvider(script)

    recorder = _Recorder(delegate=agent_mod.run_intake_tool)
    restore = _patch_run_intake_tool(recorder)
    try:
        response = ChatAgent(project, provider=mock).run_turn(
            ChatRequest(mensaje="hola", archivos=[])
        )
    finally:
        restore()

    # Se despachó cada tool-call por el núcleo, en orden.
    assert [name for name, _ in recorder.calls] == [c.name for c in calls]

    # Antes de la llamada i-ésima (0-based) hay i ToolResult en la conversación
    # (uno por cada ronda de tool-call previa), lo que prueba que el resultado se
    # anexa antes de la siguiente llamada al modelo.
    for i, messages in enumerate(mock.received):
        assert len(_tool_messages(messages)) == i

    # El turno finaliza con el texto del asistente...
    assert response.respuesta == final_text
    # ...y devuelve el estado de get_state tras las tool-calls.
    assert response.estado == get_state(project)


# =============================================================================
# Property 2 (task 5.3)
# =============================================================================
# Feature: conversational-web-chat, Property 2: Toda Tool_Call se despacha con el Project_Root inyectado
@pbt
@given(calls=st.lists(_arbitrary_call(), min_size=1, max_size=4))
def test_p2_project_root_is_injected(tmp_path, calls):
    """Los argumentos entregados a run_intake_tool contienen project == Project_Root,
    con args arbitrarios (incluso sin project o con un project distinto).

    Validates: Requirements 1.8
    """
    project = _new_project(tmp_path)
    # Una sola ronda con todas las tool-calls, luego texto para cerrar.
    mock = MockProvider([("tools", list(calls)), ("text", "listo")])

    recorder = _Recorder()  # no delega: solo inspecciona argumentos
    restore = _patch_run_intake_tool(recorder)
    try:
        ChatAgent(project, provider=mock).run_turn(ChatRequest(mensaje="x"))
    finally:
        restore()

    assert len(recorder.calls) == len(calls)
    for _name, args in recorder.calls:
        assert args.get("project") == str(project)


# =============================================================================
# Property 3 (task 5.4)
# =============================================================================
# Feature: conversational-web-chat, Property 3: El número de rondas de Tool_Call por turno está acotado
@pbt
@given(max_rounds=st.integers(min_value=1, max_value=6))
def test_p3_tool_rounds_are_bounded(tmp_path, max_rounds):
    """Con un provider que siempre emite tool-calls, el agente ejecuta a lo sumo
    max_tool_rounds rondas y cierra con el mensaje de límite y el estado vigente.

    Validates: Requirements 1.6, 1.7
    """
    project = _new_project(tmp_path)
    # get_state es de solo lectura: mantiene el contrato intacto entre rondas.
    loop_call = ToolCall(id="tc_loop", name="get_state", arguments={})
    mock = MockProvider([("tools", [loop_call])], repeat_last=True)

    recorder = _Recorder(delegate=agent_mod.run_intake_tool)
    restore = _patch_run_intake_tool(recorder)
    try:
        response = ChatAgent(
            project, provider=mock, max_tool_rounds=max_rounds
        ).run_turn(ChatRequest(mensaje="dale"))
    finally:
        restore()

    # A lo sumo (aquí exactamente) max_rounds llamadas y despachos.
    assert len(mock.received) == max_rounds
    assert len(recorder.calls) == max_rounds
    # Cierra con el mensaje de límite y el estado vigente del contrato.
    assert response.respuesta == _LIMIT_MESSAGE
    assert response.estado == get_state(project)


# =============================================================================
# Property 4 (task 5.5)
# =============================================================================
_unknown_name = st.text(min_size=1, max_size=12).filter(
    lambda s: s not in INTAKE_TOOL_NAMES
)


# Feature: conversational-web-chat, Property 4: Una Tool_Call con nombre inexistente no altera el contrato
@pbt
@given(name=_unknown_name)
def test_p4_unknown_tool_does_not_alter_contract(tmp_path, name):
    """Un nombre fuera de INTAKE_TOOL_NAMES produce un ToolResult de error y deja
    los 3 archivos del contrato byte a byte idénticos.

    Validates: Requirements 5.3
    """
    project = _new_project(tmp_path)
    unknown = ToolCall(id="tc_x", name=name, arguments={"foo": "bar"})
    mock = MockProvider([("tools", [unknown]), ("text", "ok")])

    before = _snapshot_contract(project)
    ChatAgent(project, provider=mock).run_turn(ChatRequest(mensaje="probando"))
    after = _snapshot_contract(project)

    # Contrato intacto byte a byte.
    assert before == after

    # El ToolResult entregado al modelo es un error accionable. Se inspecciona en
    # los mensajes de la segunda llamada (que ya incluyen el resultado de la tool).
    tool_msgs = _tool_messages(mock.received[1])
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0].tool_result.content)
    assert _is_error_response(payload)


# =============================================================================
# Property 6 (task 5.6)
# =============================================================================
_MARK_USER = "MARCADOR_USUARIO_PREVIO_XYZ"
_MARK_ASSISTANT = "MARCADOR_ASISTENTE_PREVIO_XYZ"


# Feature: conversational-web-chat, Property 6: El contexto del turno contiene prompt, estado e historial
@pbt
@given(extra=_history(), phase=st.one_of(st.none(), st.sampled_from(["1", "3", "5"])))
def test_p6_turn_context_has_prompt_state_and_history(tmp_path, extra, phase):
    """Los mensajes pasados a complete_chat incluyen el system prompt con el
    Contract_State vigente y el historial previo de la conversación.

    Validates: Requirements 1.1
    """
    project = _new_project(tmp_path)
    seed = [
        {"role": "user", "content": _MARK_USER},
        {"role": "assistant", "content": _MARK_ASSISTANT},
        *extra,
    ]
    save_session(project, seed, phase)

    mock = MockProvider([("text", "respuesta")])
    ChatAgent(project, provider=mock).run_turn(ChatRequest(mensaje="mensaje nuevo"))

    messages = mock.received[0]

    # 1) Primer mensaje: el system prompt con el Contract_State vigente inyectado.
    assert messages[0].role == "system"
    assert messages[0].content == build_system_prompt(get_state(project))

    # 2) El historial previo está presente en el contexto.
    contents = [m.content for m in messages]
    assert _MARK_USER in contents
    assert _MARK_ASSISTANT in contents

    # 3) El mensaje del usuario de este turno también está presente.
    assert any(
        m.role == "user" and m.content and "mensaje nuevo" in m.content
        for m in messages
    )


# =============================================================================
# Property 10 (task 5.7)
# =============================================================================
_ref_st = st.builds(
    lambda stem, ext: f"assets/{stem}.{ext}",
    st.text(alphabet=_ascii_lower + "-", min_size=1, max_size=12),
    st.sampled_from(["jpg", "png", "webp", "pdf"]),
)


# Feature: conversational-web-chat, Property 10: Los archivos se tratan como referencias textuales, sin binarios
@pbt
@given(archivos=st.lists(_ref_st, min_size=1, max_size=5))
def test_p10_files_are_textual_references(tmp_path, archivos):
    """Cada referencia aparece como texto en el mensaje de usuario y ningún mensaje
    transporta bytes (todo `content` es str o None).

    Validates: Requirements 8.1, 8.2, 8.3
    """
    project = _new_project(tmp_path)
    mock = MockProvider([("text", "ok")])
    ChatAgent(project, provider=mock).run_turn(
        ChatRequest(mensaje="tengo fotos", archivos=list(archivos))
    )

    messages = mock.received[0]
    # El mensaje de usuario del turno es el último de rol user.
    user_msgs = [m for m in messages if m.role == "user"]
    assert user_msgs, "debe existir al menos un mensaje de usuario"
    user_content = user_msgs[-1].content or ""

    # Cada referencia aparece como texto.
    for ref in archivos:
        assert ref in user_content

    # Ningún mensaje transporta bytes: todo content es str o None.
    for m in messages:
        assert m.content is None or isinstance(m.content, str)


# =============================================================================
# Property 14 (task 5.8)
# =============================================================================
# Feature: conversational-web-chat, Property 14: Los Faltantes se derivan del contrato, no del historial
@pbt
@given(
    history=_history(),
    add_place=st.booleans(),
    add_modules=st.booleans(),
    set_brand=st.booleans(),
)
def test_p14_missing_derived_from_contract_not_history(
    tmp_path, history, add_place, add_modules, set_brand
):
    """El `missing` del estado devuelto == missing de get_state(project), sin
    importar el historial previo (que puede sugerir otra cosa).

    Validates: Requirements 10.3
    """
    from puriq.intake import tools as intake_tools

    project = _new_project(tmp_path)
    # Variar el contrato en disco para variar `missing`.
    if add_place:
        intake_tools.add_place(
            project, name="cerro", category="montana", lat=-19.6, lng=-65.7
        )
    if add_modules:
        intake_tools.configure_modules(project, selection=[{"key": list(MODULE_CATALOG)[0]}])
    if set_brand:
        intake_tools.set_brand(
            project,
            colors={"primary": "#123456", "background": "#abcdef", "text": "#0f0f0f"},
        )

    # Un historial que podría "sugerir" que ya hay lugares/marca cargados.
    misleading = [
        {"role": "assistant", "content": "Ya cargué todos los lugares y la marca."},
        *history,
    ]
    save_session(project, misleading, "9")

    # El turno no ejecuta tool-calls (texto inmediato): el contrato no cambia.
    mock = MockProvider([("text", "listo")])
    response = ChatAgent(project, provider=mock).run_turn(ChatRequest(mensaje="hola"))

    assert response.estado["missing"] == get_state(project)["missing"]


# =============================================================================
# Ejemplos (task 5.9)
# =============================================================================
def test_example_agent_exposes_tools_by_intake_tool_names(tmp_path):
    """El Chat_Agent expone al LLM las tools de INTAKE_TOOL_SPECS, identificadas
    por INTAKE_TOOL_NAMES.

    Requirements: 5.2
    """
    project = _new_project(tmp_path)
    mock = MockProvider([("text", "hola")])
    ChatAgent(project, provider=mock).run_turn(ChatRequest(mensaje="hola"))

    tools_passed = mock.tools_received[0]
    # Se pasan exactamente los specs del núcleo.
    assert tools_passed is INTAKE_TOOL_SPECS
    assert [spec["name"] for spec in tools_passed] == INTAKE_TOOL_NAMES


def test_example_attach_asset_dispatches_via_run_intake_tool(tmp_path):
    """Un mock que emite `attach_asset` hace que el agente despache por
    run_intake_tool, con el Project_Root inyectado.

    Requirements: 8.4
    """
    project = _new_project(tmp_path)
    attach = ToolCall(
        id="tc_a",
        name="attach_asset",
        arguments={"filename": "foto.png", "target": "place", "id": "cerro-rico"},
    )
    mock = MockProvider([("tools", [attach]), ("text", "asociada")])

    recorder = _Recorder()  # no delega: solo verifica el despacho
    restore = _patch_run_intake_tool(recorder)
    try:
        ChatAgent(project, provider=mock).run_turn(ChatRequest(mensaje="acá va la foto"))
    finally:
        restore()

    assert len(recorder.calls) == 1
    name, args = recorder.calls[0]
    assert name == "attach_asset"
    assert args.get("project") == str(project)
    assert args.get("filename") == "foto.png"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
