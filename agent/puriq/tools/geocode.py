"""geocode: convierte direcciones en coordenadas para el modulo mapa.

Proveedores de geocodificacion (patron de adaptadores, DD-4):
  - `AmazonLocationProvider`: preferido, usa Amazon Location Service via boto3
    (cliente ``location``). Se activa cuando hay un place index configurado.
  - `NominatimProvider`: fallback OSM, usa Nominatim via httpx (`NOMINATIM_URL`).

La seleccion de proveedor se resuelve por configuracion en `get_provider`
(DD-4): Amazon Location si esta configurado/disponible; si no, Nominatim.

Variables de entorno relevantes (leidas via `puriq.config.get_env`):
  - ``PURIQ_LOCATION_PLACE_INDEX``: nombre del place index de Amazon Location
    Service a usar para geocodificar. Si esta definida, se prefiere Amazon
    Location; si falta, se usa Nominatim.
  - ``AWS_REGION``: region de AWS para el cliente ``location`` (opcional; boto3
    tambien puede resolverla de su propia cadena de configuracion).

La E/S de red/servicio se aisla dentro de cada proveedor para que la logica sea
testeable/mockeable.

`fill_missing_coords` consume estos proveedores para completar las `coords`
faltantes de los Places (Req 4).
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from puriq.config import get_env

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nombre de la variable de entorno con el place index de Amazon Location Service.
# Si esta definida, `get_provider` prefiere Amazon Location; si no, Nominatim.
PLACE_INDEX_ENV = "PURIQ_LOCATION_PLACE_INDEX"

# User-Agent requerido por la politica de uso de Nominatim (OSM).
_NOMINATIM_USER_AGENT = "puriq-tourism-builder/0.1 (+https://github.com/puriq)"

# Timeout (segundos) para las peticiones de red de los proveedores.
_REQUEST_TIMEOUT = 10.0


@runtime_checkable
class GeocodeProvider(Protocol):
    """Interfaz de un proveedor de geocodificacion.

    Un proveedor convierte una direccion en coordenadas. Aisla la E/S de
    red/servicio para permitir fallback entre implementaciones y facilitar el
    mockeo en pruebas.
    """

    def geocode(self, address: str) -> dict | None:
        """Convierte una direccion en coordenadas.

        Args:
            address: direccion a resolver.

        Returns:
            Un dict ``{"lat": float, "lng": float}`` si la direccion se resuelve,
            o ``None`` si el proveedor no encuentra coordenadas.
        """
        ...


class AmazonLocationProvider:
    """Proveedor de geocodificacion via Amazon Location Service (preferido).

    Usa el cliente ``location`` de boto3 y un place index configurado por
    entorno (`PURIQ_LOCATION_PLACE_INDEX`). La creacion del cliente y la llamada
    al servicio se realizan dentro de esta clase para aislar la E/S.
    """

    def __init__(self, place_index: str, region: str | None = None):
        """Inicializa el proveedor.

        Args:
            place_index: nombre del place index de Amazon Location Service.
            region: region de AWS para el cliente (opcional).
        """
        self.place_index = place_index
        self.region = region
        self._client = None

    def _get_client(self):
        """Crea (perezosamente) el cliente boto3 ``location``.

        Aisla la dependencia de boto3 para que el modulo importe sin AWS
        configurado y para poder mockear el cliente en pruebas.
        """
        if self._client is None:
            import boto3

            kwargs = {}
            if self.region:
                kwargs["region_name"] = self.region
            self._client = boto3.client("location", **kwargs)
        return self._client

    def geocode(self, address: str) -> dict | None:
        """Geocodifica una direccion con Amazon Location Service.

        Returns:
            ``{"lat": float, "lng": float}`` con el primer resultado, o ``None``
            si el servicio no devuelve coincidencias o falla la peticion.
        """
        try:
            client = self._get_client()
            response = client.search_place_index_for_text(
                IndexName=self.place_index,
                Text=address,
                MaxResults=1,
            )
        except Exception:
            return None
        results = response.get("Results") or []
        if not results:
            return None
        point = results[0].get("Place", {}).get("Geometry", {}).get("Point")
        if not point or len(point) < 2:
            return None
        # Amazon Location devuelve el punto como [longitud, latitud].
        lng, lat = float(point[0]), float(point[1])
        return {"lat": lat, "lng": lng}


class NominatimProvider:
    """Proveedor de geocodificacion via Nominatim (OSM), usado como fallback.

    Consulta `NOMINATIM_URL` con httpx. Incluye un header ``User-Agent`` como
    exige la politica de uso de OpenStreetMap.
    """

    def __init__(self, base_url: str = NOMINATIM_URL, user_agent: str = _NOMINATIM_USER_AGENT):
        """Inicializa el proveedor.

        Args:
            base_url: endpoint de busqueda de Nominatim.
            user_agent: valor del header ``User-Agent`` (requerido por OSM).
        """
        self.base_url = base_url
        self.user_agent = user_agent

    def geocode(self, address: str) -> dict | None:
        """Geocodifica una direccion con Nominatim.

        Returns:
            ``{"lat": float, "lng": float}`` con el primer resultado, o ``None``
            si Nominatim no devuelve coincidencias o falla la peticion.
        """
        import httpx

        params = {"q": address, "format": "json", "limit": 1}
        headers = {"User-Agent": self.user_agent}
        try:
            response = httpx.get(
                self.base_url,
                params=params,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            results = response.json()
        except Exception:
            return None
        if not results:
            return None
        first = results[0]
        try:
            lat = float(first["lat"])
            lng = float(first["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        return {"lat": lat, "lng": lng}


def get_provider() -> GeocodeProvider:
    """Fabrica de proveedor de geocodificacion (DD-4).

    Selecciona el proveedor por configuracion: si hay un place index de Amazon
    Location Service configurado (`PURIQ_LOCATION_PLACE_INDEX`), usa
    `AmazonLocationProvider`; en caso contrario, usa `NominatimProvider` (OSM).

    Returns:
        Una instancia que cumple el protocolo `GeocodeProvider`.
    """
    place_index = get_env(PLACE_INDEX_ENV)
    if place_index:
        region = get_env("AWS_REGION")
        return AmazonLocationProvider(place_index=place_index, region=region)
    return NominatimProvider()


def _coords_in_range(coords: dict | None) -> bool:
    """Indica si unas coordenadas estan dentro del rango geografico valido.

    Args:
        coords: dict con claves ``lat`` y ``lng``, o ``None``.

    Returns:
        ``True`` si ``lat`` esta en [-90, 90] y ``lng`` en [-180, 180]; en
        cualquier otro caso (None, claves faltantes o valores no numericos),
        ``False``.
    """
    if not coords:
        return False
    try:
        lat = float(coords["lat"])
        lng = float(coords["lng"])
    except (KeyError, TypeError, ValueError):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def fill_missing_coords(data: dict) -> dict:
    """Completa las `coords` faltantes de los Places usando el proveedor (Req 4).

    Comportamiento por Place:
      - Con `address` y sin `coords` -> geocodifica y asigna `coords` (Req 4.1).
      - Con `coords` -> se preservan sin recalcular (Req 4.2).
      - Sin `address` -> se deja sin modificar (Req 4.3).

    Las coordenadas asignadas se validan para cumplir `lat ∈ [-90, 90]` y
    `lng ∈ [-180, 180]` (Req 4.4); un resultado fuera de rango se trata como
    no resuelto. Si la direccion es irresoluble (el proveedor devuelve ``None``
    o coordenadas fuera de rango), el Place queda sin `coords` y la direccion no
    resuelta se registra via `logging` (Req 4.7).

    La funcion es idempotente: una segunda ejecucion no altera las `coords` ya
    presentes (Propiedad 15). El proveedor se obtiene una sola vez por invocacion.

    Args:
        data: documento Tourism_Data (dict) con posible clave ``places``.

    Returns:
        El mismo ``data``, con las `coords` completadas donde correspondia.
    """
    provider = get_provider()
    for place in data.get("places", []):
        # Preservar coords existentes (Req 4.2) y omitir Places sin address (Req 4.3).
        if place.get("coords") or not place.get("address"):
            continue
        address = place["address"]
        coords = provider.geocode(address)
        if _coords_in_range(coords):
            # Normalizar a floats dentro del contrato.
            place["coords"] = {"lat": float(coords["lat"]), "lng": float(coords["lng"])}
        else:
            # Direccion irresoluble o fuera de rango -> sin coords y registro (Req 4.7).
            logger.warning("Direccion no resuelta por geocode: %s", address)
    return data
