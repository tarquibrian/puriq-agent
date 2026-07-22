"""Carga y validacion del contrato (los 3 JSON) contra los JSON Schema de /schemas."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

# Los schemas viven en la raiz del repo, junto al agente.
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"

_FILES = {
    "tourism-data": "tourism-data.schema.json",
    "site-config": "site-config.schema.json",
    "theme-tokens": "theme-tokens.schema.json",
    "article": "article.schema.json",
}


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / _FILES[name]).read_text())


def get_schema(name: str) -> dict:
    """Devuelve el JSON Schema del documento `name` como un dict nuevo.

    Cada llamada parsea el archivo desde disco, por lo que el resultado es una
    instancia fresca e independiente: el llamador puede derivar variantes (p. ej.
    una versión con algún `required` relajado) sin riesgo de mutar el esquema en
    disco ni ningún estado compartido. Es el punto de acceso público a los
    esquemas para quien necesite el dict (no solo validar contra él).
    """
    return _schema(name)


class MissingCoordsError(ValueError):
    """Error accionable: uno o mas Places quedaron sin `coords` tras geocode.

    Se usa como comprobacion previa a `validate` (ver DD-1 del diseno) para
    emitir un mensaje que nombra cada Place afectado, en vez de dejar que
    jsonschema produzca un ValidationError crudo sobre el campo `coords`.
    """


def _place_label(place: dict) -> str:
    """Devuelve una etiqueta legible para nombrar un Place en un mensaje de error.

    Prefiere `name`; si esta vacio o ausente, cae en `id`; y si tampoco hay id,
    usa un marcador generico.
    """
    name = place.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    place_id = place.get("id")
    if isinstance(place_id, str) and place_id.strip():
        return place_id.strip()
    return "(sin nombre)"


def check_places_have_coords(data: dict) -> None:
    """Comprueba que todos los Places tengan `coords`; si no, lanza un error accionable.

    Recorre `data["places"]` y recolecta los Places que no tienen la clave
    `coords`. Si hay alguno, lanza `MissingCoordsError` con un mensaje que
    nombra cada Place afectado (usando su `name`, o su `id` como fallback),
    p. ej. "Falta ubicacion en 'X': agrega direccion o coordenadas".

    Esta comprobacion se invoca en el pipeline **antes** de `validate`, tanto
    en `collect()` como en `build()` (ver DD-1). NO geocodifica ni valida el
    esquema: solo detecta Places sin `coords` y produce el error.
    """
    faltantes = [
        _place_label(place)
        for place in data.get("places", [])
        if not place.get("coords")
    ]
    if not faltantes:
        return
    detalles = "; ".join(
        f"Falta ubicacion en '{label}': agrega direccion o coordenadas"
        for label in faltantes
    )
    raise MissingCoordsError(detalles)


def validate(data: dict, name: str) -> None:
    """Lanza jsonschema.ValidationError si el documento no cumple el contrato."""
    jsonschema.validate(instance=data, schema=_schema(name))


def load(path: Path, name: str) -> dict:
    data = json.loads(Path(path).read_text())
    validate(data, name)
    return data


def load_raw(path: Path) -> dict:
    """Parsea un documento JSON del disco SIN validarlo contra su esquema.

    Carga tolerante (ver DD-1 del diseno): permite leer un `tourism-data.json`
    editado a mano cuyos Places pueden tener solo `address` (sin `coords`), de
    modo que pueda pasar por `geocode` antes de la validacion estricta. NO
    valida el contrato; usar `load` cuando se requiera validacion al cargar.
    """
    return json.loads(Path(path).read_text())


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
