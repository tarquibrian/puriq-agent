"""Pruebas smoke del registro MCP de las intake tools (spec conversational-intake-mcp).

Cubre los criterios de aceptación del Requisito 13 (Pieza 2, exposición por MCP)
sobre `agent/puriq/mcp/server.py` y `agent/puriq/intake/tools.py`:

  - Req 13.1: `list_tools` registra las 12 intake tools (`set_site`,
    `configure_modules`, `add_place`, `add_event`, `edit_item`, `remove_item`,
    `set_brand`, `configure_landing`, `add_qa`, `attach_asset`, `get_state`,
    `build`).
  - Req 13.6: conserva registradas las 11 tools de edición y de pipeline ya
    existentes (total 25 con las del intake).
  - Req 13.2: cada intake spec declara un `inputSchema` de tipo objeto con la
    propiedad `project` requerida, conforme a la firma de la tool subyacente.
  - Req 13.4: las descripciones de las intake tools incluyen el guion por fases.
  - Req 13.5: el recurso MCP `intake://guion` se anuncia en `list_resources` y
    `read_resource("intake://guion")` devuelve `INTAKE_GUION` (text/markdown).
  - Req 13.3: una intake tool invocada por el servidor se enruta por
    `run_intake_tool` (el resultado de `call_tool` coincide con la delegación).

El SDK `mcp` NO está instalado en el entorno. Como `server.py` lo importa de
forma diferida dentro de `build_server()`, las pruebas que ejercitan el servidor
instalan un STUB mínimo del SDK en `sys.modules` (mismo enfoque que
`test_mcp_server.py`, con soporte de list_resources/read_resource/Resource/
ReadResourceContents añadido en la tarea 11.2). Las pruebas de datos puros
(inspección de specs/descripciones/inputSchema) no necesitan el stub y trabajan
directamente sobre `puriq.mcp.server` y `puriq.intake.tools`.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

# Asegurar que el paquete `puriq` sea importable al correr pytest desde cualquier dir.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.intake import tools as intake_tools  # noqa: E402
from puriq.mcp import server  # noqa: E402

#: Las 12 intake tools que deben quedar registradas (Req 13.1).
EXPECTED_INTAKE_TOOLS = {
    "set_site",
    "configure_modules",
    "add_place",
    "add_event",
    "edit_item",
    "remove_item",
    "set_brand",
    "configure_landing",
    "add_qa",
    "attach_asset",
    "get_state",
    "build",
    # Añadida en la Fase 3 (multimodal-ingest, tarea 6.1, DD-M6): expuesta por MCP.
    "extract_pdf",
    # El guion tambien como tool: no todo cliente MCP lee recursos (Kiro, p. ej.),
    # asi que `intake://guion` por si solo no alcanza para conducir la charla.
    "get_guion",
}

#: Las 11 tools de pipeline y edición que ya existían antes del intake (Req 13.6).
EXPECTED_EXISTING_TOOLS = {
    "scan_resources",
    "import_open_data",
    "generate_content",
    "build_site",
    "deploy",
    "manage_articles",
    "query_content",
    "edit_content",
    "delete_content",
    "bulk_update",
    "analyze_seo",
}

#: Total esperado de tools anunciadas por el servidor (11 + 14 = 25).
#: La Fase 3 (multimodal-ingest) suma `extract_pdf`, y `get_guion` expone el guion
#: como tool para los clientes MCP que no leen recursos.
EXPECTED_TOTAL_TOOLS = 25


# --- Stub del SDK `mcp` ------------------------------------------------------


def _install_mcp_stub(monkeypatch: pytest.MonkeyPatch):
    """Instala un stub mínimo de `mcp` en `sys.modules` (mismo enfoque que
    test_mcp_server.py, con soporte de recursos añadido en 11.2).

    Reproduce sólo la API que `build_server()` consume: un `Server` con los
    decoradores `list_tools()`/`call_tool()`/`list_resources()`/`read_resource()`
    que capturan sus handlers, y los tipos `Tool`, `TextContent`,
    `CallToolResult`, `Resource` y `ReadResourceContents` como contenedores.
    """

    class _Tool:
        def __init__(self, name, description, inputSchema):  # noqa: N803
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    class _TextContent:
        def __init__(self, type, text):  # noqa: A002 - firma del SDK real
            self.type = type
            self.text = text

    class _CallToolResult:
        def __init__(self, content, isError=False):  # noqa: N803 - firma del SDK
            self.content = content
            self.isError = isError

    class _Resource:
        def __init__(self, uri, name, description=None, mimeType=None):  # noqa: N803
            self.uri = uri
            self.name = name
            self.description = description
            self.mimeType = mimeType

    class _ReadResourceContents:
        def __init__(self, content, mime_type=None):
            self.content = content
            self.mime_type = mime_type

    class _Server:
        def __init__(self, name):
            self.name = name
            self.list_tools_handler = None
            self.call_tool_handler = None
            self.list_resources_handler = None
            self.read_resource_handler = None

        def list_tools(self):
            def _deco(fn):
                self.list_tools_handler = fn
                return fn

            return _deco

        def call_tool(self):
            def _deco(fn):
                self.call_tool_handler = fn
                return fn

            return _deco

        def list_resources(self):
            def _deco(fn):
                self.list_resources_handler = fn
                return fn

            return _deco

        def read_resource(self):
            def _deco(fn):
                self.read_resource_handler = fn
                return fn

            return _deco

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    lowlevel_mod = types.ModuleType("mcp.server.lowlevel")
    helper_types_mod = types.ModuleType("mcp.server.lowlevel.helper_types")
    types_mod = types.ModuleType("mcp.types")

    lowlevel_mod.Server = _Server
    helper_types_mod.ReadResourceContents = _ReadResourceContents
    types_mod.Tool = _Tool
    types_mod.TextContent = _TextContent
    types_mod.CallToolResult = _CallToolResult
    types_mod.Resource = _Resource

    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    monkeypatch.setitem(sys.modules, "mcp.server.lowlevel", lowlevel_mod)
    monkeypatch.setitem(
        sys.modules, "mcp.server.lowlevel.helper_types", helper_types_mod
    )
    monkeypatch.setitem(sys.modules, "mcp.types", types_mod)

    return _Server


# --- Datos puros: INTAKE_TOOL_SPECS / TOOL_SPECS (Req 13.1, 13.6) ------------


def test_intake_tool_names_are_the_expected_set():
    """`INTAKE_TOOL_NAMES` enumera exactamente las intake tools (Req 13.1)."""
    assert set(intake_tools.INTAKE_TOOL_NAMES) == EXPECTED_INTAKE_TOOLS
    # Sin duplicados: 12 originales + extract_pdf + get_guion.
    assert len(intake_tools.INTAKE_TOOL_NAMES) == len(EXPECTED_INTAKE_TOOLS) == 14


def test_tool_specs_include_intake_and_preserve_existing():
    """`TOOL_SPECS` suma las 12 intake tools y conserva las 11 existentes (Req 13.1, 13.6)."""
    names = [spec["name"] for spec in server.TOOL_SPECS]
    name_set = set(names)

    # Las 12 intake tools están registradas (Req 13.1).
    assert EXPECTED_INTAKE_TOOLS <= name_set
    # Las 11 tools de pipeline y edición siguen registradas (Req 13.6).
    assert EXPECTED_EXISTING_TOOLS <= name_set
    # Total exacto y sin duplicados.
    assert len(names) == len(name_set) == EXPECTED_TOTAL_TOOLS


def test_tool_specs_compose_existing_then_intake():
    """El registro es aditivo: primero las existentes, luego las intake (DD-1, Req 13.6)."""
    names = [spec["name"] for spec in server.TOOL_SPECS]
    # Las últimas entradas son, en orden, las intake tools (13 con extract_pdf).
    assert names[-len(intake_tools.INTAKE_TOOL_NAMES):] == list(
        intake_tools.INTAKE_TOOL_NAMES
    )
    # Y las intake no pisan a las existentes: las 11 primeras son las existentes.
    assert set(names[:11]) == EXPECTED_EXISTING_TOOLS


def test_intake_handlers_are_wired_and_callable():
    """Cada intake tool tiene su handler registrado en `_HANDLERS` (Req 13.3)."""
    for name in EXPECTED_INTAKE_TOOLS:
        assert name in server._HANDLERS
        assert callable(server._HANDLERS[name])


# --- Req 13.2: inputSchema de objeto con `project` requerido -----------------


#: Tools que NO operan sobre un proyecto y por eso no declaran `project`.
#: `get_guion` sirve el guion del intake, que es el mismo para cualquier proyecto
#: (de hecho se llama antes de saber sobre cual se va a trabajar).
_SIN_PROYECTO = {"get_guion"}


def test_every_intake_spec_declares_object_schema_with_project():
    """Cada intake spec es un objeto JSON Schema con `project` requerido (Req 13.2)."""
    for spec in intake_tools.INTAKE_TOOL_SPECS:
        schema = spec["inputSchema"]
        assert schema["type"] == "object", spec["name"]
        if spec["name"] not in _SIN_PROYECTO:
            # `project` es una propiedad declarada...
            assert "project" in schema["properties"], spec["name"]
            assert schema["properties"]["project"]["type"] == "string", spec["name"]
            # ...y es obligatoria en toda intake tool que toque un proyecto.
            assert "project" in schema.get("required", []), spec["name"]
        # `required` es subconjunto de las properties declaradas.
        assert set(schema.get("required", [])) <= set(schema["properties"]), spec[
            "name"
        ]
        # Superficie cerrada: no admite propiedades arbitrarias.
        assert schema.get("additionalProperties") is False, spec["name"]


def test_required_fields_match_known_signatures():
    """Los `required` de algunas intake tools reflejan su firma (Req 13.2)."""
    by_name = {s["name"]: s for s in intake_tools.INTAKE_TOOL_SPECS}
    # set_site requiere identidad + centro del mapa.
    assert set(by_name["set_site"]["inputSchema"]["required"]) == {
        "project",
        "name",
        "region",
        "center",
    }
    # add_place: nombre y categoría (coords opcionales -> borrador).
    assert set(by_name["add_place"]["inputSchema"]["required"]) == {
        "project",
        "name",
        "category",
    }
    # add_event: nombre y fecha de inicio.
    assert set(by_name["add_event"]["inputSchema"]["required"]) == {
        "project",
        "name",
        "start_date",
    }
    # get_state y build: solo el proyecto.
    assert by_name["get_state"]["inputSchema"]["required"] == ["project"]
    assert by_name["build"]["inputSchema"]["required"] == ["project"]


# --- Req 13.4: las descripciones incluyen el guion por fases ------------------


def test_intake_descriptions_are_non_empty():
    """Toda intake tool declara una descripción no vacía (Req 13.4)."""
    for spec in intake_tools.INTAKE_TOOL_SPECS:
        assert isinstance(spec["description"], str)
        assert spec["description"].strip(), spec["name"]


def test_intake_descriptions_reference_the_phased_script():
    """Las descripciones referencian el guion por fases/pasos del intake (Req 13.4).

    Se comprueba que globalmente las descripciones mencionan las 'fases' del
    guion (al menos las que tienen una fase asignada la nombran) y que ninguna
    descripción está vacía, de modo que el LLM del cliente disponga del guion
    embebido en la superficie de las tools.
    """
    descriptions = {
        spec["name"]: spec["description"].lower()
        for spec in intake_tools.INTAKE_TOOL_SPECS
    }

    # Las tools de cada fase nombran su fase en la descripción (Req 13.4).
    fase_por_tool = {
        "set_site": "fase 1",
        "configure_modules": "fase 2",
        "add_place": "fase 3",
        "add_event": "fase 4",
        "set_brand": "fase 5",
        "configure_landing": "fase 6",
        "add_qa": "fase 7",
        "attach_asset": "fase 8",
        "build": "fase 9",
    }
    for tool, fase in fase_por_tool.items():
        assert fase in descriptions[tool], f"{tool} no referencia '{fase}'"

    # Al menos una parte importante del guion (varias fases) está presente.
    con_fase = [d for d in descriptions.values() if "fase" in d]
    assert len(con_fase) >= 9


def test_intake_guion_covers_phases_and_active_file_request():
    """`INTAKE_GUION` describe las fases y la regla de pedir archivos (Req 13.4, 13.5)."""
    guion = intake_tools.INTAKE_GUION
    assert isinstance(guion, str) and guion.strip()
    low = guion.lower()
    # Menciona las fases del guion.
    assert "fase 1" in low
    assert "fase 9" in low
    # Regla transversal de pedir archivos activamente.
    assert "pedí" in guion or "ped" in low


# --- Req 13.1, 13.2: list_tools del servidor (con stub del SDK) --------------


def test_build_server_list_tools_announces_all_tools(monkeypatch):
    """`list_tools` anuncia todas las tools con `inputSchema` de objeto (Req 13.1, 13.2, 13.6)."""
    _install_mcp_stub(monkeypatch)

    srv = server.build_server()
    assert srv.name == server.SERVER_NAME
    assert srv.list_tools_handler is not None

    tools = asyncio.run(srv.list_tools_handler())
    names = {t.name for t in tools}

    assert len(tools) == EXPECTED_TOTAL_TOOLS
    assert EXPECTED_INTAKE_TOOLS <= names
    assert EXPECTED_EXISTING_TOOLS <= names

    # Cada intake tool anunciada expone un inputSchema de objeto; las que operan
    # sobre un proyecto declaran ademas `project` (ver `_SIN_PROYECTO`).
    intake_by_name = {t.name: t for t in tools if t.name in EXPECTED_INTAKE_TOOLS}
    assert set(intake_by_name) == EXPECTED_INTAKE_TOOLS
    for name, tool in intake_by_name.items():
        assert isinstance(tool.inputSchema, dict), name
        assert tool.inputSchema.get("type") == "object", name
        if name not in _SIN_PROYECTO:
            assert "project" in tool.inputSchema.get("properties", {}), name
        assert tool.description and tool.description.strip(), name


# --- Req 13.5: recurso MCP `intake://guion` ----------------------------------


def test_build_server_lists_intake_guion_resource(monkeypatch):
    """`list_resources` anuncia el recurso del guion en text/markdown (Req 13.5)."""
    _install_mcp_stub(monkeypatch)

    srv = server.build_server()
    assert srv.list_resources_handler is not None

    resources = asyncio.run(srv.list_resources_handler())
    uris = {str(r.uri) for r in resources}
    assert server.INTAKE_RESOURCE_URI in uris

    recurso = next(r for r in resources if str(r.uri) == server.INTAKE_RESOURCE_URI)
    assert recurso.mimeType == "text/markdown"
    assert recurso.name  # nombre legible no vacío


def test_read_resource_returns_intake_guion(monkeypatch):
    """`read_resource('intake://guion')` devuelve `INTAKE_GUION` en markdown (Req 13.5)."""
    _install_mcp_stub(monkeypatch)

    srv = server.build_server()
    assert srv.read_resource_handler is not None

    contents = asyncio.run(srv.read_resource_handler(server.INTAKE_RESOURCE_URI))
    assert isinstance(contents, list) and len(contents) == 1
    assert contents[0].content == intake_tools.INTAKE_GUION
    assert contents[0].mime_type == "text/markdown"


def test_read_resource_rejects_unknown_uri(monkeypatch):
    """Una URI de recurso desconocida se rechaza (Req 13.5)."""
    _install_mcp_stub(monkeypatch)

    srv = server.build_server()
    with pytest.raises(ValueError):
        asyncio.run(srv.read_resource_handler("intake://desconocido"))


# --- Req 13.3: una intake tool invocada se enruta por run_intake_tool --------


def test_call_tool_routes_intake_tool_via_run_intake_tool(tmp_path, monkeypatch):
    """`call_tool` de una intake tool produce el mismo resultado que `run_intake_tool` (Req 13.3).

    Se usa `get_state` (solo lectura, sin efectos) sobre un proyecto vacío: el
    resultado del ruteo del servidor debe coincidir, ya serializado, con el de la
    delegación directa a `run_intake_tool`, confirmando que el servidor enruta las
    intake tools por esa vía compartida.
    """
    _install_mcp_stub(monkeypatch)

    srv = server.build_server()
    args = {"project": str(tmp_path)}

    # Resultado por la vía del servidor MCP.
    result = asyncio.run(srv.call_tool_handler("get_state", args))
    assert isinstance(result, list) and len(result) == 1
    assert result[0].type == "text"

    # Resultado por la delegación directa, serializado igual que en el servidor.
    esperado = server._serialize(intake_tools.run_intake_tool("get_state", args))

    assert result[0].text == esperado


def test_call_tool_intake_delegates_to_run_intake_tool(tmp_path, monkeypatch):
    """El servidor invoca `run_intake_tool` para una intake tool (ruteo, Req 13.3).

    Se sustituye `run_intake_tool` por un doble que registra la llamada, para
    confirmar de forma directa que el `call_tool` del servidor enruta las intake
    tools por esa función compartida (y no por los `_HANDLERS` de pipeline).
    """
    _install_mcp_stub(monkeypatch)

    llamado = {}

    def fake_run_intake_tool(name, arguments):
        llamado["name"] = name
        llamado["arguments"] = arguments
        return {"ruteado": True}

    # El servidor referencia `run_intake_tool` importado en el módulo server.
    monkeypatch.setattr(server, "run_intake_tool", fake_run_intake_tool)

    srv = server.build_server()
    args = {"project": str(tmp_path)}
    result = asyncio.run(srv.call_tool_handler("add_place", args))

    assert llamado["name"] == "add_place"
    assert llamado["arguments"] == args
    assert "ruteado" in result[0].text
