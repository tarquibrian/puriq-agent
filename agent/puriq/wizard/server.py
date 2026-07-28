"""Wizard web local: la interfaz amigable para el encargado de turismo (no-tecnico).

Un solo punto de intake (ver PROYECTO seccion 6): modulos, preguntas clave,
carga de recursos, Q&A del chatbot y marca. Luego build y preview en el mismo sitio.

Este modulo es la **capa web fina** (FastAPI) sobre `puriq.core`/`puriq.tools` y la
capa de contrato (`wizard/contracts.py`): cada endpoint valida, delega y responde
redactado (DD-1/DD-4). Aqui viven el scaffold de la app, el servido de la UI
estatica (`/static` + `GET /`), los endpoints de estado/intake/assets/Q&A, el
WebSocket de build, preview/deploy y los **manejadores de error transversales**
que aplican `wizard_error_response`+`config.redact` a toda respuesta HTTP para que
ninguna traza cruda ni valor de secreto se filtre (Req 12.2). El servidor escucha
solo en `127.0.0.1` (`serve()`, Req 12.1).
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from pathlib import Path

import jsonschema
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from puriq import config
from puriq.config import get_env, redact_value
from puriq.core import Puriq
from puriq.errors import wizard_error_response
from puriq.intake.agent import ChatAgent, ChatRequest
from puriq.intake.ingest import IncomingFile
from puriq.wizard import contracts
from puriq.wizard.asset_store import append_image, next_available_asset
from puriq.wizard.assets import (
    IMAGE_EXTS,
    MAX_ASSET_BYTES,
    normalize_asset_name,
    resolve_within_assets,
)
from puriq.wizard.intake import (
    CoordinateRangeError,
    build_event,
    build_place,
    make_coords,
)
from puriq.wizard.qa_store import append_qa_entry, register_knowledge_source
from puriq.tools import build_site
from puriq.tools.deploy import DeployError
from puriq.wizard.landing import LandingCatalogError, build_landing
from puriq.wizard.modules import ModuleCatalogError, build_modules
from puriq.wizard.validation import (
    DeployTargetError,
    DomainError,
    QAValidationError,
    validate_deploy_target,
    validate_domain,
    validate_qa_entry,
)

STATIC = Path(__file__).parent / "static"

# Variable de entorno con la que se puede fijar la raiz del proyecto sobre el que
# opera el wizard. Si no esta definida, se usa el directorio de trabajo actual.
PROJECT_ENV_VAR = "PURIQ_PROJECT"

# Documentos del contrato que `GET /api/state` carga para prellenar la UI.
_STATE_DOCS = ("tourism-data", "site-config", "theme-tokens")

# Clave del documento de contenido turistico y su nombre de archivo (para nombrar
# el documento infractor en las respuestas de error redactadas, Req 7.2).
_TOURISM_DOC = "tourism-data"
_TOURISM_FILE = "tourism-data.json"

# Documento de estructura (modulos, hero, deploy) y su nombre de archivo.
_SITE_CONFIG_DOC = "site-config"
_SITE_CONFIG_FILE = "site.config.json"

# Documento de marca (colores, tipografia, voz, logo) y su nombre de archivo.
_THEME_DOC = "theme-tokens"
_THEME_FILE = "theme.tokens.json"

# Limite de tamano de un Asset (Req 4.5). `MAX_ASSET_BYTES` vive ahora en el
# modulo puro `wizard/assets.py` (junto a `IMAGE_EXTS`) y se importa arriba, de
# modo que la capa web y las intake tools reutilicen el mismo limite (DD-3). Se
# compara ANTES de escribir en disco.
_MAX_ASSET_MB = MAX_ASSET_BYTES // (1024 * 1024)

# Almacenamiento de la base de conocimiento Q&A (Req 5.1, 5.2, 5.3).
# Formato elegido: un unico archivo `content/qa.json` en la raiz del proyecto
# con una lista JSON de entradas ``{"question", "answer"}``. Los QA_Entry se
# **anexan** (no se pisan) y NO se indexan ni consumen durante el wizard
# (Req 5.3); un chatweb futuro leera este archivo. La ruta relativa
# `content/qa.json` es la que se registra en
# `Site_Config.modules.chatweb.knowledgeSource` (Req 5.2), coherente con que el
# knowledgeSource apunta al arbol `/content`.
_CONTENT_DIRNAME = "content"
_QA_FILENAME = "qa.json"
_QA_RELPATH = f"{_CONTENT_DIRNAME}/{_QA_FILENAME}"

app = FastAPI(title="Puriq Wizard")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Catalogo tipografico de la Template, servido tal cual al Wizard_UI. El paso de
# Marca muestra una vista previa en vivo de la identidad visual; sin las fuentes
# REALES esa vista previa mentiria (mostraria la marca con la tipografia del
# sistema y no con la que el usuario esta eligiendo). Se monta el mismo
# directorio que consume el sitio construido, para que no haya dos copias.
_TEMPLATE_FONTS = build_site.TEMPLATE_DIR / "public" / "fonts"
if _TEMPLATE_FONTS.is_dir():
    app.mount("/fonts", StaticFiles(directory=_TEMPLATE_FONTS), name="fonts")


def project_root() -> Path:
    """Resuelve la raiz del proyecto local sobre el que opera el wizard.

    El wizard trabaja siempre sobre un proyecto local (los 3 JSON del contrato,
    `/assets`, `/content`, `dist/`). La raiz se toma de la variable de entorno
    `PURIQ_PROJECT` si esta definida y no vacia; de lo contrario, del directorio
    de trabajo actual. Se resuelve a una ruta absoluta para que la capa de
    contrato y la contencion de assets trabajen sobre rutas estables.
    """
    raw = os.environ.get(PROJECT_ENV_VAR)
    base = Path(raw) if raw else Path.cwd()
    return base.resolve()


# `_redact_value` se movio a `config.redact_value` (unica fuente de verdad para
# redactar estructuras compuestas, DD-4) y se importa arriba como `redact_value`.


# --- Manejadores de errores transversales (Req 12.2, 7.5, DD-4) --------------
#
# Ademas del manejo `422` por-endpoint (que ya nombra documento+campo del
# contrato), se registran dos manejadores a nivel de la app como red de
# seguridad: ninguna respuesta HTTP puede filtrar una traza cruda ni un valor de
# secreto. Ambos pasan por `wizard_error_response` (traduccion accionable DD-4)
# que **siempre** aplica `config.redact` antes de serializar, y ademas se re-
# redacta el cuerpo completo con `redact_value` por defensa en profundidad.
#
#   - `RequestValidationError` (pydantic/FastAPI): se lanza ANTES de entrar al
#     endpoint cuando el cuerpo/params no cumplen el modelo. El cuerpo por
#     defecto de FastAPI incluye los valores de entrada, que podrian contener un
#     secreto; por eso se responde `422` con un cuerpo redactado en vez del
#     default (Req 12.2).
#   - `Exception` (catch-all): cualquier excepcion no manejada por un endpoint se
#     traduce a un `500` redactado con causa + accion sugerida, sin traceback
#     (Req 12.2). El manejador propio de `HTTPException` de Starlette no se ve
#     afectado (solo se captura lo que no tiene manejador especifico).


def _field_path(loc: object) -> str:
    """Construye una ruta de campo legible desde el `loc` de un error pydantic.

    Descarta el prefijo `body`/`query`/`path` y une el resto con puntos/indices
    (p. ej. `('body','places',0,'name')` -> `places[0].name`). No incluye el
    valor de entrada, solo la ubicacion del campo.
    """
    partes = list(loc) if isinstance(loc, (list, tuple)) else [loc]
    if partes and partes[0] in ("body", "query", "path", "header", "cookie"):
        partes = partes[1:]
    campo = ""
    for parte in partes:
        if isinstance(parte, int):
            campo += f"[{parte}]"
        elif campo:
            campo += f".{parte}"
        else:
            campo = str(parte)
    return campo or "(cuerpo)"


@app.exception_handler(RequestValidationError)
async def _handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Devuelve un `422` redactado ante un error de validacion de la peticion (Req 12.2).

    Reemplaza el cuerpo por defecto de FastAPI por una respuesta accionable que
    nombra unicamente los campos invalidos y su motivo, **sin** incluir los
    valores de entrada (que podrian ser secretos escritos por el usuario) ni la
    ruta/linea del codigo que expone el `str` de la excepcion en esta version de
    FastAPI. El texto se redacta por defensa en profundidad (Req 7.5, 12.2).
    """
    detalles = "; ".join(
        f"{_field_path(err.get('loc'))}: {err.get('msg')}"
        for err in exc.errors()
    )
    causa = "Entrada invalida en la peticion"
    if detalles:
        causa = f"{causa}: {detalles}"
    cuerpo = {
        "causa": config.redact(causa),
        "accion": config.redact(
            "Revisa los campos indicados y volve a enviar la peticion."
        ),
    }
    return JSONResponse(status_code=422, content=redact_value(cuerpo))


@app.exception_handler(Exception)
async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Red de seguridad: traduce cualquier excepcion no manejada a un `500` redactado.

    Garantiza que ninguna excepcion inesperada filtre un traceback ni un valor de
    secreto en la respuesta HTTP (Req 12.2). Reutiliza `wizard_error_response`
    (misma traduccion accionable que el CLI, DD-4) y aplica `config.redact` de
    forma exhaustiva sobre el cuerpo.
    """
    return JSONResponse(
        status_code=500,
        content=redact_value(wizard_error_response(exc)),
    )


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/fonts")
def list_fonts() -> dict:
    """Informa que archivos de fuente trae el catalogo de la Template.

    El Wizard_UI usa esto para dos cosas en el paso de Marca: marcar que familias
    se sirven desde el propio sitio (frente a las que dependeran de la pila de
    respaldo del sistema) y saber que `@font-face` inyectar en su vista previa.

    Devuelve los NOMBRES DE ARCHIVO (`playfair-display-var.woff2`), no las
    familias: la tabla de familias, pesos y pilas de respaldo vive en
    `template/src/design-system/fonts.ts`, unica fuente de verdad. Aqui solo se
    reporta lo que hay en disco, para no mantener una tercera copia del catalogo.
    """
    if not _TEMPLATE_FONTS.is_dir():
        return {"files": []}
    archivos = sorted(
        f.name for f in _TEMPLATE_FONTS.iterdir()
        if f.is_file() and f.suffix == ".woff2"
    )
    return {"files": archivos}


@app.get("/api/version")
def get_version() -> dict:
    """Huella barata del contrato en disco, para detectar cambios externos.

    El contrato es la unica fuente de verdad y hay mas de una superficie que lo
    escribe: el propio wizard, un cliente MCP conversando por Claude Desktop o
    Kiro, el CLI, o el usuario editando el JSON a mano. Hasta ahora el wizard solo
    veia lo que el mismo habia guardado, asi que un cambio hecho por fuera quedaba
    invisible hasta recargar la pagina entera.

    Se devuelve `(mtime_ns, size)` de cada documento en vez de leer y hashear su
    contenido: alcanza para saber que algo cambio y cuesta un `stat` por archivo,
    que es lo que permite consultarlo cada pocos segundos sin costo. Un documento
    ausente cuenta como `null`, de modo que crearlo tambien es un cambio.
    """
    project = project_root()
    docs: dict[str, object] = {}
    for doc in _STATE_DOCS:
        ruta = contracts._doc_path(project, doc)
        try:
            st = ruta.stat()
        except OSError:
            docs[doc] = None
        else:
            docs[doc] = {"mtime": st.st_mtime_ns, "size": st.st_size}
    return {"docs": docs}


@app.get("/api/project")
def get_project() -> dict:
    """Describe el proyecto sobre el que opera el wizard, para la pantalla de inicio.

    El wizard trabaja siempre sobre UN proyecto, resuelto por `project_root()`
    desde `PURIQ_PROJECT` o el directorio actual. Hasta ahora eso no se mostraba
    en ninguna parte: quien abria `puriq init` sin conocer esa regla podia estar
    cargando datos en una carpeta que no eligio y no tenia como notarlo. Este
    endpoint expone la ruta y si el proyecto ya tiene contrato en disco, para que
    la primera pantalla lo diga antes de que el usuario escriba nada.

    Devuelve:
      - `path`/`name`: ruta absoluta y nombre de la carpeta.
      - `isNew`: True si no hay NINGUN documento del contrato escrito todavia.
      - `summary`: conteos de lo ya cargado (vacio si `isNew`).
      - `chat`: si el chat conversacional tiene un motor de LLM configurado, y
        cual. Son banderas y el nombre del modo; nunca el valor de una clave.
    """
    project = project_root()
    docs_en_disco = [
        doc for doc in _STATE_DOCS if contracts._doc_path(project, doc).exists()
    ]
    tourism = contracts._load_contract(project, _TOURISM_DOC)
    config_doc = contracts._load_contract(project, _SITE_CONFIG_DOC)
    site = tourism.get("site") if isinstance(tourism.get("site"), dict) else {}
    modules = config_doc.get("modules") if isinstance(config_doc.get("modules"), dict) else {}

    # Motor del chat: se informa SOLO si esta configurado y con que modo, para
    # que la pantalla de inicio ofrezca la via conversacional cuando sirve y la
    # explique cuando no. Nunca se expone el valor de una credencial.
    modo = (get_env("PURIQ_LLM_MODE") or "bedrock").strip().lower()
    if modo == "openai":
        listo = bool(get_env("PURIQ_OPENAI_API_KEY", secret=True))
    elif modo == "local":
        listo = False  # Ollama es text-only: no admite tool-use, no sirve para el chat.
    else:
        listo = bool(get_env("AWS_REGION") or get_env("AWS_DEFAULT_REGION"))

    return redact_value(
        {
            "path": str(project),
            "name": project.name,
            "isNew": not docs_en_disco,
            "summary": {
                "siteName": site.get("name") or "",
                "region": site.get("region") or "",
                "places": len(tourism.get("places") or []),
                "events": len(tourism.get("events") or []),
                "modules": len(modules),
            },
            "chat": {"ready": listo, "mode": modo},
        }
    )


@app.get("/api/state")
def get_state() -> dict:
    """Devuelve los 3 contratos existentes (o defaults) para prellenar la UI.

    Carga `tourism-data`, `site-config` y `theme-tokens` con la capa de contrato
    (`_load_contract`, que usa carga tolerante/estricta segun el documento y cae
    en un documento base minimo si el archivo no existe, Req 1.5/11.1). Aplica
    `config.redact` de forma recursiva a la respuesta para que ningun valor de
    secreto se filtre (Req 12.2).
    """
    project = project_root()
    state = {doc: contracts._load_contract(project, doc) for doc in _STATE_DOCS}
    return redact_value(state)


# --- Intake de contenido turistico: sitio, Places y Events (Req 3) -----------
#
# Las tres rutas siguen el patron load -> merge -> save (DD-1): se parte del
# documento existente (`_load_contract`), se fusiona el parche del formulario de
# forma no destructiva (`merge_document`) y solo si el resultado valida contra el
# esquema se escribe (`save_contract`, validate-before-write). Ante un error de
# coordenada/validacion se responde `422` con un cuerpo redactado y accionable
# (`wizard_error_response`), sin persistir nada. Los constructores puros de
# `wizard/intake.py` (`build_place`/`build_event`/`make_coords`) derivan el `id`
# slug y validan el rango de coordenadas; el wizard aqui solo cablea la E/S.

# Excepciones que se traducen a `422` en el intake (todas de validacion/entrada).
# `CoordinateRangeError` es subclase de `ValueError`; se lista aparte por claridad.
_INTAKE_ERRORS = (CoordinateRangeError, ValueError, jsonschema.ValidationError)


class _Center(BaseModel):
    """Centro del mapa del sitio (`Tourism_Data.site.center`), un `coords`."""

    lat: float
    lng: float
    zoom: int | None = None


class SiteBody(BaseModel):
    """Cuerpo de `PUT /api/tourism-data/site` (Req 3.1)."""

    name: str
    region: str
    defaultLocale: str = "es"
    center: _Center


class PlaceBody(BaseModel):
    """Cuerpo de `POST /api/tourism-data/places` (Req 3.2, 3.4-3.6)."""

    name: str
    category: str
    lat: float | None = None
    lng: float | None = None
    zoom: int | None = None
    address: str | None = None


class EventBody(BaseModel):
    """Cuerpo de `POST /api/tourism-data/events` (Req 3.3)."""

    name: str
    startDate: str
    endDate: str | None = None
    placeId: str | None = None
    description: str | None = None
    recurring: str | None = None


def _save_tourism_patch(project: Path, patch: dict) -> dict:
    """Aplica el patron load -> merge -> save sobre `tourism-data` (DD-1).

    Carga el documento existente (o su base minima), fusiona `patch` de forma no
    destructiva y valida-antes-de-escribir. Devuelve el documento fusionado ya
    persistido. Propaga `ValueError`/`ValidationError` si la validacion falla
    (el llamador los mapea a `422`).
    """
    base = contracts._load_contract(project, _TOURISM_DOC)
    merged = contracts.merge_document(base, patch)
    contracts.save_contract(project, _TOURISM_DOC, merged)
    return merged


@app.put("/api/tourism-data/site")
def put_site(body: SiteBody):
    """Guarda los datos de sitio (nombre, region, locale, centro) (Req 3.1).

    Valida el rango del centro con `make_coords` (Req 3.5, 3.6) y persiste via
    load-merge-save. Ante error de coordenada/validacion responde `422` con un
    cuerpo redactado y accionable, sin escribir nada (Req 3.7, 7.1, 7.2).
    """
    project = project_root()
    try:
        center = make_coords(body.center.lat, body.center.lng, body.center.zoom)
        patch = {
            "site": {
                "name": body.name,
                "region": body.region,
                "defaultLocale": body.defaultLocale,
                "center": center,
            }
        }
        merged = _save_tourism_patch(project, patch)
    except _INTAKE_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_TOURISM_FILE),
        )
    return redact_value(merged)


@app.post("/api/tourism-data/places")
def add_place(body: PlaceBody):
    """Anexa un Place con `id` slug validando coords/direccion (Req 3.2, 3.4-3.6).

    Reusa el constructor puro `build_place` (deriva `id = slugify(name)`, valida
    el rango de coordenadas y conserva `address` sin inventar `coords`). El
    `merge_document` anexa por `id` sin borrar Places previos (Req 11.2). Ante
    error responde `422` redactado sin persistir (Req 3.7).
    """
    project = project_root()
    try:
        place = build_place(
            body.name,
            body.category,
            lat=body.lat,
            lng=body.lng,
            zoom=body.zoom,
            address=body.address,
        )
        merged = _save_tourism_patch(project, {"places": [place]})
    except _INTAKE_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_TOURISM_FILE),
        )
    return redact_value(merged)


@app.post("/api/tourism-data/events")
def add_event(body: EventBody):
    """Anexa un Event con `id` slug via load-merge-save (Req 3.3, 11.2).

    Reusa el constructor puro `build_event` (deriva `id = slugify(name)`). El
    `merge_document` anexa por `id` sin borrar Events previos. Ante error de
    validacion responde `422` redactado sin persistir (Req 3.7).
    """
    project = project_root()
    try:
        event = build_event(
            body.name,
            body.startDate,
            end_date=body.endDate,
            place_id=body.placeId,
            description=body.description,
            recurring=body.recurring,
        )
        merged = _save_tourism_patch(project, {"events": [event]})
    except _INTAKE_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_TOURISM_FILE),
        )
    return redact_value(merged)


# --- Estructura (modulos + deploy.target) y marca (theme tokens) -------------
#
# Ambos endpoints reusan el mismo patron load -> merge -> save (DD-1) que el
# intake, pero sobre los documentos *estrictos* del contrato (`site-config` y
# `theme-tokens`, que `_load_contract` carga y `save_contract` valida con el
# esquema completo). El wizard solo construye el parche con los constructores
# puros (`build_modules`) y validadores (`validate_deploy_target`); la validacion
# final contra el esquema (colores hex, order>=1, claves del catalogo, etc.) la
# hace `save_contract` antes de escribir (validate-before-write, Req 2.4/2.5/6.5).

# Errores de validacion/entrada que se traducen a `422` en estos endpoints.
# `ModuleCatalogError` y `DeployTargetError` son subclases de `ValueError`; se
# listan aparte por claridad de intencion.
_CONFIG_ERRORS = (
    ModuleCatalogError,
    DeployTargetError,
    DomainError,
    LandingCatalogError,
    ValueError,
    jsonschema.ValidationError,
)


def _save_patch(project: Path, doc: str, patch: dict) -> dict:
    """Aplica load -> merge -> save sobre un documento del contrato (DD-1).

    Carga el documento existente (o su base minima), fusiona `patch` de forma no
    destructiva (`merge_document`) y valida-antes-de-escribir (`save_contract`).
    Devuelve el documento fusionado ya persistido. Propaga
    `ValueError`/`ValidationError` si la validacion contra el esquema falla (el
    llamador los mapea a `422`).
    """
    base = contracts._load_contract(project, doc)
    merged = contracts.merge_document(base, patch)
    contracts.save_contract(project, doc, merged)
    return merged


class _ModuleSelection(BaseModel):
    """Descriptor de un modulo en la seleccion de `PUT /api/site-config`.

    Espeja la entrada del constructor puro `build_modules`: `key` del catalogo
    (`map`/`places`/`events`/`blog`/`chatweb`), `enabled` on/off y, solo para
    `chatweb`, los campos extra `persona`/`knowledgeSource`. El `order` no se
    envia: lo deriva `build_modules` a partir del orden de la lista (Req 2.2).
    """

    key: str
    enabled: bool = True
    # Etiqueta de la navegacion del sitio. Si no se envia, `build_modules` aplica
    # el default legible del catalogo (nunca la clave cruda).
    label: str | None = None
    persona: str | None = None
    knowledgeSource: str | None = None


class _LandingSelection(BaseModel):
    """Descriptor de una Landing_Section en la seleccion de `PUT /api/site-config`.

    Espeja la entrada del constructor puro `build_landing`: `type` del catalogo
    de portada (`hero`/`features`/`cta`/`gallery`/`stats`), `enabled` on/off y un
    `content` opcional con los campos de copy por tipo (titular, subtitulo,
    destacados, mensaje, etiqueta de CTA, etc.). El `order` no se envia: lo
    deriva `build_landing` a partir de la posicion en la lista ordenada (Req 14.2).
    """

    type: str
    enabled: bool = True
    content: dict | None = None


class _Contact(BaseModel):
    """Datos de contacto publicables del organismo (`Site_Config.contact`)."""

    email: str | None = None
    phone: str | None = None


class SiteConfigBody(BaseModel):
    """Cuerpo de `PUT /api/site-config` (Req 2.1-2.5, 10.5, 14.3, 14.4, 14.6).

    `modules` es la seleccion **ordenada** de modulos (el orden de la lista fija
    el `order`); `deployTarget` es el destino de publicacion opcional que se
    valida contra el catalogo y se persiste en `Site_Config.deploy.target`;
    `landing` es la seleccion **ordenada** de secciones de portada (el orden de
    la lista fija el `order`) que el Wizard compone a partir de secciones
    pre-construidas del Landing_Module, sin generar codigo (Req 14.6).
    """

    modules: list[_ModuleSelection]
    deployTarget: str | None = None
    landing: list[_LandingSelection] | None = None
    # Direccion web publica (`Site_Config.deploy.domain`). No es solo del paso de
    # publicacion: la resuelve el BUILD para la URL canonica, las etiquetas de
    # redes y el sitemap. Sin ella el sitio se genera con URLs de marcador.
    domain: str | None = None
    # Datos de contacto del organismo (`Site_Config.contact`), que el pie del
    # sitio publica como enlaces mailto/tel.
    contact: _Contact | None = None


class _Colors(BaseModel):
    """Colores de marca (`Theme_Tokens.colors`); el esquema exige formato hex."""

    primary: str | None = None
    secondary: str | None = None
    background: str | None = None
    text: str | None = None
    accent: str | None = None


class _Typography(BaseModel):
    """Tipografia de marca (`Theme_Tokens.typography`)."""

    headingFont: str | None = None
    bodyFont: str | None = None
    baseSize: str | None = None


class ThemeBody(BaseModel):
    """Cuerpo de `PUT /api/theme-tokens` (Req 6.1-6.5).

    Campos opcionales para permitir parches por paso (el merge conserva lo no
    tocado): `colors`, `typography`, `tone` (se escribe en `voice.tone`) y
    `logo` (ruta relativa del Asset dentro de `/assets`).
    """

    colors: _Colors | None = None
    typography: _Typography | None = None
    tone: str | None = None
    logo: str | None = None


@app.put("/api/site-config")
def put_site_config(body: SiteConfigBody):
    """Guarda seleccion/orden de modulos y `deploy.target` (Req 2.1-2.5, 10.5).

    Construye el sub-documento `modules` con el constructor puro `build_modules`
    (restringe al catalogo `map/places/events/blog/chatweb` y asigna `order`
    entero >= 1 segun el orden recibido, Req 2.2, 2.3). Si viene `deployTarget`,
    lo valida contra el catalogo soportado (`validate_deploy_target`, Req 10.2) y
    lo persiste en `Site_Config.deploy.target` (Req 10.5). Si viene `landing`,
    construye el sub-documento de portada con el constructor puro `build_landing`
    (restringe `type` al catalogo `hero/features/cta/gallery/stats` y asigna
    `order` entero >= 1 segun el orden recibido, Req 14.2, 14.3) componiendo
    secciones pre-construidas sin generar codigo (Req 14.6). Escribe via
    load-merge-save con validacion estricta contra `site-config.schema.json`
    (Req 2.4, 2.5, 14.4); ante error responde `422` nombrando el campo, sin
    persistir nada.
    """
    project = project_root()
    try:
        selection = [descriptor.model_dump(exclude_none=True) for descriptor in body.modules]
        patch: dict = {"modules": build_modules(selection)}
        # `deploy` reune destino y dominio; se arma un solo sub-documento para no
        # pisar uno con el otro cuando llegan en peticiones distintas (el destino
        # se elige al publicar y el dominio en los datos del sitio).
        deploy: dict = {}
        if body.deployTarget is not None:
            deploy["target"] = validate_deploy_target(body.deployTarget)
        if body.domain is not None:
            dominio = validate_domain(body.domain)
            # Un dominio vacio se guarda como cadena vacia a proposito: permite
            # BORRAR una direccion cargada por error, en vez de dejarla fija.
            deploy["domain"] = dominio
        if deploy:
            patch["deploy"] = deploy
        if body.contact is not None:
            contacto = body.contact.model_dump(exclude_none=True)
            if contacto:
                patch["contact"] = contacto
        if body.landing is not None:
            landing_selection = [
                descriptor.model_dump(exclude_none=True) for descriptor in body.landing
            ]
            patch["landing"] = build_landing(landing_selection)
        merged = _save_patch(project, _SITE_CONFIG_DOC, patch)
    except _CONFIG_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_SITE_CONFIG_FILE),
        )
    return redact_value(merged)


@app.put("/api/theme-tokens")
def put_theme_tokens(body: ThemeBody):
    """Guarda colores, tipografia, tono de voz y logo de marca (Req 6.1-6.5).

    Arma un parche solo con los campos provistos (el merge conserva lo no tocado)
    y lo escribe via load-merge-save con validacion estricta contra
    `theme-tokens.schema.json` (Req 6.5). Un color que no cumple el patron hex es
    rechazado por el esquema y se traduce a `422` con el formato esperado (Req 6.4),
    sin persistir nada (validate-before-write).
    """
    project = project_root()
    patch: dict = {}
    if body.colors is not None:
        colors = body.colors.model_dump(exclude_none=True)
        if colors:
            patch["colors"] = colors
    if body.typography is not None:
        typography = body.typography.model_dump(exclude_none=True)
        if typography:
            patch["typography"] = typography
    if body.tone is not None:
        patch["voice"] = {"tone": body.tone}
    if body.logo is not None:
        patch["logo"] = body.logo

    try:
        merged = _save_patch(project, _THEME_DOC, patch)
    except _CONFIG_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_THEME_FILE),
        )
    return redact_value(merged)


# --- Carga de Assets a /assets (Req 4) ---------------------------------------
#
# `POST /api/assets` recibe un archivo (multipart) y lo almacena de forma segura
# dentro de `<project>/assets` (DD-3): valida extension/tipo (Req 4.4) y tamano
# contra `MAX_ASSET_BYTES` (Req 4.5), normaliza el nombre a Slug con
# `normalize_asset_name` (Req 4.6), desambigua colisiones con sufijo numerico
# conservando los Assets previos (Req 4.6, 11.4) y verifica la contencion en el
# arbol con `resolve_within_assets` antes de escribir (Req 12.4). Devuelve la
# ruta relativa del Asset (Req 4.1) y, opcionalmente, la enlaza a las `images`
# de un Place/Event (Req 4.2) o a `Theme_Tokens.logo` (Req 4.3) via
# load-merge-save. Errores de validacion/entrada -> `422` redactado.

# Errores de validacion/entrada que se traducen a `422` en la carga de Assets.
_ASSET_ERRORS = (ValueError, jsonschema.ValidationError)


# `_next_available_asset` y `_append_image` se movieron a `wizard/asset_store.py`
# (modulo de E/S sin FastAPI, DD-3) y se importan arriba como
# `next_available_asset` y `append_image`.


@app.post("/api/assets")
async def upload_asset(
    file: UploadFile = File(...),
    target: str | None = Form(default=None),
    id: str | None = Form(default=None),
):
    """Sube un Asset a `<project>/assets` y devuelve su ruta relativa (Req 4.1-4.6, 12.4).

    Valida el tipo/extension con `normalize_asset_name` (Req 4.4) y el tamano
    contra `MAX_ASSET_BYTES` (Req 4.5); normaliza el nombre a Slug (Req 4.6);
    desambigua colisiones conservando los Assets previos (Req 4.6, 11.4) y
    verifica la contencion con `resolve_within_assets` (Req 12.4) antes de
    escribir. Segun `target`, enlaza la ruta relativa a `images` de un Place
    (`target='place'`) o Event (`target='event'`) por `id` (Req 4.2), o a
    `Theme_Tokens.logo` (`target='logo'`) via load-merge-save (Req 4.3). Ante
    error de validacion/entrada responde `422` redactado sin dejar basura.
    """
    project = project_root()
    try:
        contenido = await file.read()

        # Tamano: se compara antes de tocar disco (Req 4.5).
        if len(contenido) > MAX_ASSET_BYTES:
            raise ValueError(
                f"El archivo supera el tamano maximo permitido de "
                f"{_MAX_ASSET_MB} MB."
            )

        # Tipo/extension + normalizacion a Slug (Req 4.4, 4.6). Extension no
        # soportada -> ValueError que lista los formatos aceptados.
        nombre = normalize_asset_name(file.filename or "", IMAGE_EXTS)

        # Desambiguacion de colision + verificacion de contencion (Req 4.6, 12.4).
        nombre_final, destino = next_available_asset(project, nombre)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)

        rel_path = f"assets/{nombre_final}"

        # Enlace opcional al contrato (Req 4.2, 4.3).
        contrato = None
        if target == "logo":
            contrato = _save_patch(project, _THEME_DOC, {"logo": rel_path})
        elif target in ("place", "event"):
            if not id:
                raise ValueError(
                    f"Para asociar la imagen a un {target} se requiere el 'id' "
                    f"de la entrada."
                )
            entity_key = "places" if target == "place" else "events"
            contrato = append_image(project, entity_key, id, rel_path)
    except _ASSET_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc),
        )

    respuesta: dict = {"path": rel_path}
    if contrato is not None:
        respuesta["document"] = contrato
    return redact_value(respuesta)


# --- Captura de la base de conocimiento Q&A (Req 5) --------------------------
#
# `POST /api/qa` valida que pregunta y respuesta no esten vacias con
# `validate_qa_entry` (Req 5.4), anexa el QA_Entry a `content/qa.json` SIN
# indexarlo ni consumirlo (Req 5.1, 5.3) y registra la ruta de la base de
# conocimiento en `Site_Config.modules.chatweb.knowledgeSource` via
# load-merge-save (Req 5.2). El registro crea el modulo `chatweb` con
# `enabled`/`order` minimos si aun no existe, para no producir un `site-config`
# invalido contra el esquema.

# Errores que se traducen a `422` en la captura de Q&A.
_QA_ERRORS = (QAValidationError, ValueError, jsonschema.ValidationError)


class QABody(BaseModel):
    """Cuerpo de `POST /api/qa` (Req 5.1, 5.4): un par pregunta/respuesta."""

    question: str
    answer: str


# `_append_qa_entry` y `_register_knowledge_source` se movieron a
# `wizard/qa_store.py` (modulo de E/S sin FastAPI, DD-4) y se importan arriba
# como `append_qa_entry` y `register_knowledge_source`.


@app.post("/api/qa")
def add_qa(body: QABody):
    """Guarda un QA_Entry en `/content` y registra su knowledgeSource (Req 5.1-5.4).

    Valida que pregunta y respuesta no esten vacias con `validate_qa_entry`
    (Req 5.4); ante campo vacio responde `422` que nombra el campo faltante, sin
    almacenar nada. En caso valido anexa la entrada (recortada) a
    `content/qa.json` sin indexarla ni consumirla (Req 5.1, 5.3) y registra
    `content/qa.json` en `Site_Config.modules.chatweb.knowledgeSource` via
    load-merge-save (Req 5.2).
    """
    project = project_root()
    try:
        entry = validate_qa_entry(body.model_dump())
        append_qa_entry(project, entry)
        site_config = register_knowledge_source(project, _QA_RELPATH)
    except _QA_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_QA_FILENAME),
        )

    return redact_value(
        {
            "entry": entry,
            "knowledgeSource": _QA_RELPATH,
            "document": site_config,
        }
    )


@app.get("/api/qa")
def list_qa() -> dict:
    """Lista los QA_Entry ya guardados en `content/qa.json`.

    Contrapartida de lectura de `POST /api/qa`: la UI necesita mostrar lo que el
    usuario lleva cargado (y poder borrarlo) en vez de escribir a ciegas. Es
    tolerante a la ausencia del archivo y a un contenido corrupto: en ambos casos
    devuelve una lista vacia en lugar de fallar, igual que `_append_qa_entry`.
    Las entradas se devuelven con su `index` (posicion en el archivo), que es lo
    que consume `DELETE /api/qa/{index}`.
    """
    qa_path = project_root() / _CONTENT_DIRNAME / _QA_FILENAME
    entries: list = []
    if qa_path.exists():
        try:
            cargado = json.loads(qa_path.read_text(encoding="utf-8"))
            if isinstance(cargado, list):
                entries = cargado
        except (ValueError, OSError):
            entries = []

    salida = [
        {
            "index": i,
            "question": e.get("question", ""),
            "answer": e.get("answer", ""),
        }
        for i, e in enumerate(entries)
        if isinstance(e, dict)
    ]
    return redact_value({"entries": salida})


@app.delete("/api/qa/{index}")
def delete_qa(index: int):
    """Elimina el QA_Entry en la posicion `index` de `content/qa.json`.

    Se borra por posicion (y no por texto de la pregunta) porque el archivo es
    una lista simple sin ids y admite preguntas repetidas. Un `index` fuera de
    rango responde `422` accionable sin tocar el archivo. La escritura conserva
    el resto de las entradas (Req 5.1: los Q&A se anexan y no se pisan).
    """
    project = project_root()
    qa_path = project / _CONTENT_DIRNAME / _QA_FILENAME

    entries: list = []
    if qa_path.exists():
        try:
            cargado = json.loads(qa_path.read_text(encoding="utf-8"))
            if isinstance(cargado, list):
                entries = cargado
        except (ValueError, OSError):
            entries = []

    if index < 0 or index >= len(entries):
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(
                ValueError(
                    f"No existe una entrada Q&A en la posicion {index}. "
                    f"Actualiza la lista y volve a intentar."
                ),
                documento=_QA_FILENAME,
            ),
        )

    entries.pop(index)
    qa_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return redact_value({"deleted": index, "remaining": len(entries)})


# --- Lectura y baja de Places / Events del contrato --------------------------
#
# El intake (`POST`) ya existia, pero sin contrapartida de edicion ni de baja: el
# usuario cargaba a ciegas y no podia corregir un dato mal tipeado. Estos
# endpoints delegan en `puriq.core.Puriq`, que es el mismo punto de orquestacion
# que usan el CLI y el MCP (Req 11.3): `edit` fusiona campos y `delete` mantiene
# la integridad referencial (limpia el `placeId` colgante de los Events cuando se
# borra un Place). Ambos persisten como **draft** (coords opcional), coherente con
# DD-1: un Place recien editado puede quedarse con `address` y sin `coords` hasta
# que `geocode` corra en el build.

# Mapa de la entidad de la URL al `kind` que entiende el contrato.
_ENTITY_KEYS = {"places": "places", "events": "events"}

# Errores de las tools de contenido que se traducen a `422` redactado.
_CONTENT_ERRORS = (KeyError, ValueError, jsonschema.ValidationError)


class EntityPatch(BaseModel):
    """Cuerpo de `PUT /api/tourism-data/{entity}/{id}`: campos a fusionar.

    Se aceptan solo los campos editables desde la UI. Los ausentes (`None`) no se
    tocan, de modo que la edicion sea un merge no destructivo y no borre datos que
    el formulario no muestra (p. ej. `images` cargadas en el paso de Recursos).
    """

    name: str | None = None
    category: str | None = None
    address: str | None = None
    shortDescription: str | None = None
    description: str | None = None
    hours: str | None = None
    startDate: str | None = None
    endDate: str | None = None
    placeId: str | None = None
    recurring: str | None = None


@app.put("/api/tourism-data/{entity}/{item_id}")
def edit_entity(entity: str, item_id: str, body: EntityPatch):
    """Edita (merge) un Place o Event por `id` y devuelve el contrato actualizado.

    Delega en `Puriq.edit`, que reusa la tool pura `edit_content.edit` y persiste
    como draft. Solo se envian los campos no nulos, para no pisar con `null` lo
    que el formulario no edita. Un `entity` fuera de `places`/`events` o un `id`
    inexistente responden `422` redactado sin modificar nada.
    """
    if entity not in _ENTITY_KEYS:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(
                ValueError(
                    f"Entidad '{entity}' no soportada. Usa 'places' o 'events'."
                )
            ),
        )

    fields = body.model_dump(exclude_none=True)
    if not fields:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(
                ValueError("No se indico ningun campo para editar.")
            ),
        )

    project = project_root()
    try:
        Puriq(project).edit(item_id, fields)
    except _CONTENT_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_TOURISM_FILE),
        )

    return redact_value(
        {"id": item_id, "document": contracts._load_contract(project, _TOURISM_DOC)}
    )


@app.delete("/api/tourism-data/{entity}/{item_id}")
def delete_entity(entity: str, item_id: str):
    """Elimina un Place o Event por `id` y devuelve el contrato actualizado.

    Delega en `Puriq.delete`, que reusa `delete_content.delete`: al borrar un
    Place, limpia el `placeId` colgante de los Events que lo referenciaban y
    devuelve esos ids en `affectedEvents` para que la UI pueda avisarlo. Un `id`
    inexistente responde `422` redactado sin modificar nada.

    Nota: las imagenes asociadas quedan en `/assets`. La baja del contrato no
    borra archivos del disco (eso lo hace `DELETE /api/assets/{name}`), asi una
    foto subida sigue disponible para reasignarla a otra entrada.
    """
    if entity not in _ENTITY_KEYS:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(
                ValueError(
                    f"Entidad '{entity}' no soportada. Usa 'places' o 'events'."
                )
            ),
        )

    project = project_root()
    try:
        resultado = Puriq(project).delete(item_id)
    except _CONTENT_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_TOURISM_FILE),
        )

    return redact_value(
        {
            "id": resultado["id"],
            "affectedEvents": resultado["affectedEvents"],
            "document": contracts._load_contract(project, _TOURISM_DOC),
        }
    )


# --- Inventario de Assets ----------------------------------------------------
#
# La carga de Assets ya existia, pero sin forma de VER lo subido: el usuario no
# tenia miniaturas ni sabia que fotos llevaba. Estos endpoints exponen el
# inventario de `<project>/assets`, sirven los bytes para las miniaturas y
# permiten dar de baja un archivo. Todo acceso por nombre pasa por
# `resolve_within_assets`, que garantiza la contencion en el arbol y rechaza el
# path traversal (Req 12.4).


def _asset_usage(project: Path) -> dict[str, list[str]]:
    """Mapea cada ruta `assets/<archivo>` a los ids del contrato que la usan.

    Recorre `images` de Places y Events del `tourism-data` y el `logo` de
    `theme-tokens`. Sirve para que la UI muestre a que entrada pertenece cada
    foto y para advertir antes de borrar un Asset en uso.
    """
    uso: dict[str, list[str]] = {}

    data = contracts._load_contract(project, _TOURISM_DOC)
    for clave in ("places", "events"):
        for item in data.get(clave) or []:
            if not isinstance(item, dict):
                continue
            for ruta in item.get("images") or []:
                uso.setdefault(ruta, []).append(str(item.get("id", "")))

    theme = contracts._load_contract(project, _THEME_DOC)
    logo = theme.get("logo")
    if isinstance(logo, str) and logo:
        uso.setdefault(logo, []).append("logo")

    return uso


@app.get("/api/assets")
def list_assets() -> dict:
    """Lista los Assets de `<project>/assets` con su tamano y quien los usa.

    Devuelve, por archivo: la ruta relativa del contrato (`assets/<nombre>`), el
    nombre, el tamano en bytes y `usedBy` (ids de Places/Events que lo
    referencian, mas `logo` si es el logo de marca). Tolerante a la ausencia del
    directorio: sin `/assets` devuelve una lista vacia. El orden es alfabetico
    para que el inventario sea estable entre recargas.
    """
    project = project_root()
    assets_dir = project / "assets"
    if not assets_dir.is_dir():
        return {"assets": []}

    uso = _asset_usage(project)
    salida = []
    for archivo in sorted(assets_dir.iterdir()):
        if not archivo.is_file() or archivo.name.startswith("."):
            continue
        rel = f"assets/{archivo.name}"
        salida.append(
            {
                "path": rel,
                "name": archivo.name,
                "bytes": archivo.stat().st_size,
                "usedBy": uso.get(rel, []),
            }
        )
    return redact_value({"assets": salida})


@app.get("/api/assets/raw/{name}")
def get_asset_bytes(name: str):
    """Sirve los bytes de un Asset para que la UI pueda mostrar la miniatura.

    La ruta se resuelve con `resolve_within_assets`, que rechaza cualquier nombre
    que escape de `<project>/assets` (path traversal, Req 12.4). Un archivo
    inexistente responde `404`. Se sirve desde el propio wizard porque la raiz del
    proyecto es dinamica (`PURIQ_PROJECT`) y no se puede montar como estatico en
    tiempo de import.
    """
    from fastapi.responses import FileResponse

    try:
        destino = resolve_within_assets(project_root(), name)
    except ValueError as exc:
        return JSONResponse(status_code=422, content=wizard_error_response(exc))

    if not destino.is_file():
        return JSONResponse(
            status_code=404,
            content=wizard_error_response(
                FileNotFoundError(f"No existe el recurso '{name}' en /assets.")
            ),
        )
    return FileResponse(destino)


@app.delete("/api/assets/{name}")
def delete_asset(name: str):
    """Borra un Asset del disco y quita sus referencias del contrato.

    Ademas de eliminar el archivo (contenido verificado con
    `resolve_within_assets`, Req 12.4), depura la ruta de las `images` de todo
    Place/Event que la referenciaba, para no dejar en el contrato una imagen
    colgante que el sitio renderizaria como rota. Si el Asset era el `logo` de
    marca, tambien se limpia ese campo.
    """
    project = project_root()
    try:
        destino = resolve_within_assets(project, name)
    except ValueError as exc:
        return JSONResponse(status_code=422, content=wizard_error_response(exc))

    if not destino.is_file():
        return JSONResponse(
            status_code=404,
            content=wizard_error_response(
                FileNotFoundError(f"No existe el recurso '{name}' en /assets.")
            ),
        )

    rel = f"assets/{destino.name}"
    destino.unlink()

    # Depuracion de referencias en tourism-data (images de Places/Events).
    data = contracts._load_contract(project, _TOURISM_DOC)
    cambiado = False
    for clave in ("places", "events"):
        for item in data.get(clave) or []:
            if not isinstance(item, dict):
                continue
            images = item.get("images")
            if isinstance(images, list) and rel in images:
                item["images"] = [i for i in images if i != rel]
                cambiado = True
    if cambiado:
        # Persistencia como draft: un Place puede seguir sin `coords` hasta el
        # geocode del build, igual que en el resto del intake (DD-1).
        from puriq.tools import _persist

        _persist.save_tourism_draft(data, project / _TOURISM_FILE)

    # Depuracion del logo de marca si apuntaba a este Asset.
    theme = contracts._load_contract(project, _THEME_DOC)
    if theme.get("logo") == rel:
        theme.pop("logo", None)
        contracts.save_contract(project, _THEME_DOC, theme)

    return redact_value({"deleted": rel, "unlinked": cambiado})


# --- Generacion con progreso en vivo (Req 8, DD-2) ---------------------------
#
# `WS /ws/build` dispara la generacion del sitio (`Puriq.build`, opcionalmente
# precedida de `Puriq.collect`) y transmite cada hito de `Build_Progress` al
# navegador (Req 8.2). El pipeline del core es **sincrono y bloqueante**; para no
# congelar el event loop de FastAPI se ejecuta en un hilo (`asyncio.to_thread`) y
# el progreso se comunica via una `queue.Queue` que el consumidor asincrono drena
# y reenvia por el socket (DD-2). El wizard delega en el core y **nunca**
# reimplementa las tools (Req 8.5).
#
# Secuencia (DD-2): se maneja `build(progress=cb)` como caso base, ya que
# `build` carga el `tourism-data.json` persistido, geocodifica, valida, enriquece
# (si `use_llm`) y ensambla el sitio a partir del contrato ya intake-ado por los
# pasos previos del wizard. Solo si el cliente aporta un directorio de recursos
# crudos (`resources`) se ejecuta antes `collect(...)` para escanear/enriquecer y
# regenerar el contrato; asi el flujo normal del wizard (que ya persistio los 3
# JSON via los endpoints REST) no exige recursos en disco. El callback `progress`
# encola `config.redact(msg)` para que ningun secreto viaje por el socket (Req 12.2).

# Intervalo de sondeo de la cola de progreso mientras el build corre en el hilo.
# Un valor pequeno mantiene el progreso fluido sin ocupar la CPU en busy-wait.
_WS_POLL_SECONDS = 0.05


# Mensajes de progreso (en espanol) por documento del contrato que se
# materializa con su default cuando el paso opcional del wizard se salto. Se
# emiten por el stream del WebSocket para que el usuario sepa que se uso un
# valor por defecto en vez de fallar el build (Req 1.5, 8.2).
_DEFAULT_DOC_MESSAGES: dict[str, str] = {
    "theme-tokens": "Marca no configurada: usando tema por defecto.",
    "site-config": "Estructura no configurada: usando modulos por defecto.",
    "tourism-data": "Contenido no configurado: usando datos por defecto.",
}


def _ensure_contract_defaults(project: Path, progress) -> None:
    """Materializa en disco los documentos del contrato que aun no existen (Req 1.5).

    El build del core (`Puriq.build`) carga los 3 JSON del contrato de forma
    estricta desde disco y falla si alguno falta. En el wizard, cada documento
    solo se escribe cuando el usuario envia el formulario de su paso; si el
    usuario **salta** un paso opcional (p. ej. Marca), el archivo nunca se crea y
    el build reventaria con un FileNotFoundError. Para que el wizard sea tolerante
    a pasos salteados, aqui se recorren los 3 documentos y, **solo si el archivo
    falta**, se escribe su documento base valido (`contracts._base_document`) via
    la capa de contrato (load->validate->save, DD-1): nunca se escribe un JSON a
    mano ni se sobreescribe un archivo existente.

    Cada default materializado emite un hito de progreso en espanol via el
    callback `progress` (el mismo `emitir_progreso` del WebSocket, que ya redacta
    con `config.redact`, Req 12.2), para que el usuario vea en el stream que se
    uso un valor por defecto. Esta tolerancia vive SOLO en la capa del wizard: el
    `core.build` headless sigue fallando de forma explicita ante un proyecto
    hecho a mano incompleto.
    """
    for doc in ("tourism-data", "site-config", "theme-tokens"):
        if contracts._doc_path(project, doc).exists():
            continue
        base = contracts._base_document(doc)
        contracts.save_contract(project, doc, base)
        progress(_DEFAULT_DOC_MESSAGES[doc])


def _flatten_error_message(error: dict) -> str:
    """Aplana la respuesta de `wizard_error_response` a un unico string (Req 8.4).

    `wizard_error_response` ya viene redactado (Req 12.2). Para un error de
    esquema devuelve ``{documento, campo, sugerencia}``; para el resto,
    ``{causa, accion}``. Aqui se combinan sus partes no vacias en un mensaje
    legible de causa + accion sugerida para emitir por el WebSocket.
    """
    if "causa" in error:
        partes = [error.get("causa"), error.get("accion")]
    else:
        documento = error.get("documento")
        campo = error.get("campo")
        partes = [
            f"Documento: {documento}" if documento else None,
            f"Campo: {campo}" if campo else None,
            error.get("sugerencia"),
        ]
    return " ".join(parte for parte in partes if parte)


@app.websocket("/ws/build")
async def ws_build(ws: WebSocket) -> None:
    """Dispara la generacion en segundo plano y transmite el progreso (Req 8.1-8.5).

    Acepta el socket, lee un mensaje inicial opcional con opciones
    (``{"enrich": bool, "use_llm": bool, "resources": str}``; por defecto
    ``use_llm=True``, ``enrich=False`` y sin `resources`), crea una `queue.Queue`
    y un callback `progress(msg)` que encola `config.redact(msg)`. Lanza
    `collect()` (solo si se dio `resources`) + `build(progress=cb)` en un hilo via
    `asyncio.to_thread` para no bloquear el event loop (Req 8.1, 8.2). Drena la
    cola emitiendo ``{"type":"progress","message":...}`` mientras corre; al
    terminar emite ``{"type":"done","distPath":...}`` (Req 8.3) o, si una fase
    lanza, ``{"type":"error","message": redact(causa+accion)}`` reutilizando
    `wizard_error_response` (Req 8.4). No reimplementa tools: delega en el core
    (Req 8.5).
    """
    await ws.accept()
    project = project_root()

    # Mensaje inicial opcional con las opciones de build. Si el cliente no envia
    # nada valido, se usan los valores por defecto. Un disconnect aqui termina.
    options: dict = {}
    try:
        recibido = await ws.receive_json()
        if isinstance(recibido, dict):
            options = recibido
    except WebSocketDisconnect:
        return
    except Exception:
        # Cuerpo inicial no-JSON o vacio: seguir con los valores por defecto.
        options = {}

    use_llm = bool(options.get("use_llm", True))
    enrich = bool(options.get("enrich", False))
    resources = options.get("resources")

    # Puente sincrono->asincrono: el hilo del build encola hitos redactados; el
    # event loop los drena y reenvia por el socket (DD-2).
    progreso: "queue.Queue[str]" = queue.Queue()

    def emitir_progreso(mensaje: str) -> None:
        progreso.put(config.redact(str(mensaje)))

    def correr_pipeline() -> str:
        proyecto = Puriq(project)
        # collect() solo si el cliente aporto recursos crudos (DD-2); el flujo
        # normal del wizard ya persistio el contrato via los endpoints REST.
        if resources:
            proyecto.collect(
                Path(resources), enrich=enrich, progress=emitir_progreso
            )
        # Tolerancia a pasos salteados: materializar los defaults de los
        # documentos del contrato que aun no existan ANTES del build, para que
        # un paso opcional omitido (p. ej. Marca) no reviente la generacion
        # (Req 1.5). Solo escribe los que faltan y avisa por el stream.
        _ensure_contract_defaults(project, emitir_progreso)
        dist = proyecto.build(use_llm=use_llm, progress=emitir_progreso)
        return str(dist)

    async def drenar_cola() -> None:
        """Envia por el socket todos los hitos encolados hasta el momento."""
        while True:
            try:
                mensaje = progreso.get_nowait()
            except queue.Empty:
                return
            await ws.send_json({"type": "progress", "message": mensaje})

    tarea_build = asyncio.ensure_future(asyncio.to_thread(correr_pipeline))

    try:
        # Bucle de progreso: drena la cola y cede el control hasta que el build
        # (que corre en el hilo) termina.
        while not tarea_build.done():
            await drenar_cola()
            await asyncio.sleep(_WS_POLL_SECONDS)
        # Vaciar los hitos que pudieran quedar tras completarse el build.
        await drenar_cola()

        try:
            dist_path = tarea_build.result()
        except Exception as exc:  # noqa: BLE001 - se traduce y redacta abajo
            error = wizard_error_response(exc)
            await ws.send_json(
                {"type": "error", "message": _flatten_error_message(error)}
            )
        else:
            await ws.send_json(
                {"type": "done", "distPath": config.redact(dist_path)}
            )
    except WebSocketDisconnect:
        # El cliente cerro el socket; el hilo del build se desliga (no se puede
        # cancelar de forma cooperativa) y no se emite nada mas.
        return
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# --- Previsualizacion del sitio construido (Req 9) ---------------------------
#
# `POST /api/preview` expone el sitio ya construido (`<project>/dist`) delegando
# en `core.preview()` (que a su vez usa `build_site.serve`, un servidor estatico
# **bloqueante**). Como `serve()` no retorna hasta que se detiene, se corre en un
# hilo *daemon* para que el endpoint responda de inmediato con el enlace local
# (Req 9.1, 9.3) sin congelar el event loop de FastAPI. Un registro a nivel de
# modulo (`_preview_servers`) evita levantar dos servidores en el mismo puerto:
# si ya hay una preview activa en ese puerto, se devuelve su enlace en vez de
# reintentar el bind (que fallaria). Si no existe `dist/`, se responde con un
# mensaje que pide generar el sitio primero (Req 9.2), sin arrancar nada.

# Puerto por defecto de la preview (coherente con `core.preview`/`build_site.serve`).
_DEFAULT_PREVIEW_PORT = 4322

# Registro de previews activas: puerto -> enlace local. Protegido por un lock
# porque se consulta/actualiza desde el endpoint (event loop) y desde el hilo
# daemon del servidor. Un puerto presente significa "ya hay un serve() corriendo".
_preview_lock = threading.Lock()
_preview_servers: dict[int, str] = {}


class PreviewBody(BaseModel):
    """Cuerpo opcional de `POST /api/preview` (Req 9.1, 9.3): el puerto local."""

    port: int = _DEFAULT_PREVIEW_PORT


@app.post("/api/preview")
def start_preview(body: PreviewBody | None = None) -> dict:
    """Sirve `<project>/dist` en local y devuelve el enlace (Req 9.1-9.3).

    Si `<project>/dist` existe, arranca `Puriq(project).preview(port)` —que es
    bloqueante— en un hilo *daemon* y responde de inmediato con el enlace
    `http://localhost:<port>` (Req 9.1, 9.3). Un registro a nivel de modulo evita
    levantar dos servidores en el mismo puerto: si ya hay una preview activa en
    ese puerto, se reutiliza su enlace. Si no hay build (`dist/` ausente), no se
    arranca nada y se devuelve un mensaje que pide generar el sitio primero
    (Req 9.2).
    """
    project = project_root()
    port = body.port if body is not None else _DEFAULT_PREVIEW_PORT

    dist = project / "dist"
    if not dist.is_dir():
        return redact_value(
            {
                "message": (
                    "Aun no hay un sitio construido para previsualizar. "
                    "Genera el sitio primero (paso Generar) y volve a intentarlo."
                )
            }
        )

    url = f"http://localhost:{port}"
    with _preview_lock:
        ya_activo = port in _preview_servers
        if not ya_activo:
            # Se registra ANTES de arrancar para que peticiones concurrentes al
            # mismo puerto no disparen un segundo serve() (bind duplicado).
            _preview_servers[port] = url

    if not ya_activo:
        def _serve_preview() -> None:
            try:
                Puriq(project).preview(port=port)
            except Exception:
                # Si el servidor no pudo arrancar o termino, liberar el puerto
                # del registro para permitir un reintento posterior.
                with _preview_lock:
                    _preview_servers.pop(port, None)

        hilo = threading.Thread(
            target=_serve_preview, name=f"puriq-preview-{port}", daemon=True
        )
        hilo.start()

    return redact_value({"url": url})


# --- Publicacion del sitio en un destino soportado (Req 10) ------------------
#
# `POST /api/deploy` valida el `target` contra el catalogo (`validate_deploy_target`,
# unica fuente de verdad = adaptadores del core, Req 10.2), exige un build previo
# (`dist/`, Req 10.3), persiste `Site_Config.deploy.target` via load-merge-save
# (Req 10.5) y delega la publicacion en `core.deploy(target)` (Req 10.1), sin
# reimplementar los adaptadores (capa fina). Cualquier fallo de proveedor o de
# credenciales (`DeployError`, `MissingEnvVarError`, errores de boto3/httpx, etc.)
# se traduce a un mensaje accionable **redactado** con `wizard_error_response`
# (Req 10.4, 12.2), de modo que ningun valor de secreto ni traza cruda se filtre.


class DeployBody(BaseModel):
    """Cuerpo de `POST /api/deploy` (Req 10.1, 10.2): el destino de publicacion."""

    target: str


@app.post("/api/deploy")
def start_deploy(body: DeployBody):
    """Publica `<project>/dist` en el destino elegido y devuelve la URL (Req 10.1-10.5).

    Flujo: (1) valida `target` contra el catalogo soportado
    (`validate_deploy_target`, Req 10.2); destino invalido -> `422` que lista los
    validos. (2) Exige un build previo; sin `dist/` responde un mensaje que pide
    generar primero (Req 10.3), sin publicar. (3) Persiste
    `Site_Config.deploy.target` via load-merge-save —destino soportado e intento
    de publicacion— (Req 10.5). (4) Delega en `Puriq(project).deploy(target)`
    (Req 10.1) y devuelve la URL publica. Un fallo del proveedor o de
    credenciales se traduce a un mensaje accionable redactado con
    `wizard_error_response` (Req 10.4), sin exponer secretos ni trazas (Req 12.2).
    """
    project = project_root()

    # (1) Destino soportado (Req 10.2). Invalido -> 422 que lista los validos.
    try:
        target = validate_deploy_target(body.target)
    except _CONFIG_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_SITE_CONFIG_FILE),
        )

    # (2) Exigir un build previo (Req 10.3); sin dist/ no se publica ni persiste.
    dist = project / "dist"
    if not dist.is_dir():
        return redact_value(
            {
                "message": (
                    "Aun no hay un sitio construido para publicar. "
                    "Genera el sitio primero (paso Generar) y volve a intentarlo."
                )
            }
        )

    # (3) Persistir el destino elegido (soportado + intento de publicacion, Req 10.5).
    try:
        site_config = _save_patch(project, _SITE_CONFIG_DOC, {"deploy": {"target": target}})
    except _CONFIG_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc, documento=_SITE_CONFIG_FILE),
        )

    # (4) Publicar delegando en el core (Req 10.1). Cualquier fallo de proveedor,
    # credenciales faltantes o error de red se traduce a un mensaje redactado
    # (Req 10.4, 12.2): se captura de forma amplia para que ni una traza cruda ni
    # un valor de secreto lleguen a la respuesta HTTP.
    try:
        url = Puriq(project).deploy(target)
    except Exception as exc:  # noqa: BLE001 - se traduce y redacta a continuacion
        return JSONResponse(
            status_code=502,
            content=wizard_error_response(exc),
        )

    return redact_value({"url": url, "target": target, "document": site_config})


# --- Chat web conversacional con preview en vivo (Req 6, Pieza 6) ------------
#
# `POST /api/chat` es la **superficie B** (web) del intake: recibe un turno del
# usuario y corre un turno del `Chat_Agent` sobre el Project_Root, devolviendo
# `{respuesta, estado}` redactado (DD-5). El endpoint es una capa fina: NO
# reimplementa el nucleo de intake (las tool-calls se despachan por
# `run_intake_tool` dentro del `Chat_Agent`, Req 5.1) ni valida el contrato.
#
# Acepta DOS formas segun el `Content-Type` (DD-M8, compatible hacia atras):
#   - `application/json` -> `{mensaje, archivos[]}` (Hito 2): `archivos[]` son
#     **referencias** a assets ya subidos por `POST /api/assets`, no binarios
#     (chat text-only); `binarios=[]`. Comportamiento INTACTO (Req 6.3).
#   - `multipart/form-data` -> campos `mensaje` (str), `archivos` (referencias) y
#     `binarios` (los `UploadFile` reales). Cada `UploadFile` se lee a bytes y se
#     envuelve en `IncomingFile(filename, content)` para el Ingest_Router
#     (Req 6.1, 6.2). El endpoint NO escribe a `assets/`: esa escritura la hace
#     `attach_asset` dentro del Chat_Agent (Req 1.5).
#
# La atomicidad ante fallo (Req 6.5) es HEREDADA de `save_contract` (validate-
# before-write + os.replace): una tool-call que falla no deja escritura parcial y
# las previas confirmadas quedan integras. Se sirve solo en `127.0.0.1` por
# `serve()` (Req 11.1).

# Errores de validacion/entrada que se traducen a `422` en el chat (misma familia
# que el resto del wizard). El resto de las excepciones (fallo de proveedor de
# LLM, red, etc.) cae en el catch-all que responde `500` redactado (Req 6.4, 11.4).
_CHAT_ERRORS = (ValueError, jsonschema.ValidationError)


class ChatBody(BaseModel):
    """Cuerpo JSON de `POST /api/chat` (Req 6.2, 6.3, 8.1): un turno del chat web.

    `mensaje` es el texto del usuario para el turno; `archivos` es una lista
    opcional de **referencias** a assets ya subidos (rutas relativas bajo
    `assets/`), nunca binarios (chat text-only, Req 8.1). Este modelo valida
    exclusivamente el camino `application/json` (Hito 2); el camino
    `multipart/form-data` lee los campos del formulario directamente (DD-M8).
    """

    mensaje: str
    archivos: list[str] = []


@app.post("/api/chat")
async def chat(request: Request):
    """Corre un turno del Chat_Agent sobre el Project_Root (Req 6, 11, DD-M8).

    Acepta dos formas segun el `Content-Type`, compatible hacia atras (DD-M8):

    - `application/json` -> cuerpo `{mensaje, archivos[]}` (Hito 2): se parsea con
      `await request.json()` y se valida con el modelo `ChatBody` (mensaje
      requerido, archivos default `[]`); `binarios=[]`. Un cuerpo no-JSON o que no
      cumple el modelo responde `422` (Req 6.3).
    - `multipart/form-data` -> campos `mensaje` (str), `archivos` (referencias) y
      `binarios` (los `UploadFile` reales). Las `archivos` se toman como **lista
      repetida de campos de formulario** (`form.getlist("archivos")`, criterio
      simple elegido para las referencias); cada `UploadFile` de `binarios` se lee
      a bytes y se envuelve en `IncomingFile(filename, content)` para el
      Ingest_Router (Req 6.1, 6.2). El endpoint NO escribe a `assets/`: la
      escritura la hace `attach_asset` dentro del Chat_Agent (Req 1.5).

    Ambos caminos construyen `ChatRequest(mensaje, archivos, binarios)`, corren
    `ChatAgent(project).run_turn(...)` y responden `redact_value({respuesta,
    estado})` (Req 6.4, 11.2). Ante error se traduce con `wizard_error_response`
    (redactado, sin trazas): los errores de validacion/entrada responden `422` y
    el resto `500` (Req 6.4, 11.4). La atomicidad ante fallo (Req 6.5) la hereda
    `save_contract` dentro del nucleo; el endpoint no persiste nada por su cuenta.
    """
    project = project_root()

    # 1) Parseo/validacion de la entrada segun el Content-Type (DD-M8). Cualquier
    #    fallo aqui es de entrada -> 422 redactado (Req 6.3, 11.4).
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            mensaje = form.get("mensaje")
            if not isinstance(mensaje, str):
                raise ValueError(
                    "El campo 'mensaje' es obligatorio en el turno del chat."
                )
            # Referencias a assets ya subidos: lista repetida de campos de
            # formulario (criterio simple, DD-M8). Se descartan valores no-texto.
            archivos = [ref for ref in form.getlist("archivos") if isinstance(ref, str)]
            # Binarios reales: se lee cada UploadFile a bytes y se envuelve en
            # IncomingFile para el Ingest_Router (Req 6.1, 6.2). No se escribe a
            # disco aqui: attach_asset lo hace dentro del Chat_Agent (Req 1.5).
            binarios = [
                IncomingFile(filename=up.filename or "", content=await up.read())
                for up in form.getlist("binarios")
                if hasattr(up, "read")
            ]
        else:
            # application/json (Hito 2): {mensaje, archivos[]}; binarios=[].
            try:
                data = await request.json()
            except Exception as exc:  # noqa: BLE001 - cuerpo no-JSON -> 422
                raise ValueError(
                    "El cuerpo de la peticion no es un JSON valido."
                ) from exc
            body = ChatBody.model_validate(data)
            mensaje = body.mensaje
            archivos = body.archivos
            binarios = []
    except (ValidationError, *_CHAT_ERRORS) as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc),
        )

    # 2) Turno del Chat_Agent (Req 6.1, 6.2). Errores de validacion/entrada -> 422;
    #    el resto (proveedor de LLM, red, etc.) -> 500 redactado (Req 6.4, 11.4).
    try:
        agent = ChatAgent(project)
        resp = agent.run_turn(
            ChatRequest(mensaje=mensaje, archivos=archivos, binarios=binarios)
        )
    except _CHAT_ERRORS as exc:
        return JSONResponse(
            status_code=422,
            content=wizard_error_response(exc),
        )
    except Exception as exc:  # noqa: BLE001 - se traduce y redacta (Req 6.4, 11.4)
        return JSONResponse(
            status_code=500,
            content=wizard_error_response(exc),
        )

    return redact_value({"respuesta": resp.respuesta, "estado": resp.estado})


def serve(port: int = 4321) -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port)
