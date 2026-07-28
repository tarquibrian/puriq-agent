"""Servidor MCP `tourism-builder`: expone las tools del core a un cliente LLM (Claude).

Es un envoltorio fino sobre `puriq.core` / `puriq.tools`. Comparte exactamente la
misma implementación que usa el CLI, por lo que **no duplica lógica** (Req 8.2):
cada handler MCP delega en la función/método correspondiente.

Tools expuestas (Req 8.1, 11.1):

  - `scan_resources`    -> `puriq.tools.scan_resources.run(resources_dir)`
  - `import_open_data`  -> `puriq.tools.import_open_data.merge(data)`
  - `generate_content`  -> `puriq.tools.generate_content.enrich(data, voice)`
  - `build_site`        -> `puriq.core.Puriq(project).build(use_llm)`
  - `deploy`            -> `puriq.core.Puriq(project).deploy(target)`
  - `manage_articles`   -> `puriq.core.Puriq(project).{create,list,edit,delete}_article`
                           (una tool, despacho por `action`: create|list|edit|delete)
  - `query_content`     -> `puriq.core.Puriq(project).query(kind, **filters)`
  - `edit_content`      -> `puriq.core.Puriq(project).edit(id, fields)`
  - `delete_content`    -> `puriq.core.Puriq(project).delete(id)`
  - `bulk_update`       -> `puriq.core.Puriq(project).bulk_update(csv_path, kind)`
  - `analyze_seo`       -> `puriq.core.Puriq(project).analyze_seo()`

Decisión de API (qué firma expone cada tool al cliente LLM)
-----------------------------------------------------------
Las tools de la **fase de datos** (`scan_resources`, `import_open_data`,
`generate_content`) operan sobre el **contrato en memoria**: reciben/devuelven el
documento `tourism-data` como un objeto JSON. Esto permite al cliente encadenarlas
(scan -> import -> generate) pasando el dict de una a la siguiente sin tocar disco.

Las tools de la **fase de proyecto** (`build_site`, `deploy`) son
**orientadas a proyecto**: reciben la ruta del proyecto y delegan en los métodos
de alto nivel de `puriq.core.Puriq`, que leen el contrato persistido del disco,
ejecutan el pipeline completo (geocode -> validación estricta -> generate ->
ensamblado de Astro) y publican el `dist/`. Es la interfaz más natural para un
cliente conversacional: "construí el proyecto en esta carpeta", "publicá el sitio".

Manejo de errores (Req 8.4)
---------------------------
Si una tool lanza una excepción, el handler la captura, construye un mensaje
descriptivo y lo enmascara con `puriq.config.redact` antes de devolverlo al
cliente como un resultado de error. Así ningún valor de secreto (credenciales AWS,
etc.) aparece en la respuesta.

Notas de importación
--------------------
El SDK `mcp` (extra `mcp`) se importa de forma **diferida** dentro de
`build_server()` / `main()`, de modo que este módulo pueda importarse (y sus
`TOOL_SPECS`/delegaciones inspeccionarse) incluso en entornos sin el extra `mcp`
instalado. Los esquemas de entrada se declaran como dicts JSON Schema puros
(`TOOL_SPECS`), sin depender del SDK.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from puriq import schemas
from puriq.config import redact
from puriq.core import Puriq
from puriq.intake.tools import (
    INTAKE_GUION,
    INTAKE_TOOL_NAMES,
    INTAKE_TOOL_SPECS,
    run_intake_tool,
)
from puriq.tools import deploy as deploy_tool
from puriq.tools import generate_content, import_open_data, scan_resources

#: Nombre del servidor MCP anunciado al cliente.
SERVER_NAME = "tourism-builder"

#: URI del recurso MCP que expone el guion del intake como contexto (Req 13.5).
#: El cliente MCP puede leerlo con `read_resource("intake://guion")` para cargar
#: el Guion_Intake (fases 1-9) sin depender de las descripciones de las tools.
INTAKE_RESOURCE_URI = "intake://guion"


# --- Delegaciones a la implementación compartida (Req 8.2) -------------------
# Cada delegación recibe el dict de argumentos ya validado contra el inputSchema
# de la tool y devuelve un valor JSON-serializable (dict o str). NO duplican
# lógica: llaman a la misma función/método que usa el CLI vía `puriq.core`.


def _delegate_scan_resources(arguments: dict[str, Any]) -> dict:
    """Delega en `scan_resources.run`: recursos crudos -> contrato `tourism-data`."""
    return scan_resources.run(Path(arguments["resources_dir"]))


def _delegate_import_open_data(arguments: dict[str, Any]) -> dict:
    """Delega en `import_open_data.merge`: enriquece el contrato con datos abiertos."""
    return import_open_data.merge(arguments["data"])


def _delegate_generate_content(arguments: dict[str, Any]) -> dict:
    """Delega en `generate_content.enrich`: rellena descripciones/SEO/i18n con el LLM."""
    return generate_content.enrich(arguments["data"], arguments.get("voice"))


def _delegate_build_site(arguments: dict[str, Any]) -> str:
    """Delega en `Puriq(project).build`: ensambla el sitio y devuelve la ruta de `dist/`."""
    project = Path(arguments["project"])
    use_llm = bool(arguments.get("use_llm", True))
    return str(Puriq(project).build(use_llm=use_llm))


def _delegate_deploy(arguments: dict[str, Any]) -> str:
    """Delega en `Puriq(project).deploy`: publica el sitio y devuelve la URL/ruta pública."""
    project = Path(arguments["project"])
    target = arguments.get("target") or "aws-amplify"
    return Puriq(project).deploy(target=target)


# --- Delegaciones de gestión de contenido (content-management, Req 11) -------
# Las seis tools de gestión de contenido operan sobre un **proyecto** (como
# `build_site`/`deploy`): reciben la ruta `project` y delegan en los métodos de
# `puriq.core.Puriq` —los MISMOS que usa el CLI—, sin duplicar lógica (Req 11.2).


def _delegate_manage_articles(arguments: dict[str, Any]) -> Any:
    """Delega en las cuatro operaciones de artículos del core según `action` (Req 1-5).

    `manage_articles` es UNA sola tool MCP que expone las cuatro operaciones CRUD
    sobre artículos del blog a través de un campo `action` (enum
    `create|list|edit|delete`). Según su valor, el handler despacha al método
    correspondiente de `Puriq`:

      - `create` -> `create_article(title, body?, date?, tags?, category?, summary?)`
      - `list`   -> `list_articles(date_from?, date_to?, tag?, category?)`
      - `edit`   -> `edit_article(id, **fields)` (merge de campos)
      - `delete` -> `delete_article(id)`

    Se justifica una única tool con despacho porque el artefacto sobre el que
    operan (el Content_Store `/content`) es el mismo, y así el cliente LLM tiene
    una superficie coherente de gestión de artículos en lugar de cuatro tools casi
    idénticas.
    """
    puriq = Puriq(Path(arguments["project"]))
    action = arguments["action"]

    if action == "create":
        return puriq.create_article(
            title=arguments["title"],
            body=arguments.get("body"),
            date=arguments.get("date"),
            tags=arguments.get("tags"),
            category=arguments.get("category"),
            summary=arguments.get("summary"),
        )
    if action == "list":
        return puriq.list_articles(
            date_from=arguments.get("date_from"),
            date_to=arguments.get("date_to"),
            tag=arguments.get("tag"),
            category=arguments.get("category"),
        )
    if action == "edit":
        fields = dict(arguments.get("fields") or {})
        return puriq.edit_article(id=arguments["id"], **fields)
    if action == "delete":
        return puriq.delete_article(id=arguments["id"])

    raise ValueError(
        f"Acción de manage_articles desconocida: '{action}'. "
        "Use una de: create, list, edit, delete."
    )


def _delegate_query_content(arguments: dict[str, Any]) -> Any:
    """Delega en `Puriq(project).query(kind, **filters)`: consulta Places/Events (Req 6)."""
    puriq = Puriq(Path(arguments["project"]))
    filtros = {
        clave: arguments[clave]
        for clave in ("category", "tag", "name", "date_from", "date_to")
        if arguments.get(clave) is not None
    }
    return puriq.query(arguments["kind"], **filtros)


def _delegate_edit_content(arguments: dict[str, Any]) -> Any:
    """Delega en `Puriq(project).edit(id, fields)`: merge de campos sobre un Place/Event (Req 7)."""
    puriq = Puriq(Path(arguments["project"]))
    return puriq.edit(arguments["id"], dict(arguments.get("fields") or {}))


def _delegate_delete_content(arguments: dict[str, Any]) -> Any:
    """Delega en `Puriq(project).delete(id)`: elimina un Place/Event con integridad ref. (Req 8)."""
    puriq = Puriq(Path(arguments["project"]))
    return puriq.delete(arguments["id"])


def _delegate_bulk_update(arguments: dict[str, Any]) -> Any:
    """Delega en `Puriq(project).bulk_update(csv_path, kind)`: fusión masiva desde CSV (Req 9)."""
    puriq = Puriq(Path(arguments["project"]))
    return puriq.bulk_update(Path(arguments["csv_path"]), arguments["kind"])


def _delegate_analyze_seo(arguments: dict[str, Any]) -> Any:
    """Delega en `Puriq(project).analyze_seo()`: análisis SEO local de solo lectura (Req 10)."""
    puriq = Puriq(Path(arguments["project"]))
    return puriq.analyze_seo()


#: Especificaciones de las tools de **pipeline y edición** (las 11 tools que ya
#: existían antes del intake) como datos puros (sin dependencia del SDK `mcp`).
#: Cada entrada declara el `name`, la `description` y el `inputSchema` (JSON Schema)
#: acorde a la firma de la implementación delegada (Req 8.3), más la función
#: `handler` que realiza la delegación (Req 8.2).
_EXISTING_SPECS: list[dict[str, Any]] = [
    {
        "name": "scan_resources",
        "description": (
            "Lee una carpeta de recursos crudos (site.json + places.csv y, opcional, "
            "events.csv) y devuelve el documento tourism-data (contrato de contenido) "
            "como objeto JSON. No llama al LLM ni geocodifica."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resources_dir": {
                    "type": "string",
                    "description": (
                        "Ruta al directorio de recursos crudos que contiene site.json y "
                        "places.csv (events.csv es opcional)."
                    ),
                }
            },
            "required": ["resources_dir"],
            "additionalProperties": False,
        },
        "handler": _delegate_scan_resources,
    },
    {
        "name": "import_open_data",
        "description": (
            "Enriquece un documento tourism-data con lugares de fuentes abiertas "
            "(OpenStreetMap/Wikidata/Wikimedia Commons) dentro del área de site.center. "
            "Devuelve el documento enriquecido. Si una fuente falla, devuelve el "
            "documento sin cambios."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": (
                        "Documento tourism-data a enriquecer (el mismo objeto que produce "
                        "scan_resources). Debe incluir site.center con lat/lng."
                    ),
                }
            },
            "required": ["data"],
            "additionalProperties": False,
        },
        "handler": _delegate_import_open_data,
    },
    {
        "name": "generate_content",
        "description": (
            "Rellena con el LLM el contenido faltante del documento tourism-data: "
            "descripciones vacías de lugares/eventos, meta descripción SEO del sitio y "
            "traducciones a los locales configurados. Conserva el contenido existente. "
            "Devuelve el documento enriquecido (las traducciones van en la clave "
            "companion 'i18n', fuera del esquema del contrato)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": "Documento tourism-data a completar.",
                },
                "voice": {
                    "type": ["object", "null"],
                    "description": (
                        "Subdocumento de voz de la marca (theme.tokens -> voice), con "
                        "'tone' y opcional 'formality'. Puede omitirse o ser null."
                    ),
                    "properties": {
                        "tone": {"type": "string"},
                        "formality": {"type": "string"},
                    },
                },
            },
            "required": ["data"],
            "additionalProperties": False,
        },
        "handler": _delegate_generate_content,
    },
    {
        "name": "build_site",
        "description": (
            "Construye el sitio estático Astro del proyecto a partir del contrato "
            "persistido (tourism-data.json + site.config.json + theme.tokens.json). "
            "Ejecuta el pipeline completo (geocode -> validación -> generación de "
            "contenido -> ensamblado) y devuelve la ruta del directorio dist/."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "Ruta del proyecto que contiene los tres documentos del contrato."
                    ),
                },
                "use_llm": {
                    "type": "boolean",
                    "description": (
                        "Si es true (por defecto) genera contenido faltante con el LLM "
                        "antes de ensamblar; si es false, omite la generación."
                    ),
                    "default": True,
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": _delegate_build_site,
    },
    {
        "name": "deploy",
        "description": (
            "Publica el sitio ya construido (dist/) del proyecto mediante el adaptador "
            "del destino indicado y devuelve la URL pública (o la ruta local para "
            "static-export)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Ruta del proyecto cuyo dist/ se va a publicar.",
                },
                "target": {
                    "type": "string",
                    "description": "Destino de publicación.",
                    "enum": list(deploy_tool.ADAPTERS),
                    "default": "aws-amplify",
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": _delegate_deploy,
    },
    # --- tools de gestión de contenido (content-management, Req 11.1, 11.4) ---
    {
        "name": "manage_articles",
        "description": (
            "Gestiona los artículos del blog (Content_Store /content) mediante un "
            "campo 'action' (create|list|edit|delete). create: crea un artículo a "
            "partir de un título (genera el cuerpo con el LLM si no se aporta) y "
            "devuelve id+ruta. list: lista/filtra artículos por rango de fechas, "
            "etiqueta o categoría. edit: hace merge de los campos indicados sobre un "
            "artículo existente por id. delete: elimina el artículo con ese id. Una "
            "sola tool para las cuatro operaciones CRUD sobre el mismo artefacto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Ruta del proyecto que contiene la carpeta content/.",
                },
                "action": {
                    "type": "string",
                    "description": (
                        "Operación a realizar sobre los artículos: create, list, edit "
                        "o delete."
                    ),
                    "enum": ["create", "list", "edit", "delete"],
                },
                "id": {
                    "type": "string",
                    "description": (
                        "Id (slug) del artículo. Obligatorio para action=edit y "
                        "action=delete."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Título del artículo. Obligatorio para action=create.",
                },
                "body": {
                    "type": ["string", "null"],
                    "description": (
                        "Cuerpo markdown del artículo (action=create). Si se omite, se "
                        "genera con el LLM."
                    ),
                },
                "date": {
                    "type": ["string", "null"],
                    "description": (
                        "Fecha del artículo en formato YYYY-MM-DD (action=create). Si se "
                        "omite, se usa la fecha actual."
                    ),
                },
                "tags": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "Lista de etiquetas del artículo (action=create).",
                },
                "category": {
                    "type": ["string", "null"],
                    "description": (
                        "Categoría del artículo: al crear (action=create) o como filtro "
                        "(action=list)."
                    ),
                },
                "summary": {
                    "type": ["string", "null"],
                    "description": "Resumen del artículo (action=create).",
                },
                "date_from": {
                    "type": ["string", "null"],
                    "description": (
                        "Filtro de fecha inicial inclusive YYYY-MM-DD (action=list)."
                    ),
                },
                "date_to": {
                    "type": ["string", "null"],
                    "description": (
                        "Filtro de fecha final inclusive YYYY-MM-DD (action=list)."
                    ),
                },
                "tag": {
                    "type": ["string", "null"],
                    "description": "Filtro por etiqueta (action=list).",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Mapa de campos a actualizar (merge) sobre el artículo "
                        "(action=edit). Solo se tocan los campos indicados."
                    ),
                },
            },
            "required": ["project", "action"],
            "additionalProperties": False,
        },
        "handler": _delegate_manage_articles,
    },
    {
        "name": "query_content",
        "description": (
            "Consulta (solo lectura) los Places o Events del contrato tourism-data del "
            "proyecto, con filtros opcionales por categoría, etiqueta, nombre (sin "
            "distinguir mayúsculas/minúsculas) y rango de fechas de startDate. Devuelve "
            "la lista de elementos que cumplen todos los filtros."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Ruta del proyecto que contiene tourism-data.json.",
                },
                "kind": {
                    "type": "string",
                    "description": "Tipo de elemento a consultar.",
                    "enum": ["places", "events"],
                },
                "category": {
                    "type": ["string", "null"],
                    "description": "Filtro por categoría (Places).",
                },
                "tag": {
                    "type": ["string", "null"],
                    "description": "Filtro por etiqueta (Places).",
                },
                "name": {
                    "type": ["string", "null"],
                    "description": (
                        "Filtro por nombre; coincide si el nombre contiene el texto "
                        "(sin distinguir mayúsculas/minúsculas)."
                    ),
                },
                "date_from": {
                    "type": ["string", "null"],
                    "description": (
                        "Filtro de startDate inicial inclusive YYYY-MM-DD (Events)."
                    ),
                },
                "date_to": {
                    "type": ["string", "null"],
                    "description": (
                        "Filtro de startDate final inclusive YYYY-MM-DD (Events)."
                    ),
                },
            },
            "required": ["project", "kind"],
            "additionalProperties": False,
        },
        "handler": _delegate_query_content,
    },
    {
        "name": "edit_content",
        "description": (
            "Edita un Place o Event del contrato tourism-data por id, haciendo merge "
            "solo de los campos indicados (preserva el resto). Valida el resultado "
            "contra el esquema antes de persistir; si el id no existe o la edición "
            "invalida el contrato, se rechaza sin escribir. Devuelve el id modificado."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Ruta del proyecto que contiene tourism-data.json.",
                },
                "id": {
                    "type": "string",
                    "description": "Id del Place o Event a editar.",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Mapa de campos a actualizar (merge). Solo se sobrescriben los "
                        "campos presentes; deben ser campos válidos del esquema."
                    ),
                },
            },
            "required": ["project", "id", "fields"],
            "additionalProperties": False,
        },
        "handler": _delegate_edit_content,
    },
    {
        "name": "delete_content",
        "description": (
            "Elimina un Place o Event del contrato tourism-data por id, manejando la "
            "integridad referencial: al borrar un Place, limpia el placeId de los "
            "Events que lo referenciaban. Valida el resultado antes de persistir. "
            "Devuelve el id eliminado y los Events afectados."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Ruta del proyecto que contiene tourism-data.json.",
                },
                "id": {
                    "type": "string",
                    "description": "Id del Place o Event a eliminar.",
                },
            },
            "required": ["project", "id"],
            "additionalProperties": False,
        },
        "handler": _delegate_delete_content,
    },
    {
        "name": "bulk_update",
        "description": (
            "Fusiona un CSV de Places o Events en el contrato tourism-data por id: "
            "agrega los elementos nuevos y hace merge de los existentes (solo los "
            "campos presentes en cada fila). Omite y reporta las filas sin id ni "
            "nombre. Valida el resultado antes de persistir. Devuelve un resumen con "
            "added, updated y skipped."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Ruta del proyecto que contiene tourism-data.json.",
                },
                "csv_path": {
                    "type": "string",
                    "description": "Ruta al archivo CSV de Places o Events a fusionar.",
                },
                "kind": {
                    "type": "string",
                    "description": "Tipo de elementos que contiene el CSV.",
                    "enum": ["places", "events"],
                },
            },
            "required": ["project", "csv_path", "kind"],
            "additionalProperties": False,
        },
        "handler": _delegate_bulk_update,
    },
    {
        "name": "analyze_seo",
        "description": (
            "Analiza el contenido y la salida generada localmente del proyecto "
            "(tourism-data.json, /content, dist/) en busca de problemas de SEO: faltas "
            "de meta descripción/resumen, títulos inadecuados, imágenes sin texto "
            "alternativo, jerarquía de encabezados incorrecta y slugs mal formados. Es "
            "de solo lectura: nunca modifica el contenido. Devuelve la lista de "
            "sugerencias."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Ruta del proyecto a analizar.",
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": _delegate_analyze_seo,
    },
]

#: Especificaciones completas anunciadas por el servidor MCP, compuestas de forma
#: **aditiva** (DD-1, Req 13.1, 13.6): primero las 11 tools de pipeline y edición
#: ya existentes (`_EXISTING_SPECS`), luego las 12 intake tools
#: (`INTAKE_TOOL_SPECS`). Las intake specs comparten la MISMA forma
#: (name/description/inputSchema/handler), por lo que `_HANDLERS`, `list_tools` y
#: `_serialize` las cubren por construcción sin tocar el motor.
TOOL_SPECS: list[dict[str, Any]] = [*_EXISTING_SPECS, *INTAKE_TOOL_SPECS]

#: Índice nombre -> handler de delegación, derivado de `TOOL_SPECS`.
_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    spec["name"]: spec["handler"] for spec in TOOL_SPECS
}


def _serialize(result: Any) -> str:
    """Serializa el resultado de una tool a texto para el cliente MCP.

    Los documentos del contrato (dict) se serializan como JSON legible con
    `schemas.dumps` (UTF-8, indentado); los resultados de texto (str, p. ej. rutas
    o URLs de build/deploy) se devuelven tal cual.
    """
    if isinstance(result, str):
        return result
    return schemas.dumps(result)


def build_server():
    """Construye y devuelve el `Server` MCP `tourism-builder` con las tools registradas.

    Registra un handler `list_tools` que declara todas las tools con su `inputSchema`
    (Req 8.1, 8.3, 11.1, 11.4) y un handler `call_tool` que delega en la implementación
    compartida (Req 8.2, 11.2) y traduce cualquier error a un mensaje descriptivo sin
    secretos (Req 8.4, 11.5).

    El SDK `mcp` se importa aquí de forma diferida para no exigirlo al importar el
    módulo.
    """
    from mcp.server.lowlevel import Server
    from mcp.server.lowlevel.helper_types import ReadResourceContents
    from mcp.types import CallToolResult, Resource, TextContent, Tool

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        """Declara las tools disponibles y sus esquemas de entrada (Req 8.1, 8.3)."""
        return [
            Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in TOOL_SPECS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent] | CallToolResult:
        """Ejecuta una tool delegando en el core; enmascara errores (Req 8.2, 8.4).

        Las intake tools (Req 13.3) se enrutan por `run_intake_tool`, que ya
        despacha al handler correspondiente y traduce **internamente** cualquier
        excepción a un resultado accionable y redactado (DD-5): nunca lanza, así
        que su resultado se serializa directamente (sin envolver en `isError`).
        Las tools de pipeline/edición conservan su camino actual (handler +
        try/except con `redact` como red de seguridad).
        """
        # Ruteo de las intake tools (Req 13.3): run_intake_tool no lanza (traduce
        # internamente), por lo que su resultado se serializa tal cual.
        if name in INTAKE_TOOL_NAMES:
            return [TextContent(type="text", text=_serialize(run_intake_tool(name, arguments or {})))]

        handler = _HANDLERS.get(name)
        if handler is None:
            # Tool desconocida: error descriptivo (sin secretos que enmascarar).
            return CallToolResult(
                content=[TextContent(type="text", text=f"Tool desconocida: '{name}'.")],
                isError=True,
            )
        try:
            result = handler(arguments or {})
        except Exception as exc:  # noqa: BLE001 - se traduce a error de cliente
            # Mensaje descriptivo, enmascarando cualquier valor de secreto (Req 8.4).
            mensaje = redact(f"La tool '{name}' falló: {exc}")
            return CallToolResult(
                content=[TextContent(type="text", text=mensaje)],
                isError=True,
            )
        return [TextContent(type="text", text=_serialize(result))]

    # --- Recurso MCP del guion del intake (Req 13.4, 13.5) -------------------
    # Además de embeber el Guion_Intake en las descripciones de las intake tools
    # (Req 13.4, hecho en 10.1), se expone como un único recurso MCP legible
    # (`intake://guion`, text/markdown) para que el cliente lo cargue como
    # contexto de la conversación (Req 13.5). Es aditivo e independiente de
    # list_tools/call_tool.

    @server.list_resources()
    async def _list_resources() -> list[Resource]:
        """Anuncia el único recurso del intake: el guion en Markdown (Req 13.5)."""
        return [
            Resource(
                uri=INTAKE_RESOURCE_URI,
                name="Guion del intake",
                description=(
                    "Guion conversacional del registro (fases 1-9 y la regla de "
                    "pedir archivos activamente) para conducir el intake por MCP."
                ),
                mimeType="text/markdown",
            )
        ]

    @server.read_resource()
    async def _read_resource(uri) -> list[ReadResourceContents]:
        """Devuelve el contenido del guion para `intake://guion` (Req 13.5).

        `uri` llega como un `AnyUrl` del SDK; se compara de forma tolerante
        (normalizando una posible barra final). Una URI desconocida se rechaza
        con un error claro, alineado con las convenciones del SDK (el servidor lo
        traduce a un resultado de error para el cliente).
        """
        solicitada = str(uri).rstrip("/")
        if solicitada != INTAKE_RESOURCE_URI:
            raise ValueError(f"Recurso desconocido: '{uri}'.")
        return [ReadResourceContents(content=INTAKE_GUION, mime_type="text/markdown")]

    return server


def main() -> None:
    """Arranca el servidor MCP `tourism-builder` sobre el transporte stdio.

    Registra las tools (vía `build_server`) y sirve el protocolo MCP por stdin/stdout,
    el transporte estándar para que un cliente LLM (p. ej. Claude Desktop) lo lance
    como subproceso.
    """
    # El SDK vive detras de un extra opcional. Sin el, esto reventaba con un
    # ModuleNotFoundError crudo: el cliente MCP solo muestra "el servidor no
    # arranco" y quien lo configuro no tiene forma de saber que le falta.
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        print(
            "Falta el SDK de MCP. Instalalo con:\n"
            '  pip install "puriq[mcp]"\n'
            "o, si trabajas sobre el repositorio:\n"
            '  pip install -e "agent[mcp]"',
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    import anyio

    async def _serve() -> None:
        server = build_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )

    anyio.run(_serve)


if __name__ == "__main__":
    main()
