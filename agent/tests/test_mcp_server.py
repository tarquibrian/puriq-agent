"""Pruebas unitarias del servidor MCP `tourism-builder` (spec agent-tools).

Cubre los criterios de aceptación del Requisito 8:

  - Req 8.1: al iniciarse, el MCP_Server registra las tools `scan_resources`,
    `import_open_data`, `generate_content`, `build_site` y `deploy`.
  - Req 8.2: cada handler delega en la misma implementación del core/tools que
    usa el CLI, sin duplicar lógica.
  - Req 8.3: cada tool declara un esquema de entrada acorde a la firma de la
    tool del core.

El SDK `mcp` no está instalado en el entorno de pruebas. Como `server.py` lo
importa de forma diferida dentro de `build_server()`, se instala un stub mínimo
en `sys.modules` (una fake que expone la API mínima que consume `server.py`) para
poder construir el servidor e inspeccionar el registro de tools sin la dependencia
real. Las delegaciones se prueban directamente sobre las funciones `_delegate_*`,
sustituyendo las fronteras core/tools por dobles.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import types
from pathlib import Path

import pytest

# Asegurar que el paquete `puriq` sea importable al correr pytest desde cualquier dir.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.mcp import server  # noqa: E402

#: Las cinco tools que el MCP_Server debe registrar (Req 8.1).
EXPECTED_TOOLS = {
    "scan_resources",
    "import_open_data",
    "generate_content",
    "build_site",
    "deploy",
}


# --- Stub del SDK `mcp` ------------------------------------------------------


def _install_mcp_stub(monkeypatch: pytest.MonkeyPatch):
    """Instala un stub mínimo de `mcp` en `sys.modules` y lo devuelve.

    Reproduce sólo la API que `build_server()` consume: un `Server` con los
    decoradores `list_tools()`/`call_tool()` que capturan sus handlers, y los
    tipos `Tool`, `TextContent` y `CallToolResult` como contenedores simples.
    """

    class _Tool:
        def __init__(self, name, description, inputSchema):
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


# --- Req 8.1: registro de las cinco tools -----------------------------------


def test_tool_specs_register_expected_tools():
    """TOOL_SPECS declara (al menos) las cinco tools esperadas de agent-tools (Req 8.1).

    El registro puede incluir tools adicionales de otros specs (p. ej.
    content-management), por lo que se comprueba inclusión y no igualdad estricta.
    """
    names = {spec["name"] for spec in server.TOOL_SPECS}
    assert EXPECTED_TOOLS <= names


def test_tool_specs_have_no_duplicate_names():
    """No hay nombres de tool duplicados en el registro (Req 8.1)."""
    names = [spec["name"] for spec in server.TOOL_SPECS]
    assert len(names) == len(set(names))


def test_handlers_index_covers_all_tools():
    """El índice nombre->handler cubre (al menos) las cinco tools de agent-tools (Req 8.1).

    Otros specs pueden registrar handlers adicionales; se comprueba inclusión y que
    cada tool declarada en TOOL_SPECS tenga su handler.
    """
    assert EXPECTED_TOOLS <= set(server._HANDLERS)
    assert set(server._HANDLERS) == {spec["name"] for spec in server.TOOL_SPECS}
    assert all(callable(h) for h in server._HANDLERS.values())


def test_build_server_lists_expected_tools_with_schemas(monkeypatch):
    """Al construir el servidor, el handler list_tools declara las cinco tools con
    su inputSchema (Req 8.1, 8.3)."""
    _install_mcp_stub(monkeypatch)

    srv = server.build_server()
    assert srv.name == server.SERVER_NAME
    assert srv.list_tools_handler is not None
    assert srv.call_tool_handler is not None

    tools = asyncio.run(srv.list_tools_handler())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names

    # Cada tool anunciada expone un inputSchema JSON Schema de tipo objeto (Req 8.3).
    for tool in tools:
        assert isinstance(tool.inputSchema, dict)
        assert tool.inputSchema.get("type") == "object"
        assert tool.description  # descripción no vacía


# --- Req 8.3: esquemas de entrada consistentes con la firma del core ---------


def _spec(name: str) -> dict:
    return next(s for s in server.TOOL_SPECS if s["name"] == name)


def _required_params(func) -> set[str]:
    """Parámetros sin valor por defecto de `func` (los obligatorios de su firma)."""
    req = set()
    for pname, param in inspect.signature(func).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is inspect.Parameter.empty:
            req.add(pname)
    return req


def test_every_spec_declares_object_schema():
    """Cada tool declara un inputSchema de tipo objeto con properties (Req 8.3)."""
    for spec in server.TOOL_SPECS:
        schema = spec["inputSchema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)
        # `required` debe ser un subconjunto de las properties declaradas.
        assert set(schema.get("required", [])) <= set(schema["properties"])


def test_scan_resources_schema_matches_signature():
    """El esquema de scan_resources refleja el parámetro obligatorio de run() (Req 8.3)."""
    schema = _spec("scan_resources")["inputSchema"]
    # run(resources_dir) -> obligatorio resources_dir (mapeado como string/ruta).
    assert schema["required"] == ["resources_dir"]
    assert schema["properties"]["resources_dir"]["type"] == "string"
    assert _required_params(server.scan_resources.run) == {"resources_dir"}


def test_import_open_data_schema_matches_signature():
    """El esquema de import_open_data refleja el parámetro obligatorio de merge() (Req 8.3)."""
    schema = _spec("import_open_data")["inputSchema"]
    # merge(data) -> obligatorio data (objeto tourism-data).
    assert schema["required"] == ["data"]
    assert schema["properties"]["data"]["type"] == "object"
    assert _required_params(server.import_open_data.merge) == {"data"}


def test_generate_content_schema_matches_signature():
    """El esquema de generate_content refleja enrich(data, voice=None) (Req 8.3).

    `data` es obligatorio; `voice` es opcional (tiene default) y por tanto no
    figura en `required`.
    """
    schema = _spec("generate_content")["inputSchema"]
    assert schema["required"] == ["data"]
    assert "data" in schema["properties"]
    assert "voice" in schema["properties"]
    assert _required_params(server.generate_content.enrich) == {"data"}


def test_build_site_schema_matches_signature():
    """El esquema de build_site refleja project (obligatorio) y use_llm (opcional) (Req 8.3).

    En el core, build() vive en Puriq(project).build(use_llm=True): `project` es el
    argumento del constructor y `use_llm` es opcional del método.
    """
    schema = _spec("build_site")["inputSchema"]
    assert schema["required"] == ["project"]
    assert schema["properties"]["project"]["type"] == "string"
    assert "use_llm" in schema["properties"]
    assert schema["properties"]["use_llm"]["type"] == "boolean"
    # use_llm es opcional en el método build del core.
    assert "use_llm" not in _required_params(server.Puriq.build)


def test_deploy_schema_matches_signature():
    """El esquema de deploy refleja project (obligatorio) y target (opcional) (Req 8.3).

    El enum de `target` se deriva del registro de adaptadores del core.
    """
    schema = _spec("deploy")["inputSchema"]
    assert schema["required"] == ["project"]
    assert schema["properties"]["project"]["type"] == "string"
    target = schema["properties"]["target"]
    assert target["type"] == "string"
    # El enum coincide con los destinos soportados por el core (Req 8.3).
    assert set(target["enum"]) == set(server.deploy_tool.ADAPTERS)
    # target es opcional en el método deploy del core.
    assert "target" not in _required_params(server.Puriq.deploy)


# --- Req 8.2: los handlers delegan en el core/tools sin duplicar lógica ------


def test_scan_resources_handler_delegates_to_tool(monkeypatch):
    """El handler de scan_resources delega en scan_resources.run con la ruta dada (Req 8.2)."""
    llamado = {}

    def fake_run(resources_dir):
        llamado["resources_dir"] = resources_dir
        return {"ok": "scan"}

    monkeypatch.setattr(server.scan_resources, "run", fake_run)

    result = server._delegate_scan_resources({"resources_dir": "/tmp/recursos"})

    assert result == {"ok": "scan"}
    assert llamado["resources_dir"] == Path("/tmp/recursos")


def test_import_open_data_handler_delegates_to_tool(monkeypatch):
    """El handler de import_open_data delega en import_open_data.merge con el data dado (Req 8.2)."""
    llamado = {}
    entrada = {"site": {"center": {"lat": 1, "lng": 2}}}

    def fake_merge(data):
        llamado["data"] = data
        return {"ok": "import"}

    monkeypatch.setattr(server.import_open_data, "merge", fake_merge)

    result = server._delegate_import_open_data({"data": entrada})

    assert result == {"ok": "import"}
    assert llamado["data"] is entrada


def test_generate_content_handler_delegates_with_voice(monkeypatch):
    """El handler de generate_content delega en enrich(data, voice) (Req 8.2)."""
    llamado = {}
    entrada = {"places": []}
    voz = {"tone": "cercano"}

    def fake_enrich(data, voice=None):
        llamado["data"] = data
        llamado["voice"] = voice
        return {"ok": "generate"}

    monkeypatch.setattr(server.generate_content, "enrich", fake_enrich)

    result = server._delegate_generate_content({"data": entrada, "voice": voz})

    assert result == {"ok": "generate"}
    assert llamado["data"] is entrada
    assert llamado["voice"] == voz


def test_generate_content_handler_defaults_voice_to_none(monkeypatch):
    """Sin 'voice' en los argumentos, el handler pasa voice=None a enrich (Req 8.2)."""
    llamado = {}

    def fake_enrich(data, voice=None):
        llamado["voice"] = voice
        return {}

    monkeypatch.setattr(server.generate_content, "enrich", fake_enrich)

    server._delegate_generate_content({"data": {}})

    assert llamado["voice"] is None


def test_build_site_handler_delegates_to_core(monkeypatch):
    """El handler de build_site instancia Puriq(project) y delega en .build(use_llm) (Req 8.2)."""
    registro = {}

    class FakePuriq:
        def __init__(self, project):
            registro["project"] = project

        def build(self, use_llm=True):
            registro["use_llm"] = use_llm
            return Path("/proj/dist")

    monkeypatch.setattr(server, "Puriq", FakePuriq)

    result = server._delegate_build_site({"project": "/proj", "use_llm": False})

    assert result == str(Path("/proj/dist"))
    assert registro["project"] == Path("/proj")
    assert registro["use_llm"] is False


def test_build_site_handler_use_llm_defaults_true(monkeypatch):
    """Sin 'use_llm', el handler de build_site delega con use_llm=True (Req 8.2)."""
    registro = {}

    class FakePuriq:
        def __init__(self, project):
            pass

        def build(self, use_llm=True):
            registro["use_llm"] = use_llm
            return Path("/proj/dist")

    monkeypatch.setattr(server, "Puriq", FakePuriq)

    server._delegate_build_site({"project": "/proj"})

    assert registro["use_llm"] is True


def test_deploy_handler_delegates_to_core(monkeypatch):
    """El handler de deploy instancia Puriq(project) y delega en .deploy(target) (Req 8.2)."""
    registro = {}

    class FakePuriq:
        def __init__(self, project):
            registro["project"] = project

        def deploy(self, target="aws-amplify"):
            registro["target"] = target
            return "https://sitio.example"

    monkeypatch.setattr(server, "Puriq", FakePuriq)

    result = server._delegate_deploy({"project": "/proj", "target": "s3-cloudfront"})

    assert result == "https://sitio.example"
    assert registro["project"] == Path("/proj")
    assert registro["target"] == "s3-cloudfront"


def test_deploy_handler_target_defaults_to_aws_amplify(monkeypatch):
    """Sin 'target', el handler de deploy delega con el destino por defecto (Req 8.2)."""
    registro = {}

    class FakePuriq:
        def __init__(self, project):
            pass

        def deploy(self, target="aws-amplify"):
            registro["target"] = target
            return "ok"

    monkeypatch.setattr(server, "Puriq", FakePuriq)

    server._delegate_deploy({"project": "/proj"})

    assert registro["target"] == "aws-amplify"


def test_call_tool_delegates_via_registered_handler(monkeypatch):
    """El handler call_tool del servidor enruta al delegado registrado (Req 8.2).

    Se verifica de extremo a extremo (con el stub del SDK) que invocar una tool por
    nombre ejecuta su delegación y devuelve el resultado serializado como texto.
    """
    _install_mcp_stub(monkeypatch)

    def fake_run(resources_dir):
        return {"site": {"name": "Demo"}}

    monkeypatch.setattr(server.scan_resources, "run", fake_run)

    srv = server.build_server()
    result = asyncio.run(
        srv.call_tool_handler("scan_resources", {"resources_dir": "/tmp/x"})
    )

    # Resultado exitoso: lista con un TextContent cuyo texto es el JSON del contrato.
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].type == "text"
    assert "Demo" in result[0].text
