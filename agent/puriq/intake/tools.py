"""intake/tools.py: núcleo compartido de las intake tools (Pieza 1, DD-1..DD-7).

Este módulo declara las acciones de registro (intake) como una **capa fina**
sobre los cimientos que ya existen: los constructores puros
(`build_place`, `build_event`, `make_coords`, `build_modules`, `build_landing`),
los validadores, la capa de contrato atómica (`_load_contract`, `merge_document`,
`save_contract`) y la redacción de secretos (`config.redact_value`). Las intake
tools **envuelven** estas piezas; no reimplementan lógica (Req 1.2).

Frontera de imports (Req 1, DD-3/DD-4): este módulo importa SOLO de los cimientos
neutrales (`puriq.wizard.contracts`, `puriq.config`, y más adelante
`puriq.wizard.asset_store`/`qa_store`, `puriq.errors`, `puriq.core`). NUNCA importa
de `puriq.wizard.server`, que arrastra FastAPI y levanta la app web.

Estado de la implementación (tarea 2.1 — solo andamiaje):
    * Definidas las constantes de documentos y el marcador de marca por defecto.
    * Implementados los helpers base `_save` (ciclo load→merge→save) y
      `_state_response` (envoltura redactada del estado devuelto por una escritura).
    * `INTAKE_TOOL_SPECS`, `INTAKE_TOOL_NAMES`, `INTAKE_GUION` y `run_intake_tool`
      son PLACEHOLDERS que las tareas posteriores (3.x, 5.x, 6.x, 7.x, 8.x, 10.x)
      irán completando con las funciones de intake concretas, sus specs, el guion
      y el despacho con traducción de errores. Se declaran ahora para que
      `intake/__init__.py` pueda reexportar la superficie pública sin romper el
      import del paquete.
"""
from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from puriq import config
from puriq.core import Puriq
from puriq.errors import wizard_error_response
from puriq.intake.ingest import MAX_PDF_BYTES, extract_pdf_text
from puriq.wizard import contracts
from puriq.wizard.asset_store import append_image, next_available_asset
from puriq.wizard.assets import (
    IMAGE_EXTS,
    MAX_ASSET_BYTES,
    normalize_asset_name,
    resolve_within_assets,
)
from puriq.wizard.intake import build_event, build_place, make_coords
from puriq.wizard.landing import LANDING_CATALOG, build_landing
from puriq.wizard.modules import MODULE_CATALOG, build_modules
from puriq.wizard.qa_store import append_qa_entry, register_knowledge_source
from puriq.wizard.validation import (
    QAValidationError,
    validate_domain,
    validate_qa_entry,
)

# --- Constantes de documentos del contrato -----------------------------------
# Mismas claves que `contracts._DOC_FILES` (documento -> archivo del proyecto).
#: Patron de id que el esquema exige en `categories[].id` (kebab-case).
_SLUG_RE = re.compile(r"[a-z0-9-]+")

_TOURISM = "tourism-data"
_CONFIG = "site-config"
_THEME = "theme-tokens"

# Colores marcadores por defecto del documento base de `theme-tokens`
# (ver `contracts._base_document("theme-tokens")`). Se usan para detectar
# "marca sin definir" al computar los faltantes de `get_state` (Req 2.6, DD-7).
_DEFAULT_BRAND_COLORS = {
    "primary": "#000000",
    "background": "#ffffff",
    "text": "#111111",
}


# --- Helpers base compartidos por las tools de escritura ----------------------
def _save(project: Path, doc: str, patch: dict) -> dict:
    """Ciclo canónico *load → merge → save* del contrato (DD-1, Req 1.3, 1.4).

    Carga el documento `doc` del proyecto (tolerante para `tourism-data`,
    estricta para el resto), fusiona `patch` de forma aditiva y no destructiva,
    y persiste el resultado con `save_contract` (que valida antes de escribir de
    forma atómica; para `tourism-data` admite borradores con `coords` opcional).

    Devuelve el documento **fusionado sin redactar**, para que la función de
    intake que lo llama pueda envolverlo con `_state_response` al construir su
    respuesta.
    """
    base = contracts._load_contract(project, doc)
    merged = contracts.merge_document(base, patch)
    contracts.save_contract(project, doc, merged)
    return merged


def _state_response(merged: dict) -> dict:
    """Envuelve el documento afectado como estado redactado de una escritura.

    Aplica `config.redact_value` de forma recursiva para que ningún valor de
    secreto configurado aparezca en la respuesta (Req 2.8, 14.5) y lo devuelve
    bajo la clave `document`, forma de salida común de las tools de escritura.
    """
    return {"document": config.redact_value(merged)}


# --- Intake tools: identidad y estructura (tarea 3.1) ------------------------
def set_site(
    project: Path,
    *,
    name: str,
    region: str,
    center_lat: float,
    center_lng: float,
    center_zoom: int | None = None,
    default_locale: str = "es",
    domain: str | None = None,
    contact: Mapping[str, Any] | None = None,
) -> dict:
    """Registra la identidad del sitio y, si aplica, dominio/contacto (Req 3).

    Escribe el nombre, la región, el idioma por defecto y el centro del mapa en
    `tourism-data.site` (Req 3.1, 3.2). El centro se construye con `make_coords`,
    que valida el rango de `lat`/`lng` y lanza `CoordinateRangeError` cuando está
    fuera de rango (Req 3.6). Si se indica `domain`, se normaliza y valida con
    `validate_domain` (formato inválido → `DomainError`, Req 3.4) y se guarda en
    `site-config.deploy.domain` (Req 3.3); si se indican datos de `contact`, se
    guardan en `site-config.contact` (Req 3.5).

    Alineado con el REST del wizard, que reparte estos datos entre `put_site`
    (tourism-data) y `put_site_config` (site-config): aquí `set_site` puede
    escribir en **dos** documentos en un solo llamado. Toda la validación de
    entrada (`make_coords`, `validate_domain`) se ejecuta **antes** de persistir
    nada, de modo que una entrada rechazada deja ambos documentos sin cambios
    (Property 8, Req 14.3). Se devuelve el estado del `tourism-data` afectado
    (Req 3.7); el `site-config` se persiste en el mismo llamado si hubo dominio o
    contacto. Las excepciones tipadas se dejan propagar para su traducción en el
    borde del intake (DD-5).
    """
    # Validar toda la entrada antes de tocar disco: si el centro o el dominio son
    # inválidos, no se escribe ninguno de los dos documentos (atomicidad).
    center = make_coords(center_lat, center_lng, center_zoom)

    config_patch: dict = {}
    if domain is not None:
        # Un dominio vacío se guarda como cadena vacía a propósito: permite BORRAR
        # una dirección cargada por error (mismo criterio que `put_site_config`).
        config_patch["deploy"] = {"domain": validate_domain(domain)}
    if contact:
        config_patch["contact"] = dict(contact)

    tourism_patch = {
        "site": {
            "name": name,
            "region": region,
            "defaultLocale": default_locale,
            "center": center,
        }
    }
    merged = _save(project, _TOURISM, tourism_patch)
    if config_patch:
        _save(project, _CONFIG, config_patch)

    return _state_response(merged)


def configure_modules(project: Path, *, selection) -> dict:
    """Configura los módulos del sitio a partir de una selección ordenada (Req 4).

    Delega en el constructor puro `build_modules`, que restringe la selección al
    catálogo soportado, rechaza claves fuera de catálogo o repetidas con
    `ModuleCatalogError` y asigna a cada módulo un `order` entero >= 1 coherente
    con el orden recibido (Req 4.1–4.4). Persiste el resultado en
    `site-config.modules` con validación estricta contra el esquema y devuelve el
    estado del contrato afectado (Req 4.5). Las excepciones se dejan propagar
    (DD-5).
    """
    merged = _save(project, _CONFIG, {"modules": build_modules(selection)})
    return _state_response(merged)


def configure_landing(project: Path, *, selection) -> dict:
    """Configura las secciones de la portada según una selección ordenada (Req 9).

    Delega en el constructor puro `build_landing`, que restringe los tipos al
    catálogo soportado (fuera de catálogo → `LandingCatalogError`) y asigna a cada
    sección un `order` entero >= 1 coherente con el orden recibido (Req 9.1–9.3).
    Persiste el resultado en `site-config.landing` y devuelve el estado del
    contrato afectado (Req 9.4). Las excepciones se dejan propagar (DD-5).
    """
    merged = _save(project, _CONFIG, {"landing": build_landing(selection)})
    return _state_response(merged)


# --- Intake tools: contenido (lugares y eventos, tarea 3.2) ------------------
def add_place(
    project: Path,
    *,
    name: str,
    category: str,
    lat: float | None = None,
    lng: float | None = None,
    zoom: int | None = None,
    address: str | None = None,
    category_label: str | None = None,
) -> dict:
    """Agrega un Place a `tourism-data.places` (Req 5).

    Delega en el constructor puro `build_place`, que deriva ``id = slugify(name)``
    (Req 5.1) y arma la ubicación del Place:
      - Si vienen `lat` y `lng`, se validan y se asigna `coords` vía `make_coords`
        dentro de `build_place` (Req 5.2); fuera de rango → `CoordinateRangeError`
        (Req 5.5).
      - Si viene solo una de las dos coordenadas, `build_place` lanza
        `CoordinateRangeError` indicando que se requieren ambas (Req 5.4).
      - Si viene solo `address` sin coordenadas, se conserva la dirección y el
        Place se persiste como **borrador** sin inventar `coords` (Req 5.3); la
        validación relajada de `save_contract` para `tourism-data` lo admite.

    El parche `{"places": [place]}` se **anexa por id** vía `merge_document`, que
    no borra ni reordena los lugares existentes (Req 5.6). Alineado con el
    endpoint REST `add_place` del wizard. Devuelve el estado del `tourism-data`
    afectado (Req 5.7). Las excepciones tipadas se dejan propagar para su
    traducción en el borde del intake (DD-5).
    """
    place = build_place(
        name, category, lat=lat, lng=lng, zoom=zoom, address=address
    )
    patch: dict = {"places": [place]}
    # La categoria se declara sola la primera vez que se usa. El sitio muestra
    # `categories[].label` y, si la categoria no esta declarada, cae al id crudo:
    # una ficha cargada por conversacion mostraba el chip "habitaciones" en
    # minuscula en vez de "Habitaciones". Los ejemplos versionados traen sus
    # categorias escritas a mano, asi que esto solo se notaba al cargar por chat
    # —y sobre todo con categorias propias de un emprendimiento
    # (`habitaciones`, `tours`, `platos`), que ningun ejemplo predefine.
    nueva = _category_entry(project, place["category"], category_label)
    if nueva is not None:
        patch["categories"] = [nueva]
    merged = _save(project, _TOURISM, patch)
    return _state_response(merged)


def _category_entry(project: Path, category_id: str, label: str | None) -> dict | None:
    """Devuelve la Category a declarar para `category_id`, o None si ya existe.

    El `label` lo puede aportar quien llama (el LLM sabe como lo dijo el usuario);
    si no viene, se deriva del id volviendolo legible: ``casa-museo`` -> ``Casa
    museo``. `merge_document` fusiona por id, asi que devolver una entrada ya
    existente igual seria inocuo, pero se evita para no pisar un `label`/`icon`
    que el usuario haya ajustado a mano.

    Solo se declara si el id cumple el patron slug que el esquema exige en
    `categories[].id`. El campo `category` de un Place NO tiene esa restriccion,
    asi que declarar a ciegas volveria invalido un contrato que antes se guardaba
    sin problema: `add_place` pasaria a rechazar categorias que hoy acepta. Si no
    es un slug se omite la declaracion y el sitio muestra el id crudo, que es
    exactamente lo que hacia antes de este cambio.
    """
    if not _SLUG_RE.fullmatch(category_id or ""):
        return None
    actual = contracts._load_contract(project, _TOURISM)
    existentes = actual.get("categories")
    ids = {
        c.get("id")
        for c in (existentes if isinstance(existentes, list) else [])
        if isinstance(c, dict)
    }
    if category_id in ids:
        return None
    if not label or not str(label).strip():
        label = category_id.replace("-", " ").strip().capitalize()
    return {"id": category_id, "label": str(label).strip()}


def add_event(
    project: Path,
    *,
    name: str,
    start_date: str,
    end_date: str | None = None,
    place_id: str | None = None,
    description: str | None = None,
    recurring: str | None = None,
) -> dict:
    """Agrega un Event a `tourism-data.events` (Req 6).

    Delega en el constructor puro `build_event`, que deriva ``id = slugify(name)``
    (Req 6.1) e incluye `name` y `startDate`; los campos opcionales `end_date`,
    `place_id`, `description` y `recurring` se agregan solo cuando se proveen
    (Req 6.2). El parche `{"events": [event]}` se **anexa por id** vía
    `merge_document`, que no borra ni reordena los eventos existentes (Req 6.3).
    Alineado con el endpoint REST `add_event` del wizard. Devuelve el estado del
    `tourism-data` afectado (Req 6.4). Las excepciones se dejan propagar (DD-5).
    """
    event = build_event(
        name,
        start_date,
        end_date=end_date,
        place_id=place_id,
        description=description,
        recurring=recurring,
    )
    merged = _save(project, _TOURISM, {"events": [event]})
    return _state_response(merged)


# --- Intake tools: edición y eliminación de contenido (tarea 5.1) ------------
def edit_item(project: Path, *, id: str, fields: Mapping[str, Any]) -> dict:
    """Edita un Place o Event por `id`, actualizando solo los campos dados (Req 7).

    Delega en `Puriq(project).edit(id, fields)`, que reusa la tool pura
    `edit_content.edit` (merge aditivo: actualiza únicamente los campos indicados
    y preserva los no indicados, Req 7.1) y persiste el `tourism-data` resultante
    como borrador (coords opcional por Place). Un `id` que no corresponde a ningún
    Place ni Event hace que `edit_content.edit` lance ``ValueError`` indicando que
    el elemento no fue encontrado (Req 7.3), excepción que se deja propagar para su
    traducción en el borde del intake (DD-5).

    Tras la escritura, relee el `tourism-data` con `contracts._load_contract` y
    devuelve el estado resultante del contrato (Req 7.5). Alineado con el REST
    `edit_entity` del wizard, la forma de salida es
    ``{"id", "document": <tourism-data>}``, redactada con `config.redact_value`
    para que ningún secreto configurado aparezca en la respuesta.
    """
    Puriq(project).edit(id, dict(fields))
    document = contracts._load_contract(project, _TOURISM)
    return config.redact_value({"id": id, "document": document})


def remove_item(project: Path, *, id: str) -> dict:
    """Elimina un Place o Event por `id` y devuelve el estado resultante (Req 7).

    Delega en `Puriq(project).delete(id)`, que reusa `delete_content.delete`
    (integridad referencial: al borrar un Place, limpia el `placeId` colgante de
    los Events que lo referenciaban y devuelve esos ids en `affectedEvents`,
    Req 7.2, 7.4) y persiste el `tourism-data` resultante como borrador. Un `id`
    inexistente hace que `delete_content.delete` lance ``ValueError`` indicando que
    el elemento no fue encontrado (Req 7.3), excepción que se deja propagar para su
    traducción en el borde del intake (DD-5).

    Tras la escritura, relee el `tourism-data` con `contracts._load_contract` y
    devuelve el estado resultante del contrato (Req 7.5). Alineado con el REST
    `delete_entity` del wizard, la forma de salida es
    ``{"id", "affectedEvents", "document": <tourism-data>}``, redactada con
    `config.redact_value`.
    """
    resultado = Puriq(project).delete(id)
    document = contracts._load_contract(project, _TOURISM)
    return config.redact_value(
        {
            "id": resultado["id"],
            "affectedEvents": resultado["affectedEvents"],
            "document": document,
        }
    )


# --- Intake tools: marca y base de conocimiento Q&A (tarea 5.2) --------------
def set_brand(
    project: Path,
    *,
    colors: Mapping[str, Any] | None = None,
    typography: Mapping[str, Any] | None = None,
    voice: Mapping[str, Any] | None = None,
) -> dict:
    """Define colores, tipografía y voz de la marca en `theme-tokens` (Req 8).

    Arma un parche de `theme-tokens` **solo** con los campos provistos y lo
    persiste con el ciclo load→merge→save (`_save`). El `merge_document` conserva
    lo no tocado, de modo que un parche por paso (p. ej. solo colores) no borra la
    tipografía ni la voz ya cargadas (Req 8.3, 8.4). La validación estricta contra
    `theme-tokens.schema.json` la aplica `save_contract` antes de escribir: un
    color que no cumple el patrón hexadecimal es rechazado por el esquema y se
    traduce (en el borde del intake, DD-5) a un mensaje que indica el formato de
    color esperado (Req 8.2), sin persistir nada (validate-before-write).

    Alineado con el REST `put_theme_tokens` del wizard, que arma el mismo parche
    (`colors`/`typography`/`voice`) sobre el documento estricto de marca. `voice`
    se escribe tal cual como el sub-objeto `theme-tokens.voice` (p. ej.
    ``{"tone": ..., "formality": ...}``, Req 8.4). Devuelve el estado del
    `theme-tokens` afectado, redactado con `config.redact_value` (Req 8.5).
    """
    patch: dict = {}
    if colors:
        patch["colors"] = dict(colors)
    if typography:
        patch["typography"] = dict(typography)
    if voice:
        patch["voice"] = dict(voice)

    merged = _save(project, _THEME, patch)
    return _state_response(merged)


def add_qa(project: Path, *, question: str, answer: str) -> dict:
    """Anexa un QA_Entry a `content/qa.json` y registra su knowledgeSource (Req 10).

    Valida el par pregunta/respuesta con `validate_qa_entry`, que recorta los
    valores y lanza `QAValidationError` nombrando el campo cuando la pregunta o la
    respuesta está vacía o es solo espacios (Req 10.1, 10.2); la excepción se deja
    propagar para su traducción en el borde del intake (DD-5). El QA_Entry validado
    se anexa con `qa_store.append_qa_entry`, que no borra las entradas existentes
    (Req 10.3) y devuelve la ruta relativa `content/qa.json`. Esa ruta se registra
    en `site-config.modules.chatweb.knowledgeSource` con
    `qa_store.register_knowledge_source`, que persiste el `site-config` vía
    load→merge→save.

    Alineado con el REST `add_qa` del wizard, devuelve
    ``{entry, knowledgeSource, document}`` (con `document` = `site-config`
    resultante) redactado con `config.redact_value` para que ningún secreto
    configurado aparezca en la respuesta (Req 10.4, 14.5).
    """
    entry = validate_qa_entry({"question": question, "answer": answer})
    rel_path = append_qa_entry(project, entry)
    site_config = register_knowledge_source(project, rel_path)
    return config.redact_value(
        {
            "entry": entry,
            "knowledgeSource": rel_path,
            "document": site_config,
        }
    )


# --- Intake tools: adjunto de imágenes (tarea 6.1, DD-6) ---------------------
#: Límite de tamaño de un Asset expresado en MiB, para el mensaje de rechazo por
#: tamaño (Req 11.3). Deriva de `MAX_ASSET_BYTES` (fuente única en `wizard/assets`).
_MAX_ASSET_MB = MAX_ASSET_BYTES // (1024 * 1024)

#: Mapeo de `target` (superficie de la tool) a la clave de colección del contrato.
_TARGET_TO_ENTITY_KEY = {"place": "places", "event": "events"}


def attach_asset(
    project: Path,
    *,
    filename: str,
    content_base64: str | None = None,
    source_path: str | None = None,
    target: str,
    id: str,
) -> dict:
    """Adjunta una imagen a `assets/` y la asocia a un Place/Event (Req 11, DD-6).

    A diferencia del REST `upload_asset` del wizard (que recibe un `UploadFile`
    multipart), esta tool transporta el binario por MCP como JSON: acepta **una
    de dos** fuentes (DD-6):
      - `content_base64`: el binario codificado en base64 (portable entre
        clientes), o
      - `source_path`: una ruta local legible por el servidor.
    Exactamente una debe venir; si faltan ambas o vienen ambas, se rechaza con un
    `ValueError` accionable sin tocar disco.

    Pasos (idénticos en lógica al REST, salvo el origen del binario):
      1. Obtener los bytes de la fuente indicada.
      2. Comprobar `len(bytes) <= MAX_ASSET_BYTES` **antes** de escribir; si excede
         → `ValueError` indicando el límite (Req 11.3).
      3. Normalizar el nombre con `normalize_asset_name(filename, IMAGE_EXTS)`
         (extensión no soportada → `ValueError` que lista formatos, Req 11.1, 11.2).
      4. Verificar la contención en `assets/` con `resolve_within_assets` y
         desambiguar colisiones con `next_available_asset` (que también verifica la
         contención internamente); cualquier ruta que escape de `assets/` se
         rechaza (Req 11.4).
      5. Escribir los bytes al destino (creando el dir padre si hace falta).
      6. Asociar con `append_image(project, entity_key, id, rel_path)`; un `id`
         inexistente → `ValueError` "no encontrado" (Req 11.6).

    Devuelve `{path, document}` redactado con `config.redact_value` (Req 11.5,
    11.7). Las excepciones tipadas se dejan propagar para su traducción en el
    borde del intake (DD-5).
    """
    # Fuente del binario: exactamente una de content_base64 / source_path (DD-6).
    if content_base64 is not None and source_path is not None:
        raise ValueError(
            "Indica solo una fuente de la imagen: 'content_base64' o "
            "'source_path', no ambas."
        )
    if content_base64 is not None:
        try:
            # binascii.Error (que b64decode puede lanzar) es subclase de
            # ValueError, así que un único except cubre ambos casos.
            contenido = base64.b64decode(content_base64, validate=True)
        except ValueError as exc:
            raise ValueError(
                "El contenido 'content_base64' no es base64 válido."
            ) from exc
    elif source_path is not None:
        origen = Path(source_path)
        if not origen.is_file():
            raise ValueError(
                f"No se encontró un archivo legible en 'source_path': "
                f"{source_path!r}."
            )
        contenido = origen.read_bytes()
    else:
        raise ValueError(
            "Falta la fuente de la imagen: indica 'content_base64' o "
            "'source_path'."
        )

    # Destino de asociación: place o event (Req 11.6).
    entity_key = _TARGET_TO_ENTITY_KEY.get(target)
    if entity_key is None:
        aceptados = ", ".join(sorted(_TARGET_TO_ENTITY_KEY))
        raise ValueError(
            f"Destino no soportado: {target!r}. Valores aceptados: {aceptados}."
        )

    # Tamaño: se compara ANTES de tocar disco (Req 11.3).
    if len(contenido) > MAX_ASSET_BYTES:
        raise ValueError(
            f"El archivo supera el tamaño máximo permitido de "
            f"{_MAX_ASSET_MB} MB."
        )

    # Tipo/extensión + normalización a Slug (Req 11.1, 11.2). Extensión no
    # soportada -> ValueError que lista los formatos aceptados.
    nombre = normalize_asset_name(filename, IMAGE_EXTS)

    # Verificación de contención (Req 11.4): rechaza rutas que escapen de assets/.
    resolve_within_assets(project, nombre)
    # Desambiguación de colisión conservando los assets previos (que también
    # verifica la contención internamente, Req 11.4).
    nombre_final, destino = next_available_asset(project, nombre)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)

    rel_path = f"assets/{nombre_final}"

    # Asociación al Place/Event por id (Req 11.5); id inexistente -> ValueError
    # "no encontrado" (Req 11.6).
    document = append_image(project, entity_key, id, rel_path)

    return config.redact_value({"path": rel_path, "document": document})


# --- Intake tool: extracción de texto de PDF (Extract_PDF_Tool, tarea 6.1) ----
#: Límite de tamaño de un PDF expresado en MiB, para el mensaje de rechazo por
#: tamaño (Req 10.4). Deriva de `MAX_PDF_BYTES` (fuente única en `intake/ingest`).
_MAX_PDF_MB = MAX_PDF_BYTES // (1024 * 1024)


def extract_pdf(
    project: Path,
    *,
    content_base64: str | None = None,
    source_path: str | None = None,
) -> dict:
    """Extrae el Texto_Extraido de un PDF (base64 o ruta) y lo devuelve redactado (Req 7).

    Como `attach_asset`, transporta el binario por MCP como JSON y acepta **una de
    dos** fuentes (DD-6/DD-M6):
      - `content_base64`: el PDF codificado en base64 (portable entre clientes), o
      - `source_path`: una ruta local legible por el servidor.
    Exactamente una debe venir; si faltan ambas o vienen ambas, se rechaza con un
    `ValueError` accionable sin procesar nada (Req 7.3).

    Pasos:
      1. Obtener los bytes de la fuente indicada (base64 inválido o ruta
         inexistente → `ValueError` accionable, mismo criterio que `attach_asset`).
      2. Comprobar `len(bytes) <= MAX_PDF_BYTES` **antes** de procesar; si excede →
         `ValueError` indicando el límite (Req 10.4, 10.5).
      3. Delegar en `ingest.extract_pdf_text` para obtener el texto en memoria
         (Req 7.2); el PDF nunca se persiste (Req 11.5).

    Devuelve `config.redact_value({"text": Texto_Extraido})` (Req 7.2, 11.2). Las
    excepciones tipadas (fuente inválida, tamaño excedido, PDF sin texto, extra
    ausente) se dejan propagar para su traducción en el borde del intake
    (`run_intake_tool` → `wizard_error_response`, DD-5).
    """
    # Fuente del binario: exactamente una de content_base64 / source_path (DD-6),
    # mismo criterio que attach_asset.
    if content_base64 is not None and source_path is not None:
        raise ValueError(
            "Indica solo una fuente del PDF: 'content_base64' o 'source_path', "
            "no ambas."
        )
    if content_base64 is not None:
        try:
            # binascii.Error (que b64decode puede lanzar) es subclase de
            # ValueError, así que un único except cubre ambos casos.
            contenido = base64.b64decode(content_base64, validate=True)
        except ValueError as exc:
            raise ValueError(
                "El contenido 'content_base64' no es base64 válido."
            ) from exc
    elif source_path is not None:
        origen = Path(source_path)
        if not origen.is_file():
            raise ValueError(
                f"No se encontró un archivo legible en 'source_path': "
                f"{source_path!r}."
            )
        contenido = origen.read_bytes()
    else:
        raise ValueError(
            "Falta la fuente del PDF: indica 'content_base64' o 'source_path'."
        )

    # Tamaño: se comprueba ANTES de procesar (Req 10.4, 10.5).
    if len(contenido) > MAX_PDF_BYTES:
        raise ValueError(
            f"El PDF supera el tamaño máximo permitido de {_MAX_PDF_MB} MB."
        )

    # Extracción en memoria (Req 7.2, 11.5); el PDF no se persiste. Un PDF sin
    # texto o el extra ausente propagan su excepción tipada para su traducción.
    texto = extract_pdf_text(contenido)

    return config.redact_value({"text": texto})


# --- Intake tools: estado del contrato (get_state, tarea 7.1, DD-7) ----------
def _is_blank(value: Any) -> bool:
    """Un valor cuenta como «vacío» a efectos de faltantes: None o str en blanco.

    Coincide con el marcador base de `site.name`/`site.region` (la cadena vacía
    del `_base_document`), de modo que un nombre o región ausente o en blanco se
    detecta como faltante (Req 2.3).
    """
    return value is None or (isinstance(value, str) and value.strip() == "")


def _center_is_default(center: Any) -> bool:
    """Indica si el centro del mapa está ausente o es el marcador base `{lat:0, lng:0}`.

    El `_base_document("tourism-data")` inicializa `site.center` en
    `{"lat": 0, "lng": 0}`; ese valor (o un centro ausente/vacío) señala que el
    usuario aún no fijó el centro real del mapa (Req 2.3, DD-7).
    """
    if not isinstance(center, Mapping) or not center:
        return True
    return center.get("lat") == 0 and center.get("lng") == 0


def get_state(project: Path) -> dict:
    """Devuelve el estado de los 3 documentos del contrato + faltantes (Req 2, DD-7).

    Snapshot de **solo lectura**: carga los tres documentos con
    `contracts._load_contract` **sin mutarlos** (Req 2.1) y computa la lista
    `missing` comparando contra los marcadores por defecto del documento base
    (`contracts._base_document`), para que el LLM del cliente sepa qué preguntar a
    continuación:

      - **site** (Req 2.3): por cada uno de `name`, `region`, `center` que esté
        vacío/ausente o conserve el marcador base (`name`/`region` == ``""``,
        `center` == ``{lat:0, lng:0}``), se añade `{"piece": "site", "field": <campo>}`.
      - **modules** (Req 2.4): si ningún módulo de `site-config.modules` tiene
        `enabled: true`, se añade `{"piece": "modules", "field": None}`.
      - **places** (Req 2.5): si `tourism-data.places` está vacío, se añade
        `{"piece": "places", "field": None}`.
      - **brand** (Req 2.6): si `theme-tokens.colors` conserva
        `_DEFAULT_BRAND_COLORS`, se añade `{"piece": "brand", "field": "colors"}`.
      - **completo** (Req 2.7): si ninguna regla dispara, `missing` queda vacío.

    Devuelve los tres documentos bajo sus claves de contrato (`"tourism-data"`,
    `"site-config"`, `"theme-tokens"`) más `missing`, todo redactado con
    `config.redact_value` para que ningún secreto configurado aparezca en la
    respuesta (Req 2.2, 2.8).
    """
    tourism = contracts._load_contract(project, _TOURISM)
    site_config = contracts._load_contract(project, _CONFIG)
    theme = contracts._load_contract(project, _THEME)

    missing: list[dict] = []

    # site (Req 2.3): identidad del sitio, campo por campo en orden estable.
    site = tourism.get("site")
    if not isinstance(site, Mapping):
        site = {}
    if _is_blank(site.get("name")):
        missing.append({"piece": "site", "field": "name"})
    if _is_blank(site.get("region")):
        missing.append({"piece": "site", "field": "region"})
    if _center_is_default(site.get("center")):
        missing.append({"piece": "site", "field": "center"})

    # modules (Req 2.4): ningún módulo habilitado.
    modules = site_config.get("modules")
    if not isinstance(modules, Mapping):
        modules = {}
    any_enabled = any(
        isinstance(mod, Mapping) and mod.get("enabled") is True
        for mod in modules.values()
    )
    if not any_enabled:
        missing.append({"piece": "modules", "field": None})

    # places (Req 2.5): sin lugares cargados.
    places = tourism.get("places")
    if not places:
        missing.append({"piece": "places", "field": None})

    # brand (Req 2.6): colores marcadores por defecto del documento base.
    if theme.get("colors") == _DEFAULT_BRAND_COLORS:
        missing.append({"piece": "brand", "field": "colors"})

    return config.redact_value(
        {
            _TOURISM: tourism,
            _CONFIG: site_config,
            _THEME: theme,
            "missing": missing,
        }
    )


# --- Intake tools: construcción del sitio (build, tarea 8.1) -----------------
def build(project: Path, *, use_llm: bool = True) -> dict:
    """Construye el sitio estático a partir del contrato persistido (Req 12).

    Delega en `Puriq(project).build(use_llm=use_llm)`, que corre el pipeline
    completo sobre el contrato en disco: carga tolerante → geocode →
    comprobación accionable de `coords` → validación estricta → generate (si
    `use_llm`) → assemble, y devuelve la ruta del directorio `dist/` generado
    (Req 12.1, 12.2). Esa ruta se envuelve como ``{"dist": str(path)}``.

    Los errores del pipeline (p. ej. `MissingCoordsError` por un Place sin
    coordenadas, o un `ValidationError`/`ValueError` de esquema por un contrato
    incompleto o inválido) se **dejan propagar** sin capturarlos aquí, para su
    traducción a un mensaje accionable y redactado en el borde del intake
    (`run_intake_tool` → `wizard_error_response`, DD-5, Req 12.3).
    """
    dist = Puriq(project).build(use_llm=use_llm)
    return {"dist": str(dist)}


# --- Guion del intake (fases 1–9 del §5 de docs/registro-conversacional.md) ----
#: Texto del guion del intake, embebido (por extracto) en las descripciones de
#: las tools (Req 13.4) y servido íntegro como recurso MCP `intake://guion`
#: (Req 13.5). Instruye al LLM del cliente qué preguntar y en qué orden, marcando
#: completo/falta con `get_state` en cada paso. Refleja el §5 del documento de
#: diseño conversacional (fases 1–9) y la regla transversal de pedir archivos
#: activamente.
INTAKE_GUION: str = """\
# Guion del intake conversacional de Puriq

Conducí el registro por fases, apoyándote en `get_state` para saber qué falta y
qué preguntar a continuación. Consultá `get_state` al empezar y después de cada
cambio: su lista `missing` te dice la pieza pendiente (identidad del sitio,
módulos, lugares, marca).

`missing` es la **única** autoridad sobre lo que falta: no inventes requisitos
propios ni trates un campo opcional como bloqueante. Las fases son un orden
sugerido, no un candado — si el usuario quiere adelantarse (por ejemplo, cargar
una Q&A o definir la marca antes de terminar los lugares), **hacelo** y volvé
después a lo pendiente. Priorizá siempre el ritmo del usuario.

**Cuando el usuario acepta algo que vos propusiste, eso es una orden de
registrar**: si le ofreciste una paleta, una descripción o una selección de
módulos y responde "sí", "dale" o "usá esa", invocá la tool correspondiente
(`set_brand`, `edit_item`, `configure_modules`…) **en ese mismo turno**, antes de
contestar. Proponer no persiste nada: mientras no llames a la tool, el contrato
sigue igual. Nunca digas "ya lo guardé", "ya lo registré" ni "ya quedó
configurado" si no invocaste la tool en ese turno; si no la invocaste, decí qué
falta y hacelo.

## Fases

**Fase 0 — De qué es el sitio.** Antes de nada, entendé QUIEN te está hablando,
porque cambia todo lo que preguntes después. Puriq sirve a dos casos:

  - Un **destino**: un municipio, pueblo o región que quiere mostrarse entero.
    El `name` es el del lugar ("Turismo Potosí") y los lugares son atractivos
    públicos.
  - Un **emprendimiento turístico**: una hospedería, un operador de tours, un
    guía, un restaurante, un taller de artesanía. El `name` es el del negocio
    ("Hostal Kori Wasi") y los "lugares" son lo que ofrece o los sitios que
    incluye su propuesta.

No lo preguntes de forma mecánica: se deduce de cómo se presenta el usuario. Si
dice "quiero promocionar mi hostal" ya sabés que es el segundo caso; si dice
"soy de la alcaldía", el primero. Ante la duda, preguntá en una línea. Adaptá el
vocabulario al caso: a un emprendedor no le hables de "atractivos del destino"
sino de "lo que ofrecés".

**Fase 1 — Sitio.** Pedí el nombre del sitio, la región, el centro del mapa
(latitud y longitud) y el idioma por defecto. Opcionalmente el dominio web y los
datos de contacto. Registralo con `set_site`.

El `name` es el del destino o el del emprendimiento, según el caso de la fase 0.

La `region` ubica geográficamente al sitio y es la **unidad administrativa**
(departamento, provincia o estado), **no el país**: si el usuario dice "Sucre,
Bolivia", la región es "Chuquisaca" (podés escribirla como "Chuquisaca,
Bolivia"), nunca sólo "Bolivia". Vale igual para un emprendimiento: la región es
donde está, no su dirección. Si no sabés a qué departamento pertenece la
localidad, preguntáselo en vez de poner el país.

Para un emprendimiento, el `center` del mapa es su propia ubicación; para un
destino, el centro de la zona que abarca.

**Fase 2 — Módulos.** Traducí lo que el usuario quiere mostrar a una selección
ordenada de módulos (mapa, lugares, eventos, blog, asistente). Ej.: "quiero
lugares y eventos" → activá `places` y `events`. Registralo con
`configure_modules`.

**Fase 3 — Lugares.** Cargá los lugares uno por uno con `add_place`. Lo único
imprescindible es el **nombre** y la **categoría**: con eso alcanza para
registrarlo.

Qué es un "lugar" depende del caso de la fase 0. Para un destino son sus
atractivos (un cerro, un museo, una laguna). Para un emprendimiento son **lo que
ofrece**: las habitaciones o cabañas de una hospedería, cada tour de un
operador, los platos o el salón de un restaurante, las piezas de un taller. Las
categorías las elegís vos según el caso —`habitaciones`, `tours`, `platos`,
`atractivos`— y las nombrás en el lenguaje del usuario. La ubicación (coordenadas o dirección) es **opcional y deseable**,
no un requisito: pedila una vez, pero si el usuario no la tiene a mano o no
contesta, **registrá el lugar igual** como borrador —sin inventar coordenadas— y
seguí adelante; se completa después con `edit_item`. Nunca bloquees la carga de
un lugar, ni el avance de la conversación, por una ubicación faltante. En esta fase
**pedí fotos activamente** de cada lugar ("¿Tenés una foto del Cerro Rico?
Mandámela y la asocio"). Cuando el usuario adjunte una imagen, **guardá el
archivo en ese mismo turno** con `attach_asset` (sin pedir confirmación: enviarla
ya fue la decisión del usuario). La ves además por visión: usá su descripción
para **proponer** la `description` y/o la `shortDescription` del lugar. Ese texto
derivado no se escribe todavía: proponelo y esperá la confirmación del usuario
antes de invocar `edit_item` o `add_place`.

**Fase 4 — Eventos.** Cargá eventos con su fecha de inicio y, si aplica, fecha de
fin, lugar asociado, descripción y recurrencia. Usá `add_event`.

**Fase 5 — Marca.** Proponé una paleta de colores y definí tipografía y voz de la
marca con `set_brand`. **Pedí el logo** al usuario.

**Fase 6 — Portada.** Armá las secciones de la portada (hero, features, cta,
galería, etc.) en un orden según lo ya cargado, con `configure_landing`.

**Fase 7 — Q&A.** Cargá preguntas y respuestas para el asistente con `add_qa`.
**Pedí activamente un PDF de contexto** (folleto, ficha del municipio, historia)
para nutrir las preguntas. El texto del PDF llega como contexto del turno; en la
superficie MCP lo obtenés con la tool `extract_pdf`. **Destilá** ese texto a Q&A
(y a descripciones o datos históricos) con las intake tools; **no** publiques el
PDF. Proponé las Q&A derivadas y esperá el "sí" del usuario antes de llamar a
`add_qa`: **no invoques `add_qa` en el mismo turno en que llega el texto del
PDF**.

**Fase 8 — Recursos.** Solicitá activamente las imágenes que falten y los PDFs de
contexto. Asociá cada imagen a su lugar o evento con `attach_asset` **en el mismo
turno en que llega**. Recordá: las imágenes se guardan como asset y su
descripción alimenta la `description`/`shortDescription` del ítem; los PDFs se
**destilan** a contenido del contrato con las intake tools, nunca se publican como
archivo. Confirmá con el usuario antes de escribir cualquier contenido derivado.

**Fase 9 — Generar.** Cuando `get_state` no reporte faltantes esenciales,
construí el sitio con `build` y ofrecé la previsualización.

## Regla transversal: pedí los archivos, no esperes

No te quedes esperando a que el usuario ofrezca imágenes o documentos: pedilos de
forma proactiva y concreta ("¿Tenés una foto del Cerro Rico? Mandámela y la
asocio"). El estado de `get_state` (qué falta) guía cada pregunta.

## Ingesta multimodal: imágenes y PDFs

Puriq acepta **imágenes** y **PDFs** durante la charla. Pedilos activamente en
las fases que corresponden (fotos de lugares en la fase 3; PDFs de contexto en
las fases 7 y 8) y tratalos así:

- **Imágenes (visión).** Cuando el usuario adjunta una foto, la interpretás por
  visión y obtenés una descripción. Usá esa descripción para **proponer** la
  `description` y/o la `shortDescription` del lugar o evento asociado: son los
  campos que el contrato acepta. El texto alternativo accesible de la imagen no
  se registra por ahora en el contrato, así que
  **no intentes escribir un campo `alt`** en un lugar o evento: el esquema lo
  rechaza. La imagen se guarda como asset con `attach_asset`; las descripciones
  se escriben en el ítem con `edit_item` (o al crearlo con `add_place`).

- **PDFs (destilado, no publicación).** El texto de un PDF llega como **contexto**
  del turno. En la superficie MCP, obtené ese texto con la tool `extract_pdf`
  (recibe el PDF como base64 o ruta local y devuelve su texto). **Destilá** ese
  texto a contenido del contrato —descripciones de lugares, Q&A y datos
  históricos— usando las intake tools `add_qa`, `edit_item` y `add_place`. **No
  publiques el PDF** ni lo adjuntes como archivo del sitio: solo se usa su texto
  para poblar el contrato.

- **Guardar el archivo ≠ escribir contenido derivado.** Son dos cosas distintas:
  * **Guardar el archivo** (`attach_asset`) se hace **en el mismo turno** en que el
    usuario adjunta la imagen, **sin pedir confirmación**: al enviarla el usuario
    ya decidió. Los bytes solo están disponibles en ese turno; si esperás al turno
    siguiente, el archivo ya no se puede guardar y el usuario tendría que
    reenviarlo.
  * **El contenido derivado** (descripciones, Q&A, datos tomados de un PDF) se
    **propone** primero y se escribe con `edit_item`/`add_qa`/`add_place`
    **solo tras la confirmación** del usuario.

- **Confirmación antes de escribir (obligatoria).** Todo el contenido que derives
  de una imagen o de un PDF (descripciones, Q&A, datos históricos) es una
  **propuesta**. Presentálo primero en tu respuesta y **pedí la confirmación** del
  usuario. Recién cuando el usuario diga "sí" (o acepte tu propuesta) invocá la
  intake tool de escritura correspondiente. Si el usuario rechaza o modifica la
  propuesta, respetá su decisión y no escribas el contenido original.
  **Prohibido en el camino PDF:** NO invoques `add_qa`, `edit_item` ni `add_place`
  en el mismo turno en que llega el texto de un PDF. En ese turno solo listás las
  entradas propuestas y esperás el "sí"; escribir en ese turno además duplica el
  contenido cuando el usuario confirma.

## Correcciones

Para corregir o retirar contenido durante la charla usá `edit_item` (edita
campos de un lugar/evento por id) y `remove_item` (elimina por id).
"""


# --- Handlers de las intake tools (adaptador dict `arguments` → función) -------
# Cada handler recibe el dict `arguments` ya validado contra el `inputSchema` de
# su tool, extrae `project` como Path y desempaqueta el resto como kwargs de la
# función de intake correspondiente (DD-2). Son la fina capa de desempaquetado
# reutilizable tanto por MCP como por el loop web.


def _h_set_site(arguments: dict) -> dict:
    """Adapta `arguments` a `set_site`, mapeando el objeto `center` a kwargs."""
    project = Path(arguments["project"])
    center = arguments["center"]
    kwargs: dict[str, Any] = {
        "name": arguments["name"],
        "region": arguments["region"],
        "center_lat": center["lat"],
        "center_lng": center["lng"],
        "center_zoom": center.get("zoom"),
    }
    if "defaultLocale" in arguments:
        kwargs["default_locale"] = arguments["defaultLocale"]
    if "domain" in arguments:
        kwargs["domain"] = arguments["domain"]
    if "contact" in arguments:
        kwargs["contact"] = arguments["contact"]
    return set_site(project, **kwargs)


def _h_configure_modules(arguments: dict) -> dict:
    """Adapta `arguments` a `configure_modules`."""
    return configure_modules(
        Path(arguments["project"]), selection=arguments["selection"]
    )


def _h_configure_landing(arguments: dict) -> dict:
    """Adapta `arguments` a `configure_landing`."""
    return configure_landing(
        Path(arguments["project"]), selection=arguments["selection"]
    )


def _h_add_place(arguments: dict) -> dict:
    """Adapta `arguments` a `add_place`."""
    return add_place(
        Path(arguments["project"]),
        name=arguments["name"],
        category=arguments["category"],
        lat=arguments.get("lat"),
        lng=arguments.get("lng"),
        zoom=arguments.get("zoom"),
        address=arguments.get("address"),
        category_label=arguments.get("category_label"),
    )


def _h_add_event(arguments: dict) -> dict:
    """Adapta `arguments` a `add_event` (mapea `start_date` al kwarg homónimo)."""
    return add_event(
        Path(arguments["project"]),
        name=arguments["name"],
        start_date=arguments["start_date"],
        end_date=arguments.get("end_date"),
        place_id=arguments.get("place_id"),
        description=arguments.get("description"),
        recurring=arguments.get("recurring"),
    )


def _h_edit_item(arguments: dict) -> dict:
    """Adapta `arguments` a `edit_item`."""
    return edit_item(
        Path(arguments["project"]),
        id=arguments["id"],
        fields=arguments["fields"],
    )


def _h_remove_item(arguments: dict) -> dict:
    """Adapta `arguments` a `remove_item`."""
    return remove_item(Path(arguments["project"]), id=arguments["id"])


def _h_set_brand(arguments: dict) -> dict:
    """Adapta `arguments` a `set_brand` (solo pasa los sub-objetos provistos)."""
    kwargs: dict[str, Any] = {}
    if "colors" in arguments:
        kwargs["colors"] = arguments["colors"]
    if "typography" in arguments:
        kwargs["typography"] = arguments["typography"]
    if "voice" in arguments:
        kwargs["voice"] = arguments["voice"]
    return set_brand(Path(arguments["project"]), **kwargs)


def _h_add_qa(arguments: dict) -> dict:
    """Adapta `arguments` a `add_qa`."""
    return add_qa(
        Path(arguments["project"]),
        question=arguments["question"],
        answer=arguments["answer"],
    )


def _h_attach_asset(arguments: dict) -> dict:
    """Adapta `arguments` a `attach_asset`."""
    return attach_asset(
        Path(arguments["project"]),
        filename=arguments["filename"],
        content_base64=arguments.get("content_base64"),
        source_path=arguments.get("source_path"),
        target=arguments["target"],
        id=arguments["id"],
    )


def _h_get_guion(arguments: dict) -> dict:
    """Devuelve el guion del intake; no toma argumentos ni toca el contrato."""
    return {"guion": INTAKE_GUION}


def _h_get_state(arguments: dict) -> dict:
    """Adapta `arguments` a `get_state`."""
    return get_state(Path(arguments["project"]))


def _h_build(arguments: dict) -> dict:
    """Adapta `arguments` a `build`."""
    return build(
        Path(arguments["project"]),
        use_llm=bool(arguments.get("use_llm", True)),
    )


def _h_extract_pdf(arguments: dict) -> dict:
    """Adapta `arguments` a `extract_pdf`."""
    return extract_pdf(
        Path(arguments["project"]),
        content_base64=arguments.get("content_base64"),
        source_path=arguments.get("source_path"),
    )


# --- Fragmentos de JSON Schema reutilizados en los inputSchema -----------------
#: Propiedad `project` común a todas las intake tools (ruta del proyecto).
_PROJECT_PROP: dict[str, Any] = {
    "type": "string",
    "description": "Ruta del proyecto que contiene los tres documentos del contrato.",
}


# --- Superficie pública: specs de las intake tools (tarea 10.1) ---------------
#: Especificaciones de las intake tools como datos puros (JSON Schema, sin
#: dependencia del SDK `mcp`), con la MISMA forma que `mcp/server.TOOL_SPECS`:
#: `name`, `description` (accionable e incluyendo un extracto del guion por fases,
#: Req 13.4), `inputSchema` (objeto con `project` requerido y
#: `additionalProperties: false`, coherente con la firma de la función, Req 13.2)
#: y `handler` (adaptador `arguments → función`). La tarea 10.2 usará estos specs
#: para el despacho compartido `run_intake_tool`.
INTAKE_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "set_site",
        "description": (
            "Fase 1 (Sitio): registra la identidad del sitio en tourism-data.site "
            "(nombre, región, centro del mapa e idioma por defecto) y, si se "
            "aportan, el dominio en site-config.deploy.domain y el contacto en "
            "site-config.contact. Es el primer paso del intake: preguntá nombre, "
            "región, centro del mapa (lat/lng) e idioma antes de cargar contenido. "
            "Devuelve el estado resultante del contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "name": {
                    "type": "string",
                    "description": "Nombre del sitio turístico.",
                },
                "region": {
                    "type": "string",
                    "description": "Región o zona geográfica del sitio.",
                },
                "center": {
                    "type": "object",
                    "description": "Centro del mapa del sitio.",
                    "properties": {
                        "lat": {
                            "type": "number",
                            "description": "Latitud del centro, en el rango [-90, 90].",
                        },
                        "lng": {
                            "type": "number",
                            "description": (
                                "Longitud del centro, en el rango [-180, 180]."
                            ),
                        },
                        "zoom": {
                            "type": "integer",
                            "description": "Nivel de zoom inicial del mapa (opcional).",
                        },
                    },
                    "required": ["lat", "lng"],
                    "additionalProperties": False,
                },
                "defaultLocale": {
                    "type": "string",
                    "description": (
                        "Idioma por defecto del sitio (p. ej. 'es'). Por defecto 'es'."
                    ),
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Dirección web del sitio; se valida y se normaliza el dominio."
                    ),
                },
                "contact": {
                    "type": "object",
                    "description": "Datos de contacto del sitio (p. ej. email, teléfono).",
                },
            },
            "required": ["project", "name", "region", "center"],
            "additionalProperties": False,
        },
        "handler": _h_set_site,
    },
    {
        "name": "configure_modules",
        "description": (
            "Fase 2 (Módulos): activa y ordena los módulos del sitio (mapa, "
            "lugares, eventos, blog, asistente) a partir de una selección "
            "ordenada; el orden de la lista define el 'order' de cada módulo. "
            "Traducí lo que el usuario quiere mostrar a esta selección (ej.: "
            "'quiero lugares y eventos' → places + events). Devuelve el estado "
            "resultante del contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "selection": {
                    "type": "array",
                    "description": (
                        "Selección ordenada de módulos; la posición define el orden."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "description": "Clave del módulo del catálogo soportado.",
                                "enum": list(MODULE_CATALOG),
                            },
                            "enabled": {
                                "type": "boolean",
                                "description": (
                                    "Si el módulo está activo (por defecto true)."
                                ),
                            },
                            "label": {
                                "type": "string",
                                "description": (
                                    "Etiqueta de navegación del módulo (opcional)."
                                ),
                            },
                            "persona": {
                                "type": "string",
                                "description": (
                                    "Persona del asistente; solo válido para 'chatweb'."
                                ),
                            },
                            "knowledgeSource": {
                                "type": "string",
                                "description": (
                                    "Fuente de conocimiento; solo válido para 'chatweb'."
                                ),
                            },
                        },
                        "required": ["key"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["project", "selection"],
            "additionalProperties": False,
        },
        "handler": _h_configure_modules,
    },
    {
        "name": "add_place",
        "description": (
            "Fase 3 (Lugares): agrega un lugar turístico a tourism-data.places con "
            "su nombre y categoría; el id se deriva como slug del nombre. Si se "
            "aportan lat y lng se fija la ubicación; con solo una dirección se "
            "persiste como borrador sin inventar coordenadas. Cargá los lugares "
            "uno por uno y pedí fotos de cada uno activamente. Anexa sin borrar "
            "los lugares existentes y devuelve el estado resultante del contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "name": {
                    "type": "string",
                    "description": "Nombre del lugar (base del id slug).",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Categoría del lugar, en kebab-case. Para un destino suele "
                        "ser el tipo de atractivo ('naturaleza', 'historia'); para "
                        "un emprendimiento, el tipo de oferta ('habitaciones', "
                        "'tours', 'platos'). Si es nueva se declara sola."
                    ),
                },
                "category_label": {
                    "type": "string",
                    "description": (
                        "Nombre legible de la categoría tal como lo diría el usuario "
                        "('Habitaciones', 'Tours guiados'), usado en la navegación y "
                        "en las fichas. Solo hace falta la primera vez que aparece la "
                        "categoría; si se omite se deriva del propio id."
                    ),
                },
                "lat": {
                    "type": "number",
                    "description": "Latitud del lugar, en el rango [-90, 90] (opcional).",
                },
                "lng": {
                    "type": "number",
                    "description": (
                        "Longitud del lugar, en el rango [-180, 180] (opcional)."
                    ),
                },
                "zoom": {
                    "type": "integer",
                    "description": "Nivel de zoom del lugar en el mapa (opcional).",
                },
                "address": {
                    "type": "string",
                    "description": (
                        "Dirección del lugar; permite persistirlo como borrador si "
                        "no hay coordenadas."
                    ),
                },
            },
            "required": ["project", "name", "category"],
            "additionalProperties": False,
        },
        "handler": _h_add_place,
    },
    {
        "name": "add_event",
        "description": (
            "Fase 4 (Eventos): agrega un evento a tourism-data.events con su nombre "
            "y fecha de inicio; el id se deriva como slug del nombre. Opcionalmente "
            "acepta fecha de fin, lugar asociado, descripción y recurrencia. Anexa "
            "sin borrar los eventos existentes y devuelve el estado resultante del "
            "contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "name": {
                    "type": "string",
                    "description": "Nombre del evento (base del id slug).",
                },
                "start_date": {
                    "type": "string",
                    "description": "Fecha de inicio del evento (YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Fecha de fin del evento (YYYY-MM-DD, opcional).",
                },
                "place_id": {
                    "type": "string",
                    "description": "Id del lugar asociado al evento (opcional).",
                },
                "description": {
                    "type": "string",
                    "description": "Descripción del evento (opcional).",
                },
                "recurring": {
                    "type": "string",
                    "description": "Recurrencia del evento (opcional).",
                },
            },
            "required": ["project", "name", "start_date"],
            "additionalProperties": False,
        },
        "handler": _h_add_event,
    },
    {
        "name": "edit_item",
        "description": (
            "Corrige un lugar o evento existente por id, actualizando solo los "
            "campos indicados y preservando el resto (merge aditivo, sin regenerar "
            "el id). Usala para corregir contenido durante la charla. Un id "
            "inexistente se rechaza como 'no encontrado'. Devuelve el estado "
            "resultante del contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "id": {
                    "type": "string",
                    "description": "Id del lugar o evento a editar.",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Mapa de campos a actualizar (merge). Solo se tocan los "
                        "campos presentes."
                    ),
                },
            },
            "required": ["project", "id", "fields"],
            "additionalProperties": False,
        },
        "handler": _h_edit_item,
    },
    {
        "name": "remove_item",
        "description": (
            "Elimina un lugar o evento por id con integridad referencial: al borrar "
            "un lugar, limpia el placeId colgante de los eventos que lo "
            "referenciaban. Un id inexistente se rechaza como 'no encontrado'. "
            "Devuelve el estado resultante del contrato y los eventos afectados."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "id": {
                    "type": "string",
                    "description": "Id del lugar o evento a eliminar.",
                },
            },
            "required": ["project", "id"],
            "additionalProperties": False,
        },
        "handler": _h_remove_item,
    },
    {
        "name": "set_brand",
        "description": (
            "Fase 5 (Marca): define la identidad visual en theme-tokens: colores "
            "(hexadecimales), tipografía y voz de la marca. Proponé una paleta y "
            "pedí el logo al usuario. Un color no hexadecimal se rechaza indicando "
            "el formato esperado. Conserva lo no tocado y devuelve el estado "
            "resultante del contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "colors": {
                    "type": "object",
                    "description": (
                        "Colores de la marca (valores hexadecimales), p. ej. "
                        "primary/background/text."
                    ),
                },
                "typography": {
                    "type": "object",
                    "description": "Tipografías de la marca.",
                },
                "voice": {
                    "type": "object",
                    "description": "Voz de la marca (p. ej. tone, formality).",
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": _h_set_brand,
    },
    {
        "name": "configure_landing",
        "description": (
            "Fase 6 (Portada): arma las secciones de la portada (hero, features, "
            "cta, galería, etc.) a partir de una selección ordenada; el orden de la "
            "lista define el 'order' de cada sección. Un tipo fuera del catálogo se "
            "rechaza listando el catálogo soportado. Devuelve el estado resultante "
            "del contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "selection": {
                    "type": "array",
                    "description": (
                        "Selección ordenada de secciones; la posición define el orden."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "description": "Tipo de sección del catálogo soportado.",
                                "enum": list(LANDING_CATALOG),
                            },
                            "enabled": {
                                "type": "boolean",
                                "description": (
                                    "Si la sección está activa (por defecto true)."
                                ),
                            },
                            "content": {
                                "type": "object",
                                "description": "Campos de copy de la sección (opcional).",
                            },
                        },
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["project", "selection"],
            "additionalProperties": False,
        },
        "handler": _h_configure_landing,
    },
    {
        "name": "add_qa",
        "description": (
            "Fase 7 (Q&A): agrega una pregunta y su respuesta a la base de "
            "conocimiento del asistente (content/qa.json) y registra la fuente en "
            "site-config.modules.chatweb.knowledgeSource. Podés extraerlas de un "
            "PDF de contexto. Un campo vacío se rechaza nombrándolo. Anexa sin "
            "borrar las entradas existentes y devuelve el estado resultante."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "question": {
                    "type": "string",
                    "description": "Pregunta del QA.",
                },
                "answer": {
                    "type": "string",
                    "description": "Respuesta del QA.",
                },
            },
            "required": ["project", "question", "answer"],
            "additionalProperties": False,
        },
        "handler": _h_add_qa,
    },
    {
        "name": "attach_asset",
        "description": (
            "Fase 8 (Recursos): adjunta una imagen a assets/ y la asocia a un lugar "
            "o evento. El binario llega por base64 (content_base64) o por una ruta "
            "local legible por el servidor (source_path): indicá exactamente una. "
            "Valida formato y tamaño antes de escribir y confirma la contención en "
            "assets/. Pedí las fotos activamente. Un id inexistente se rechaza como "
            "'no encontrado'. Devuelve la ruta y el estado resultante del contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "filename": {
                    "type": "string",
                    "description": "Nombre del archivo de imagen (se normaliza a slug.ext).",
                },
                "content_base64": {
                    "type": "string",
                    "description": "Contenido de la imagen codificado en base64 (opcional).",
                },
                "source_path": {
                    "type": "string",
                    "description": (
                        "Ruta local legible por el servidor con la imagen (opcional)."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": "Tipo de elemento al que se asocia la imagen.",
                    "enum": ["place", "event"],
                },
                "id": {
                    "type": "string",
                    "description": "Id del lugar o evento al que se asocia la imagen.",
                },
            },
            "required": ["project", "filename", "target", "id"],
            "additionalProperties": False,
        },
        "handler": _h_attach_asset,
    },
    {
        "name": "get_guion",
        "description": (
            "Devuelve el guion conversacional del intake (las fases 1-9 y la regla "
            "de pedir archivos activamente). Llamalo AL EMPEZAR, antes de la "
            "primera pregunta, para saber qué preguntar y en qué orden. Es el "
            "mismo texto que sirve el recurso MCP 'intake://guion': existe también "
            "como tool porque no todos los clientes MCP leen recursos. No toma "
            "argumentos y no modifica nada."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _h_get_guion,
    },
    {
        "name": "get_state",
        "description": (
            "Devuelve (solo lectura) el estado de los tres documentos del contrato "
            "y una lista de 'missing' con las piezas requeridas aún ausentes "
            "(identidad del sitio y su campo, selección de módulos, carga de "
            "lugares, definición de marca). Es la brújula del intake: consultala al "
            "empezar y tras cada cambio para decidir qué preguntar en la fase "
            "actual. No modifica el contrato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": _h_get_state,
    },
    {
        "name": "build",
        "description": (
            "Fase 9 (Generar): construye el sitio estático a partir del contrato "
            "persistido (geocode → validación → generación de contenido → "
            "ensamblado) y devuelve la ruta del directorio dist/. Invocala cuando "
            "get_state no reporte faltantes esenciales. Un contrato incompleto o "
            "inválido devuelve un mensaje accionable sin exponer secretos."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "use_llm": {
                    "type": "boolean",
                    "description": (
                        "Si es true (por defecto) genera contenido faltante con el "
                        "LLM antes de ensamblar; si es false, omite la generación."
                    ),
                    "default": True,
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": _h_build,
    },
    {
        "name": "extract_pdf",
        "description": (
            "Recibe un PDF (folleto, ficha o historia) por base64 (content_base64) "
            "o por una ruta local legible por el servidor (source_path): indicá "
            "exactamente una fuente. Extrae su texto en memoria y lo devuelve para "
            "que puedas destilarlo a descripciones, Q&A o datos históricos con las "
            "demás intake tools (add_qa, edit_item, add_place); el PDF no se "
            "publica ni se guarda. Un PDF sin texto legible o una fuente inválida "
            "devuelven un mensaje accionable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT_PROP,
                "content_base64": {
                    "type": "string",
                    "description": "Contenido del PDF codificado en base64 (opcional).",
                },
                "source_path": {
                    "type": "string",
                    "description": (
                        "Ruta local legible por el servidor con el PDF (opcional)."
                    ),
                },
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "handler": _h_extract_pdf,
    },
]

#: Nombres de las intake tools, derivados de `INTAKE_TOOL_SPECS` (Req 13.1).
INTAKE_TOOL_NAMES: list[str] = [spec["name"] for spec in INTAKE_TOOL_SPECS]

#: Índice nombre -> handler, derivado de `INTAKE_TOOL_SPECS` (análogo a
#: `_HANDLERS` de `mcp/server.py`). Lo usa `run_intake_tool` para localizar el
#: adaptador `arguments -> función` de cada intake tool en O(1).
_INTAKE_HANDLERS: dict[str, Any] = {
    spec["name"]: spec["handler"] for spec in INTAKE_TOOL_SPECS
}

# Nombres de archivo de los documentos del contrato, definidos localmente para
# NO importar de `wizard/server.py` (frontera de imports, DD-3/DD-4). Los tres
# del contrato se derivan del mapeo canónico `contracts._DOC_FILES`
# (`tourism-data.json` / `site.config.json` / `theme.tokens.json`); `qa.json`
# es el archivo de la base de conocimiento Q&A bajo `content/`.
_TOURISM_FILE = contracts._DOC_FILES[_TOURISM]
_SITE_CONFIG_FILE = contracts._DOC_FILES[_CONFIG]
_THEME_FILE = contracts._DOC_FILES[_THEME]
_QA_FILE = "qa.json"

#: Mapeo tool -> documento del contrato afectado, para nombrar el documento
#: infractor en el mensaje de error (Req 14.2, igual criterio que el REST del
#: wizard con `documento=_TOURISM_FILE`/`_SITE_CONFIG_FILE`/`_THEME_FILE`/
#: `_QA_FILENAME`). `get_state` y `build` no escriben un documento puntual, por
#: lo que no aportan `documento` (None); su error se traduce igual de forma
#: accionable con `describir_error`.
_INTAKE_TOOL_DOCS: dict[str, str | None] = {
    "set_site": _TOURISM_FILE,
    "add_place": _TOURISM_FILE,
    "add_event": _TOURISM_FILE,
    "edit_item": _TOURISM_FILE,
    "remove_item": _TOURISM_FILE,
    "attach_asset": _TOURISM_FILE,
    "configure_modules": _SITE_CONFIG_FILE,
    "configure_landing": _SITE_CONFIG_FILE,
    "set_brand": _THEME_FILE,
    "add_qa": _QA_FILE,
    "get_state": None,
    "build": None,
    "extract_pdf": None,
}


def run_intake_tool(name: str, arguments: dict) -> dict | str:
    """Despacho compartido de las intake tools con traducción de errores (DD-5).

    Localiza el handler de la tool `name` en `_INTAKE_HANDLERS` (derivado de
    `INTAKE_TOOL_SPECS`, análogo al `_HANDLERS` de `mcp/server.py`), lo ejecuta
    con `arguments` y devuelve su resultado (`dict`). Ante **cualquier**
    excepción, la traduce con `errors.wizard_error_response(exc, documento=<doc
    afectado>)` —ya redactado y accionable— y devuelve ese resultado (Req 14.4,
    14.5). El `documento` afectado se toma de `_INTAKE_TOOL_DOCS`, que mapea cada
    tool al archivo del contrato correspondiente para nombrar el documento
    infractor (Req 14.2), igual que hace el REST del wizard.

    Como esta función es el **borde del intake** (paridad entre superficies), una
    tool desconocida NO propaga: se traduce un `ValueError` accionable que lista
    las tools disponibles (alineado con el `call_tool` de `mcp/server.py`, que
    ante una tool desconocida devuelve un mensaje descriptivo).
    """
    handler = _INTAKE_HANDLERS.get(name)
    if handler is None:
        disponibles = ", ".join(INTAKE_TOOL_NAMES)
        exc = ValueError(
            f"Tool de intake desconocida: '{name}'. "
            f"Tools disponibles: {disponibles}."
        )
        return wizard_error_response(exc)

    documento = _INTAKE_TOOL_DOCS.get(name)
    try:
        return handler(arguments or {})
    except Exception as exc:  # noqa: BLE001 - se traduce a mensaje accionable
        return wizard_error_response(exc, documento=documento)
