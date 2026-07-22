"""import_open_data: enriquece los datos con fuentes abiertas (opcional).

Fuentes: OpenStreetMap (Overpass), Wikidata, Wikimedia Commons.
El usuario siempre revisa y aprueba lo importado; se marca con source="osm"/"wikidata".

Este módulo aísla la **frontera de red** hacia las fuentes abiertas (las llamadas
HTTP crudas y su parseo a estructuras intermedias). El mapeo de esas estructuras a
Place, la deduplicación y el marcado con `source` los hace `merge` (tarea 6.2).

Contrato de las funciones de frontera:
  - `_query_overpass(center, radius_m)`  -> POIs turísticos de OpenStreetMap.
  - `_query_wikidata(center)`            -> lugares/metadatos de Wikidata.
  - `_image_from_commons(entity)`        -> URL de imagen libre en Wikimedia Commons.

Todas usan `httpx` con timeouts razonables y un `User-Agent` identificable (política
de uso de OSM/Overpass y de la Wikimedia Foundation). Estas funciones **propagan**
las excepciones de red de `httpx`; el manejo tolerante a fallos (DD-3, Req 2.8) se
cablea en `merge` (tarea 6.2).
"""
from __future__ import annotations

import copy
import logging
import math
from difflib import SequenceMatcher

import httpx

from puriq.tools._slug import slugify

logger = logging.getLogger(__name__)

# --- Endpoints de las fuentes abiertas -------------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
# Special:FilePath resuelve un nombre de archivo de Commons a la URL de la imagen.
COMMONS_FILEPATH_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/"

# --- Configuración de red ---------------------------------------------------

# User-Agent identificable, requerido por la política de uso de Overpass/OSM y de
# los servicios de la Wikimedia Foundation (Wikidata/Commons). Incluir un contacto
# o URL del proyecto es una buena práctica de ciudadanía con estas APIs públicas.
USER_AGENT = "Puriq/0.1 (agente de sitios turísticos; +https://github.com/puriq)"

# Timeouts razonables: Overpass puede tardar en zonas densas; Wikidata SPARQL
# también. Se usan segundos y se dejan holgados para no cortar consultas válidas.
OVERPASS_TIMEOUT_S = 60.0
WIKIDATA_TIMEOUT_S = 60.0

# Radio por defecto (en km) para la consulta geográfica a Wikidata, que no recibe
# radio explícito en su firma. Aproxima el área de una región/municipio.
WIKIDATA_RADIUS_KM = 25.0

# --- Configuración de mapeo y deduplicación (usada por `merge`) -------------

# Radio por defecto (en metros) para la consulta Overpass alrededor de
# `site.center`. Se elige 25 km para cubrir el área de una región/municipio,
# consistente con `WIKIDATA_RADIUS_KM` (25 km) para que ambas fuentes cubran
# aproximadamente la misma zona (Req 2.1).
DEFAULT_OVERPASS_RADIUS_M = 25_000

# Criterio de deduplicación (Req 2.6): un POI importado se considera duplicado de
# un Place existente si (a) sus nombres son similares —ratio de similitud de
# `difflib` >= NAME_SIMILARITY_THRESHOLD sobre los nombres normalizados a slug— y
# (b) están geográficamente próximos —distancia haversine <= DEDUP_DISTANCE_M
# metros—. Ambas condiciones deben cumplirse para descartar el importado y
# conservar el existente sin modificarlo. 200 m es un umbral razonable para
# distinguir dos POIs distintos con nombres parecidos dentro de una ciudad.
NAME_SIMILARITY_THRESHOLD = 0.85
DEDUP_DISTANCE_M = 200.0

# Categoría por defecto cuando la fuente no permite derivar una (Req 2.9). Para
# OSM se deriva de las etiquetas (`tourism`/`historic`/`natural`); si no hay
# información útil (o para Wikidata, que no trae tipo en el formato intermedio)
# se usa este fallback documentado.
DEFAULT_CATEGORY = "atractivo-turistico"

# Categorías OSM consideradas "turísticas" para la consulta Overpass. Se incluyen
# `tourism=*` e `historic=*` completos, y un subconjunto relevante de `natural=*`
# (accidentes geográficos de interés turístico, no cualquier vegetación/agua).
_OSM_TOURIST_TAGS = ("tourism", "historic")
_OSM_NATURAL_VALUES = (
    "peak",
    "volcano",
    "waterfall",
    "hot_spring",
    "geyser",
    "cave_entrance",
    "beach",
    "spring",
    "cliff",
    "glacier",
)


def _overpass_query(center: dict, radius_m: int) -> str:
    """Construye la consulta Overpass QL para POIs turísticos alrededor de `center`.

    Usa el filtro `(around:radius,lat,lng)` para acotar por proximidad e incluye
    nodos y vías con `tourism=*`, `historic=*` y valores relevantes de `natural=*`.
    `out center tags;` devuelve las etiquetas y, para las vías, un punto `center`
    con las coordenadas representativas.
    """
    lat = float(center["lat"])
    lng = float(center["lng"])
    around = f"(around:{int(radius_m)},{lat},{lng})"

    parts: list[str] = []
    for key in _OSM_TOURIST_TAGS:
        parts.append(f'  node["{key}"]{around};')
        parts.append(f'  way["{key}"]{around};')
    natural_regex = "|".join(_OSM_NATURAL_VALUES)
    parts.append(f'  node["natural"~"^({natural_regex})$"]{around};')
    body = "\n".join(parts)
    return f"[out:json][timeout:50];\n(\n{body}\n);\nout center tags;"


def _query_overpass(center: dict, radius_m: int) -> list[dict]:
    """Consulta Overpass por POIs turísticos alrededor de `center`.

    Args:
        center: coordenada central de la región, ``{"lat": float, "lng": float}``
            (típicamente ``Tourism_Data.site.center``).
        radius_m: radio de búsqueda en metros alrededor de `center`.

    Returns:
        Lista de POIs en formato intermedio (aún NO son Places). Cada dict tiene:
            - ``name`` (str): nombre del POI (``tags.name``) o ``""`` si no tiene.
            - ``lat`` / ``lng`` (float | None): coordenadas del nodo o del
              ``center`` de la vía.
            - ``tags`` (dict): etiquetas OSM crudas del elemento.
            - ``osm_type`` (str): ``"node"`` o ``"way"``.
            - ``osm_id`` (int): identificador del elemento en OSM.
            - ``wikidata`` (str | None): Q-id de Wikidata (``tags.wikidata``) si existe.

    Propaga excepciones de `httpx` (timeout, error de red/estado); su captura es
    responsabilidad de `merge` (tarea 6.2, DD-3).
    """
    query = _overpass_query(center, radius_m)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    response = httpx.post(
        OVERPASS_URL,
        data={"data": query},
        headers=headers,
        timeout=OVERPASS_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()

    results: list[dict] = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {}) or {}
        lat = element.get("lat")
        lng = element.get("lon")
        if lat is None or lng is None:
            # Las vías (way) no traen lat/lon propios; usan el punto `center`.
            center_pt = element.get("center") or {}
            lat = center_pt.get("lat")
            lng = center_pt.get("lon")
        results.append(
            {
                "name": (tags.get("name") or "").strip(),
                "lat": float(lat) if lat is not None else None,
                "lng": float(lng) if lng is not None else None,
                "tags": tags,
                "osm_type": element.get("type"),
                "osm_id": element.get("id"),
                "wikidata": tags.get("wikidata"),
            }
        )
    return results


def _wikidata_sparql(center: dict, radius_km: float) -> str:
    """Construye la consulta SPARQL de lugares con coordenadas cerca de `center`.

    Usa el servicio geoespacial ``wikibase:around`` sobre la propiedad de
    coordenadas (P625) y trae, de forma opcional, la imagen (P18) y la etiqueta
    legible del ítem en español/inglés.
    """
    lat = float(center["lat"])
    lng = float(center["lng"])
    # WKT usa el orden Point(lng lat).
    return (
        "SELECT ?item ?itemLabel ?coord ?image WHERE {\n"
        "  SERVICE wikibase:around {\n"
        "    ?item wdt:P625 ?coord .\n"
        f'    bd:serviceParam wikibase:center "Point({lng} {lat})"^^geo:wktLiteral .\n'
        f'    bd:serviceParam wikibase:radius "{radius_km}" .\n'
        "  }\n"
        "  OPTIONAL { ?item wdt:P18 ?image. }\n"
        '  SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". }\n'
        "}\n"
        "LIMIT 200"
    )


def _parse_wkt_point(wkt: str) -> tuple[float | None, float | None]:
    """Extrae ``(lat, lng)`` de un literal WKT ``Point(lng lat)`` de Wikidata."""
    try:
        inner = wkt[wkt.index("(") + 1 : wkt.index(")")]
        lng_str, lat_str = inner.split()
        return float(lat_str), float(lng_str)
    except (ValueError, IndexError):
        return None, None


def _query_wikidata(center: dict) -> list[dict]:
    """Consulta Wikidata por lugares/metadatos de la región alrededor de `center`.

    Args:
        center: coordenada central de la región, ``{"lat": float, "lng": float}``.
            El radio de búsqueda es ``WIKIDATA_RADIUS_KM`` (no se recibe por firma).

    Returns:
        Lista de lugares en formato intermedio (aún NO son Places). Cada dict tiene:
            - ``name`` (str): etiqueta del ítem (``itemLabel``) o ``""``.
            - ``lat`` / ``lng`` (float | None): coordenadas del ítem (P625).
            - ``qid`` (str): identificador del ítem en Wikidata (p. ej. ``"Q42"``).
            - ``image`` (str | None): URL de la imagen (P18) si el ítem la declara.

    Propaga excepciones de `httpx`; su captura es responsabilidad de `merge`
    (tarea 6.2, DD-3).
    """
    query = _wikidata_sparql(center, WIKIDATA_RADIUS_KM)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}
    response = httpx.get(
        WIKIDATA_SPARQL_URL,
        params={"query": query, "format": "json"},
        headers=headers,
        timeout=WIKIDATA_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()

    results: list[dict] = []
    for binding in payload.get("results", {}).get("bindings", []):
        item_uri = binding.get("item", {}).get("value", "")
        qid = item_uri.rsplit("/", 1)[-1] if item_uri else ""
        coord_wkt = binding.get("coord", {}).get("value", "")
        lat, lng = _parse_wkt_point(coord_wkt) if coord_wkt else (None, None)
        image = binding.get("image", {}).get("value") or None
        results.append(
            {
                "name": (binding.get("itemLabel", {}).get("value") or "").strip(),
                "lat": lat,
                "lng": lng,
                "qid": qid,
                "image": image,
            }
        )
    return results


def _image_from_commons(entity) -> str | None:
    """Devuelve la URL de una imagen de licencia libre en Wikimedia Commons.

    Acepta una entidad/POI en formato intermedio (de `_query_overpass` o de
    `_query_wikidata`) y resuelve su imagen de Commons, o ``None`` si no hay:
        - Wikidata: si la entidad trae ``image`` (URL de P18, ya sobre Commons),
          se devuelve tal cual.
        - OSM: si las etiquetas incluyen ``wikimedia_commons`` con un archivo
          (``File:Nombre.jpg``), se construye la URL vía ``Special:FilePath``.
          Como alternativa, si hay una etiqueta ``image`` que ya es una URL, se
          devuelve esa URL.

    Las imágenes de Commons son de licencia libre por política del proyecto, por lo
    que resolverlas desde ``wikimedia_commons``/P18 satisface el requisito de imagen
    de licencia libre (Req 2.4).
    """
    if not isinstance(entity, dict):
        return None

    # Wikidata P18: ya es una URL sobre Commons.
    image = entity.get("image")
    if isinstance(image, str) and image.startswith("http"):
        return image

    tags = entity.get("tags") or {}

    # OSM: etiqueta wikimedia_commons -> "File:Algo.jpg" | "Category:...".
    commons = tags.get("wikimedia_commons")
    if commons:
        prefix, sep, filename = commons.partition(":")
        # Solo los archivos (File:/Image:) resuelven a una imagen concreta.
        if sep and prefix.strip().lower() in {"file", "image"} and filename.strip():
            return _commons_file_url(filename.strip())

    # OSM: etiqueta image que ya es una URL directa.
    osm_image = tags.get("image")
    if isinstance(osm_image, str) and osm_image.startswith("http"):
        return osm_image

    return None


def _commons_file_url(filename: str) -> str:
    """Construye la URL de descarga de un archivo de Commons vía Special:FilePath."""
    # Commons usa '_' en lugar de espacios; el resto lo maneja el servidor.
    return COMMONS_FILEPATH_URL + filename.replace(" ", "_")


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distancia haversine aproximada en metros entre dos coordenadas.

    Aproximación esférica (radio medio de la Tierra = 6 371 km), suficiente para
    el criterio de proximidad de la deduplicación (Req 2.6): no se necesita la
    precisión de un elipsoide para decidir si dos POIs son "el mismo lugar".
    """
    radius_m = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(a))


def _name_similarity(a: str, b: str) -> float:
    """Similitud [0, 1] entre dos nombres, normalizados a slug antes de comparar.

    Slugificar normaliza acentos, mayúsculas y puntuación, de modo que
    "Cerro Rico" y "cerro-rico" (o "Cerro  Rico!") se comparen de forma robusta.
    """
    return SequenceMatcher(None, slugify(a), slugify(b)).ratio()


def _is_duplicate(candidate: dict, existing: list[dict]) -> bool:
    """Indica si `candidate` coincide con algún Place de `existing` (Req 2.6).

    Coincidencia = nombre similar (>= NAME_SIMILARITY_THRESHOLD) Y proximidad
    geográfica (<= DEDUP_DISTANCE_M metros). Si el candidato o el existente no
    tienen `coords`, no puede afirmarse proximidad y no se consideran duplicados.
    """
    cand_coords = candidate.get("coords")
    if not cand_coords:
        return False
    for place in existing:
        place_coords = place.get("coords")
        if not place_coords:
            continue
        if _name_similarity(candidate.get("name", ""), place.get("name", "")) < (
            NAME_SIMILARITY_THRESHOLD
        ):
            continue
        distance = _haversine_m(
            cand_coords["lat"],
            cand_coords["lng"],
            place_coords["lat"],
            place_coords["lng"],
        )
        if distance <= DEDUP_DISTANCE_M:
            return True
    return False


def _unique_id(name: str, taken: set[str]) -> str:
    """Genera un id slug único que no colisiona con `taken` (Req 2.5).

    Desambigua con un sufijo numérico incremental (`-2`, `-3`, ...). Si el nombre
    no produce slug (p. ej. solo símbolos), usa "lugar" como base.
    """
    base = slugify(name) or "lugar"
    candidate = base
    counter = 2
    while candidate in taken:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _osm_category(tags: dict) -> str:
    """Deriva una categoría (slug) de las etiquetas OSM, con fallback (Req 2.9).

    Prioriza `tourism` e `historic` con valor específico (distinto de "yes"),
    luego `natural`; si solo hay `tourism=yes`/`historic=yes` usa una categoría
    genérica, y si no hay señal usa `DEFAULT_CATEGORY`.
    """
    for key in ("tourism", "historic"):
        value = (tags.get(key) or "").strip()
        if value and value != "yes":
            return slugify(value) or DEFAULT_CATEGORY
    natural = (tags.get("natural") or "").strip()
    if natural:
        return slugify(natural) or DEFAULT_CATEGORY
    if tags.get("tourism"):
        return "atractivo-turistico"
    if tags.get("historic"):
        return "sitio-historico"
    return DEFAULT_CATEGORY


def _osm_to_place(poi: dict) -> dict | None:
    """Mapea un POI de OSM (formato intermedio) a un Place `source="osm"`.

    Descarta POIs sin nombre o sin coordenadas (Req 2.9). El `id` se asigna luego
    en `merge` para garantizar unicidad global (Req 2.5).
    """
    name = (poi.get("name") or "").strip()
    lat, lng = poi.get("lat"), poi.get("lng")
    if not name or lat is None or lng is None:
        return None
    tags = poi.get("tags") or {}
    place: dict = {
        "name": name,
        "category": _osm_category(tags),
        "coords": {"lat": float(lat), "lng": float(lng)},
        "source": "osm",
    }
    return place


def _wikidata_to_place(item: dict) -> dict | None:
    """Mapea un ítem de Wikidata (formato intermedio) a un Place `source="wikidata"`.

    Descarta ítems sin nombre o sin coordenadas (Req 2.9). Wikidata no aporta un
    tipo utilizable en el formato intermedio, por lo que se usa `DEFAULT_CATEGORY`.
    """
    name = (item.get("name") or "").strip()
    lat, lng = item.get("lat"), item.get("lng")
    if not name or lat is None or lng is None:
        return None
    place: dict = {
        "name": name,
        "category": DEFAULT_CATEGORY,
        "coords": {"lat": float(lat), "lng": float(lng)},
        "source": "wikidata",
    }
    return place


def _attach_image(place: dict, entity: dict) -> None:
    """Adjunta a `place["images"]` la imagen de Commons de `entity`, si existe (Req 2.4).

    La resolución de la imagen es una frontera (`_image_from_commons`): un fallo
    aquí no debe impedir importar el Place, solo se omite la imagen (DD-3).
    """
    try:
        url = _image_from_commons(entity)
    except Exception as exc:  # frontera: nunca aborta el enriquecimiento
        logger.warning("Fallo al resolver imagen de Commons para %r: %s", place.get("name"), exc)
        return
    if url:
        place["images"] = [url]


def merge(data: dict) -> dict:
    """Devuelve `data` enriquecido con Places de fuentes abiertas (OSM/Wikidata).

    Consulta Overpass y Wikidata por POIs dentro del área de ``data["site"]["center"]``
    (radio `DEFAULT_OVERPASS_RADIUS_M` / `WIKIDATA_RADIUS_KM`), los mapea a Place
    marcándolos con `source="osm"`/`"wikidata"` (Req 2.2, 2.3, 2.7), adjunta la
    imagen de Wikimedia Commons cuando existe (Req 2.4), genera ids slug únicos
    que no colisionan con los existentes (Req 2.5) y descarta duplicados por
    nombre + proximidad conservando el Place existente (Req 2.6).

    Robustez (Req 2.8, DD-3): cada fuente se consulta de forma independiente y
    protegida; si una falla (timeout u otra excepción) se registra la causa y se
    continúa con las demás. Si todas fallan (o no hay `site.center`), no se agrega
    nada y se devuelve `data` sin cambios. Un fallo de enriquecimiento nunca aborta.

    La salida es conforme a `tourism-data.schema.json` (Req 2.9): los Places
    importados llevan `id`, `name`, `category` y `coords`; los que carezcan de
    nombre o coordenadas se descartan.
    """
    site = data.get("site") or {}
    center = site.get("center") or {}
    if "lat" not in center or "lng" not in center:
        logger.warning(
            "import_open_data: sin 'site.center' con lat/lng; se omite el enriquecimiento."
        )
        return data

    # Se trabaja sobre una copia para no dejar `data` en un estado parcial si una
    # fuente falla a mitad del proceso; solo se devuelve enriquecido si hubo éxito.
    result = copy.deepcopy(data)
    places: list[dict] = result.setdefault("places", [])
    taken_ids: set[str] = {p["id"] for p in places if isinstance(p, dict) and "id" in p}

    # (candidato_place, entidad_origen) para resolver la imagen tras el mapeo.
    candidates: list[tuple[dict, dict]] = []

    # --- Fuente 1: OpenStreetMap / Overpass (Req 2.1, 2.2) ---
    try:
        osm_pois = _query_overpass(center, DEFAULT_OVERPASS_RADIUS_M)
    except Exception as exc:  # httpx u otra: se registra y se continúa (Req 2.8)
        logger.warning("Fallo al consultar Overpass (OSM): %s", exc)
        osm_pois = []
    for poi in osm_pois:
        place = _osm_to_place(poi)
        if place is not None:
            candidates.append((place, poi))

    # --- Fuente 2: Wikidata (Req 2.1, 2.3) ---
    try:
        wd_items = _query_wikidata(center)
    except Exception as exc:  # httpx u otra: se registra y se continúa (Req 2.8)
        logger.warning("Fallo al consultar Wikidata: %s", exc)
        wd_items = []
    for item in wd_items:
        place = _wikidata_to_place(item)
        if place is not None:
            candidates.append((place, item))

    # --- Mapeo final: dedup, id único, imagen y marcado ---
    for place, entity in candidates:
        if _is_duplicate(place, places):
            # Duplicado de un Place existente (o ya importado): se omite y se
            # conserva el existente sin modificarlo (Req 2.6).
            continue
        place["id"] = _unique_id(place["name"], taken_ids)
        taken_ids.add(place["id"])
        _attach_image(place, entity)
        places.append(place)

    return result
