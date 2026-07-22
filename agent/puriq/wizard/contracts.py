"""Capa de contrato del wizard: load / (merge) / save de los 3 JSON (DD-1).

Este módulo es el pegamento de E/S del wizard sobre `puriq.schemas`. Aplica la
invariante del diseño (DD-1): **toda escritura del contrato se valida contra su
esquema antes de persistir** (Req 7.1). Nunca se escribe un documento inválido.

Patrón completo *load → merge → validate → save*:

- `_load_contract`  -> parte de este módulo (tarea 2.1).
- `merge_document`  -> lo implementa la tarea 2.2 (frontera de import más abajo).
- `save_contract`   -> parte de este módulo (tarea 2.1).

`_load_contract` usa carga tolerante (`schemas.load_raw`) para `tourism-data`
—que puede tener Places con solo `address` y aún sin `coords`— y carga estricta
(`schemas.load`) para `site-config`/`theme-tokens`. Si el archivo no existe,
devuelve un documento base con los campos requeridos mínimos del esquema.
"""
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import jsonschema

from puriq import schemas

# --- Patrón load → merge → validate → save (DD-1) ----------------------------
# `merge_document(base, patch)` (tarea 2.2, más abajo) es la pieza pura del
# patrón. Los endpoints lo encadenan así:
#     base = _load_contract(project, doc)
#     merged = merge_document(base, patch)   # fusión aditiva/no destructiva
#     save_contract(project, doc, merged)    # valida y solo entonces escribe

# Nombres de archivo de los 3 documentos del contrato en la raíz del proyecto.
# Espejan las constantes DATA/CONFIG/THEME de `puriq.core`; se definen aquí para
# mantener la capa de contrato desacoplada de las tools que importa el core.
DATA = "tourism-data.json"
CONFIG = "site.config.json"
THEME = "theme.tokens.json"

# Mapa doc-key (igual que schemas._FILES) -> nombre de archivo en el proyecto.
_DOC_FILES: dict[str, str] = {
    "tourism-data": DATA,
    "site-config": CONFIG,
    "theme-tokens": THEME,
}


def _base_document(doc: str) -> dict:
    """Documento base con los campos requeridos mínimos de cada esquema.

    Se usa cuando el archivo del contrato aún no existe, para que la UI pueda
    arrancar con una estructura coherente que el merge del paso irá completando
    (Req 1.5). Los valores son marcadores mínimos, no contenido real.
    """
    if doc == "tourism-data":
        # Requeridos: site (name, region, defaultLocale, center), places.
        return {
            "site": {
                "name": "",
                "region": "",
                "defaultLocale": "es",
                "center": {"lat": 0, "lng": 0},
            },
            "places": [],
        }
    if doc == "site-config":
        # Requeridos: layout, modules (objeto sin propiedades requeridas).
        return {"layout": "clasico", "modules": {}}
    if doc == "theme-tokens":
        # Requeridos: colors (primary, background, text), typography (fonts).
        return {
            "colors": {
                "primary": "#000000",
                "background": "#ffffff",
                "text": "#111111",
            },
            "typography": {
                "headingFont": "sans-serif",
                "bodyFont": "sans-serif",
            },
        }
    raise ValueError(
        f"Documento de contrato desconocido: '{doc}'. "
        f"Esperado uno de: {', '.join(sorted(_DOC_FILES))}."
    )


def _doc_path(project: Path, doc: str) -> Path:
    try:
        filename = _DOC_FILES[doc]
    except KeyError:
        raise ValueError(
            f"Documento de contrato desconocido: '{doc}'. "
            f"Esperado uno de: {', '.join(sorted(_DOC_FILES))}."
        ) from None
    return Path(project) / filename


def _load_contract(project: Path, doc: str) -> dict:
    """Carga un documento del contrato desde el proyecto.

    - `tourism-data`: carga **tolerante** con `schemas.load_raw` (permite Places
      con solo `address` y sin `coords`, que `geocode` completará después).
    - `site-config` / `theme-tokens`: carga **estricta** con `schemas.load`
      (se validan al leer, no dependen de `geocode`).
    - Si el archivo no existe: devuelve `_base_document(doc)` con los campos
      requeridos mínimos (Req 1.5).
    """
    path = _doc_path(project, doc)
    if not path.exists():
        return _base_document(doc)
    if doc == "tourism-data":
        return schemas.load_raw(path)
    # site-config / theme-tokens: validación estricta al cargar.
    return schemas.load(path, doc)


# --- merge_document: fusión pura, aditiva y no destructiva (DD-1, Req 11) -----

# Clave cuyo valor no debe sobreescribirse con un parche vacío/ausente: una
# `description` ya redactada por el usuario se conserva (Req 11.3).
_DESCRIPTION_KEY = "description"


def _is_empty(value: object) -> bool:
    """Indica si un valor cuenta como «vacío» a efectos de no pisar contenido.

    Se consideran vacíos: `None`, la cadena vacía y las cadenas de solo espacios.
    Cualquier otro valor (incluido `0`, `False` o una lista/dict) se considera
    contenido presente y por tanto reemplaza explícitamente al existente.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _is_entity(item: object) -> bool:
    """Un «entity» es un dict con una clave `id` no vacía (Place/Event)."""
    return (
        isinstance(item, dict)
        and "id" in item
        and not _is_empty(item.get("id"))
    )


def _is_entity_list(value: object) -> bool:
    """Indica si `value` es una lista de entidades identificadas por `id`.

    Una lista **vacía** califica de forma vacua, para que anexar sobre una lista
    inicial vacía (o anexar nada sobre una lista existente) siga la rama de
    fusión por `id` en lugar de la de reemplazo escalar.
    """
    return isinstance(value, list) and all(_is_entity(i) for i in value)


def _merge_entity_lists(base_list: list, patch_list: list) -> list:
    """Anexa/actualiza entidades por `id` **sin eliminar** las existentes (Req 11.2).

    - Toda entidad previa se conserva (nunca se borra ni se reordena).
    - Una entidad del parche cuyo `id` **no** existe en base se **anexa** al final
      (Property 3: el resultado contiene todas las previas más la nueva).
    - Una entidad del parche cuyo `id` **coincide** con una existente se **fusiona**
      sobre ella con las mismas reglas no destructivas (Property 4: una
      `description` no vacía no se pisa; los assets referenciados se conservan
      salvo reemplazo explícito, Req 11.3, 11.4). Fusionar en lugar de duplicar
      es la desambiguación de colisiones de `id`: el resultado nunca contiene dos
      entidades con el mismo `id`.
    """
    result = [copy.deepcopy(entity) for entity in base_list]
    index: dict = {}
    for pos, entity in enumerate(result):
        index.setdefault(entity["id"], pos)

    for patch_entity in patch_list:
        pid = patch_entity["id"]
        if pid in index:
            pos = index[pid]
            result[pos] = _merge_dicts(result[pos], patch_entity)
        else:
            result.append(copy.deepcopy(patch_entity))
            index[pid] = len(result) - 1
    return result


def _merge_field(key: str | None, base_value: object, patch_value: object) -> object:
    """Fusiona un único valor decidiendo entre recursión, anexado o reemplazo."""
    # `description` no vacía nunca se sobreescribe con un parche vacío (Req 11.3).
    if key == _DESCRIPTION_KEY and _is_empty(patch_value) and not _is_empty(base_value):
        return copy.deepcopy(base_value)

    # Dos dicts: fusión recursiva y no destructiva.
    if isinstance(base_value, dict) and isinstance(patch_value, dict):
        return _merge_dicts(base_value, patch_value)

    # Dos listas de entidades (Places/Events): anexar/actualizar por `id`.
    if _is_entity_list(base_value) and _is_entity_list(patch_value):
        return _merge_entity_lists(base_value, patch_value)

    # Escalar, lista de escalares o tipos dispares: el parche reemplaza de forma
    # explícita (el usuario proveyó un valor nuevo para esa clave, Req 11.4).
    return copy.deepcopy(patch_value)


def _merge_dicts(base: dict, patch: dict) -> dict:
    """Fusiona `patch` sobre `base` de forma recursiva y no destructiva.

    Las claves que el parche no toca se conservan sin cambios (Req 11.1); las que
    aparecen en ambos se fusionan según `_merge_field`; las nuevas se agregan.
    """
    result = copy.deepcopy(base)
    for key, patch_value in patch.items():
        if key in result:
            result[key] = _merge_field(key, result[key], patch_value)
        else:
            result[key] = copy.deepcopy(patch_value)
    return result


def merge_document(base: dict, patch: dict) -> dict:
    """Fusiona `patch` sobre `base` de forma aditiva y no destructiva (DD-1, Req 11).

    Función **pura** (sin E/S, no muta sus argumentos): es la pieza del patrón
    *load → merge → validate → save* apta para pruebas de propiedad. Reglas:

    - **Preservación** (Req 11.1): las claves que el parche no toca quedan
      idénticas a las de `base`, en cualquier nivel de anidamiento.
    - **Anexado por `id`** (Req 11.2): las listas de Places/Events se fusionan por
      `id` slug: las entradas nuevas se anexan y **ninguna existente se elimina**;
      una colisión de `id` se resuelve fusionando (no se duplican ids).
    - **Descripciones** (Req 11.3): una `description` no vacía no se sobreescribe
      con un parche cuya descripción es vacía o ausente.
    - **Assets** (Req 11.4): los valores existentes (p. ej. `images`/`logo`) se
      conservan salvo que el parche los reemplace explícitamente aportando un
      valor nuevo para esa clave.

    Si `base` o `patch` no es un dict, se devuelve una copia del parche cuando
    éste aporta contenido, o de la base en caso contrario (reemplazo explícito).
    """
    if not isinstance(base, dict) or not isinstance(patch, dict):
        if patch is None:
            return copy.deepcopy(base)
        return copy.deepcopy(patch)
    return _merge_dicts(base, patch)


def _field_from_validation_error(exc: jsonschema.ValidationError) -> str:
    """Deriva una ruta legible del campo infractor a partir del error de jsonschema."""
    path = list(exc.absolute_path)
    if not path:
        # Error a nivel raíz (p. ej. una clave requerida ausente).
        return exc.message
    return ".".join(str(p) for p in path)


def _relaxed_tourism_schema() -> dict:
    """Esquema de `tourism-data` con `coords` **opcional** en cada Place (DD-1, Req 3.4).

    Al guardar `tourism-data`, un Place puede tener legítimamente solo `address`
    y todavía **sin** `coords`: `geocode` completará las coordenadas después,
    durante el build (ver DD-1 paso 3). Por eso la validación previa a escribir
    NO debe exigir `coords` en cada Place. Todas las demás reglas estructurales
    se conservan intactas (requeridos `id`/`name`/`category`, patrón de `id`,
    tipos, `additionalProperties`, rangos de `lat`/`lng` cuando hay `coords`,
    campos de evento, etc.).

    Se parte de una copia profunda del esquema en disco (`schemas.get_schema`
    ya devuelve una instancia fresca; el `deepcopy` es defensa explícita) y solo
    se elimina `"coords"` de la lista `required` del `$defs.place`. No se muta el
    esquema en disco ni ningún estado compartido.
    """
    schema = copy.deepcopy(schemas.get_schema("tourism-data"))
    place = schema.get("$defs", {}).get("place", {})
    required = place.get("required")
    if isinstance(required, list):
        place["required"] = [key for key in required if key != "coords"]
    return schema


def _validate_for_save(doc: str, merged: dict) -> None:
    """Valida `merged` para escritura, aplicando la relajación de coords a tourism-data.

    - `tourism-data`: se valida contra `_relaxed_tourism_schema()` (coords opcional),
      reutilizando el mismo validador/format-behavior que usa `schemas.validate`
      (`jsonschema.validate` sin format checker). Todo el resto de la estructura
      se sigue exigiendo.
    - `site-config` / `theme-tokens`: validación **estricta** sin cambios, vía
      `schemas.validate` (Req 2.4, 2.5, 6.5, 7.1).

    Propaga `jsonschema.ValidationError` en caso de incumplimiento.
    """
    if doc == "tourism-data":
        jsonschema.validate(instance=merged, schema=_relaxed_tourism_schema())
        return
    schemas.validate(merged, doc)


def save_contract(project: Path, doc: str, merged: dict) -> None:
    """Valida `merged` contra su esquema y **solo entonces** lo escribe (Req 7.1).

    Si la validación falla, no se escribe **nada** y se propaga un `ValueError`
    que nombra el documento y el campo infractor (Req 7.2). La escritura es
    atómica (archivo temporal + `os.replace`) para no dejar el contrato a medias.

    Para `tourism-data` la validación es **relajada** en un único punto: `coords`
    deja de ser obligatorio por Place (un draft con solo `address` es válido; las
    coords las completará `geocode` en el build, DD-1 / Req 3.4). Cualquier otra
    regla estructural sigue vigente. `site-config` y `theme-tokens` se validan de
    forma estricta, sin cambios.
    """
    path = _doc_path(project, doc)
    try:
        _validate_for_save(doc, merged)
    except jsonschema.ValidationError as exc:
        field = _field_from_validation_error(exc)
        raise ValueError(
            f"Documento '{doc}' inválido en el campo '{field}': {exc.message}. "
            f"No se escribió nada."
        ) from exc

    payload = schemas.dumps(merged)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica: temp en el mismo directorio + os.replace.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        # Si algo falla tras crear el temp, no dejar basura ni tocar el destino.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
