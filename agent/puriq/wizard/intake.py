"""Lógica pura de intake de contenido del wizard (Tarea 3.1).

Constructores **puros** (sin E/S) de Place y Event para el flujo de formularios
del wizard. No leen ni escriben disco, no validan contra el esquema (eso lo hace
`save_contract` vía `puriq.schemas`) y no invocan tools: solo transforman los
datos del formulario en las porciones de `Tourism_Data` correspondientes.

Responsabilidades (Req 3.2–3.6):
  - Derivar `id = slugify(name)` para Places y Events, **reutilizando**
    `puriq.tools._slug.slugify` (sin duplicar la normalización) (Req 3.2, 3.3).
  - Si el usuario ingresa `lat`/`lng`: validar `lat ∈ [-90, 90]` y
    `lng ∈ [-180, 180]` y asignar `coords`; fuera de rango → error que indica
    el rango permitido (Req 3.5, 3.6).
  - Si el usuario ingresa solo `address` (sin coordenadas): conservar la
    dirección y **no** inventar `coords`; las completará `geocode` durante la
    generación (Req 3.4).

Al ser funciones puras, son aptas para pruebas de propiedad (Properties 5, 6, 7).
"""
from __future__ import annotations

from numbers import Real

from puriq.tools._slug import slugify

# Rangos válidos de coordenadas geográficas (WGS84), consistentes con
# `coords` en `schemas/tourism-data.schema.json` (Req 3.5).
LAT_MIN, LAT_MAX = -90.0, 90.0
LNG_MIN, LNG_MAX = -180.0, 180.0


class CoordinateRangeError(ValueError):
    """Error accionable: una latitud o longitud está fuera del rango permitido.

    Se usa en el intake (Req 3.6) para rechazar coordenadas explícitas inválidas
    con un mensaje que nombra el rango aceptado, en vez de dejar que jsonschema
    produzca un `ValidationError` crudo más adelante.
    """


def _is_number(value: object) -> bool:
    """Indica si `value` es un número real utilizable como coordenada.

    Excluye `bool` (que en Python es subclase de `int`) para que un `True`/`False`
    accidental no se tome como una coordenada válida.
    """
    return isinstance(value, Real) and not isinstance(value, bool)


def make_coords(lat: float, lng: float, zoom: int | None = None) -> dict:
    """Construye un objeto `coords` validando el rango de `lat`/`lng` (Req 3.5, 3.6).

    Args:
        lat: latitud; debe cumplir ``LAT_MIN <= lat <= LAT_MAX``.
        lng: longitud; debe cumplir ``LNG_MIN <= lng <= LNG_MAX``.
        zoom: nivel de zoom opcional; se incluye tal cual si se provee.

    Returns:
        Un dict ``{"lat": lat, "lng": lng}`` (con ``"zoom"`` si se pasó).

    Raises:
        CoordinateRangeError: si `lat` o `lng` no es numérico o está fuera de
            su rango permitido; el mensaje nombra el rango aceptado (Req 3.6).
    """
    if not _is_number(lat) or not (LAT_MIN <= lat <= LAT_MAX):
        raise CoordinateRangeError(
            f"Latitud fuera de rango: debe estar entre {LAT_MIN} y {LAT_MAX} "
            f"(recibido: {lat!r})"
        )
    if not _is_number(lng) or not (LNG_MIN <= lng <= LNG_MAX):
        raise CoordinateRangeError(
            f"Longitud fuera de rango: debe estar entre {LNG_MIN} y {LNG_MAX} "
            f"(recibido: {lng!r})"
        )
    coords: dict = {"lat": lat, "lng": lng}
    if zoom is not None:
        coords["zoom"] = zoom
    return coords


def build_place(
    name: str,
    category: str,
    *,
    lat: float | None = None,
    lng: float | None = None,
    zoom: int | None = None,
    address: str | None = None,
) -> dict:
    """Construye un Place para `Tourism_Data.places` a partir del formulario.

    Deriva ``id = slugify(name)`` (Req 3.2) e incluye `name` y `category`. Para la
    ubicación:
      - Si se proveen **ambas** coordenadas (`lat` y `lng`), se validan y se asigna
        `coords` (Req 3.5); fuera de rango se lanza `CoordinateRangeError` (Req 3.6).
      - Si solo se provee una de las dos coordenadas, se considera incompleto y se
        lanza `CoordinateRangeError` indicando que se requieren ambas.
      - Si se provee `address` (con o sin coordenadas), se conserva. Cuando no hay
        coordenadas, **no** se inventan `coords`: las completará `geocode` durante
        la generación (Req 3.4).

    Función pura: no realiza E/S ni valida contra el esquema (eso ocurre al guardar).
    """
    place: dict = {"id": slugify(name), "name": name, "category": category}

    has_lat = lat is not None
    has_lng = lng is not None
    if has_lat != has_lng:
        raise CoordinateRangeError(
            "Coordenadas incompletas: se requieren tanto la latitud como la "
            "longitud para asignar la ubicación."
        )
    if has_lat and has_lng:
        place["coords"] = make_coords(lat, lng, zoom)

    if isinstance(address, str) and address.strip():
        place["address"] = address.strip()

    return place


def build_event(
    name: str,
    start_date: str,
    *,
    end_date: str | None = None,
    place_id: str | None = None,
    description: str | None = None,
    recurring: str | None = None,
) -> dict:
    """Construye un Event para `Tourism_Data.events` a partir del formulario.

    Deriva ``id = slugify(name)`` (Req 3.3) e incluye `name` y `startDate`. Los
    campos opcionales (`endDate`, `placeId`, `description`, `recurring`) se agregan
    solo cuando se proveen, de modo que el documento no contenga claves con `None`.

    Función pura: no realiza E/S ni valida contra el esquema (eso ocurre al guardar).
    """
    event: dict = {"id": slugify(name), "name": name, "startDate": start_date}

    if end_date is not None:
        event["endDate"] = end_date
    if place_id is not None:
        event["placeId"] = place_id
    if description is not None:
        event["description"] = description
    if recurring is not None:
        event["recurring"] = recurring

    return event
