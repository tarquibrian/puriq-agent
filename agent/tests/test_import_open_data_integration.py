"""Pruebas de integración de las fronteras de red de import_open_data (Req 2.1).

Verifican que `_query_overpass` y `_query_wikidata` parsean respuestas HTTP
realistas (mockeadas) al formato intermedio de POI que consume `merge`. El mock
se coloca en la **frontera** `httpx` (se sustituyen `httpx.post`/`httpx.get` por
dobles que devuelven una respuesta falsa), de modo que no ocurre ninguna llamada
de red real. `conftest.py` ya sustituye el módulo `httpx` por un stub cuando no
está instalado, así que aquí solo se inyectan `post`/`get` sobre esa referencia.

Ejemplos cubiertos (1-3): un POI de OSM tipo `node` y tipo `way` desde Overpass,
y dos ítems geolocalizados desde Wikidata (con y sin imagen).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import import_open_data  # noqa: E402


class _FakeResponse:
    """Respuesta HTTP falsa mínima compatible con el uso de `httpx` en el módulo.

    Reproduce el contrato que consumen `_query_overpass`/`_query_wikidata`:
    `raise_for_status()` (aquí no-op, simula 200 OK) y `json()` (devuelve el
    payload que emularía el cuerpo JSON de la respuesta real).
    """

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:  # 200 OK simulado
        return None

    def json(self) -> dict:
        return self._payload


# --- Payloads realistas mockeados ------------------------------------------

# Respuesta típica de Overpass (formato `[out:json]`): un `node` con lat/lon
# propios y una `way` cuyas coordenadas vienen en `center` (Potosí, Bolivia).
_OVERPASS_PAYLOAD = {
    "version": 0.6,
    "generator": "Overpass API",
    "elements": [
        {
            "type": "node",
            "id": 1234567,
            "lat": -19.5847,
            "lon": -65.7534,
            "tags": {
                "name": "Cerro Rico",
                "tourism": "attraction",
                "wikidata": "Q1165362",
                "wikimedia_commons": "File:Cerro Rico Potosi.jpg",
            },
        },
        {
            "type": "way",
            "id": 9876543,
            "center": {"lat": -19.5891, "lon": -65.7539},
            "tags": {
                "name": "Casa Nacional de la Moneda",
                "historic": "museum",
            },
        },
        {
            # Elemento sin nombre: se conserva en el formato intermedio con
            # name="" (el descarte por falta de nombre lo hace `merge`, no aquí).
            "type": "node",
            "id": 555,
            "lat": -19.60,
            "lon": -65.76,
            "tags": {"natural": "peak"},
        },
    ],
}

# Respuesta típica de Wikidata SPARQL (`application/sparql-results+json`): dos
# ítems geolocalizados, uno con imagen (P18) y otro sin ella.
_WIKIDATA_PAYLOAD = {
    "head": {"vars": ["item", "itemLabel", "coord", "image"]},
    "results": {
        "bindings": [
            {
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1165362"},
                "itemLabel": {"type": "literal", "value": "Cerro Rico"},
                "coord": {"type": "literal", "value": "Point(-65.7534 -19.5847)"},
                "image": {
                    "type": "uri",
                    "value": "http://commons.wikimedia.org/wiki/Special:FilePath/Cerro%20Rico.jpg",
                },
            },
            {
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2743"},
                "itemLabel": {"type": "literal", "value": "Laguna Colorada"},
                "coord": {"type": "literal", "value": "Point(-67.7920 -22.1889)"},
                # Sin clave "image": el ítem no declara P18.
            },
        ]
    },
}


# --- Ejemplo 1: _query_overpass parsea la respuesta de Overpass -------------

def test_query_overpass_parses_nodes_and_ways():
    """`_query_overpass` mapea la respuesta JSON de Overpass al formato POI:
    toma lat/lon del nodo o del `center` de la vía, conserva las etiquetas y
    extrae `osm_type`, `osm_id` y `wikidata` (Req 2.1)."""
    center = {"lat": -19.58, "lng": -65.75}

    with mock.patch.object(
        import_open_data.httpx, "post", return_value=_FakeResponse(_OVERPASS_PAYLOAD), create=True
    ) as post:
        pois = import_open_data._query_overpass(center, radius_m=25_000)

    # La consulta se envió al endpoint de Overpass (frontera de red ejercida).
    assert post.call_count == 1
    assert post.call_args.args[0] == import_open_data.OVERPASS_URL

    assert len(pois) == 3

    # Nodo con coords propias, wikidata y etiquetas preservadas.
    cerro = pois[0]
    assert cerro["name"] == "Cerro Rico"
    assert cerro["lat"] == -19.5847
    assert cerro["lng"] == -65.7534
    assert cerro["osm_type"] == "node"
    assert cerro["osm_id"] == 1234567
    assert cerro["wikidata"] == "Q1165362"
    assert cerro["tags"]["tourism"] == "attraction"

    # Vía (way): las coordenadas se toman del punto `center`.
    moneda = pois[1]
    assert moneda["name"] == "Casa Nacional de la Moneda"
    assert moneda["lat"] == -19.5891
    assert moneda["lng"] == -65.7539
    assert moneda["osm_type"] == "way"
    assert moneda["osm_id"] == 9876543
    assert moneda["wikidata"] is None

    # Elemento sin nombre -> name normalizado a "" en el formato intermedio.
    sin_nombre = pois[2]
    assert sin_nombre["name"] == ""
    assert sin_nombre["tags"]["natural"] == "peak"


# --- Ejemplo 2: _query_wikidata parsea la respuesta SPARQL ------------------

def test_query_wikidata_parses_bindings():
    """`_query_wikidata` mapea los `bindings` de SPARQL al formato POI: extrae el
    Q-id del URI del ítem, parsea el WKT `Point(lng lat)` a lat/lng y recoge la
    imagen (P18) cuando existe (Req 2.1)."""
    center = {"lat": -19.58, "lng": -65.75}

    with mock.patch.object(
        import_open_data.httpx, "get", return_value=_FakeResponse(_WIKIDATA_PAYLOAD), create=True
    ) as get:
        items = import_open_data._query_wikidata(center)

    # La consulta se envió al endpoint SPARQL de Wikidata (frontera ejercida).
    assert get.call_count == 1
    assert get.call_args.args[0] == import_open_data.WIKIDATA_SPARQL_URL

    assert len(items) == 2

    # Ítem con imagen: Q-id derivado del URI y WKT Point(lng lat) -> (lat, lng).
    cerro = items[0]
    assert cerro["name"] == "Cerro Rico"
    assert cerro["qid"] == "Q1165362"
    assert cerro["lat"] == -19.5847
    assert cerro["lng"] == -65.7534
    assert cerro["image"] == (
        "http://commons.wikimedia.org/wiki/Special:FilePath/Cerro%20Rico.jpg"
    )

    # Ítem sin P18: image es None.
    laguna = items[1]
    assert laguna["name"] == "Laguna Colorada"
    assert laguna["qid"] == "Q2743"
    assert laguna["lat"] == -22.1889
    assert laguna["lng"] == -67.7920
    assert laguna["image"] is None


# --- Ejemplo 3: la salida parseada alimenta el mapeo que consume `merge` ----

def test_parsed_pois_feed_merge_mapping():
    """El formato intermedio producido por las fronteras es exactamente el que
    consumen los mapeadores de `merge`: los POIs parseados de Overpass/Wikidata
    se convierten en Places con `source`, `coords` y `category` (Req 2.1)."""
    center = {"lat": -19.58, "lng": -65.75}

    with mock.patch.object(
        import_open_data.httpx, "post", return_value=_FakeResponse(_OVERPASS_PAYLOAD), create=True
    ):
        osm_pois = import_open_data._query_overpass(center, radius_m=25_000)
    with mock.patch.object(
        import_open_data.httpx, "get", return_value=_FakeResponse(_WIKIDATA_PAYLOAD), create=True
    ):
        wd_items = import_open_data._query_wikidata(center)

    # Un POI de OSM con nombre y coords se mapea a un Place source="osm".
    osm_place = import_open_data._osm_to_place(osm_pois[0])
    assert osm_place is not None
    assert osm_place["source"] == "osm"
    assert osm_place["name"] == "Cerro Rico"
    assert osm_place["coords"] == {"lat": -19.5847, "lng": -65.7534}
    assert osm_place["category"]  # categoría derivada de las etiquetas OSM

    # El POI de OSM sin nombre se descarta en el mapeo (Req 2.9).
    assert import_open_data._osm_to_place(osm_pois[2]) is None

    # Un ítem de Wikidata con nombre y coords se mapea a un Place source="wikidata".
    wd_place = import_open_data._wikidata_to_place(wd_items[0])
    assert wd_place is not None
    assert wd_place["source"] == "wikidata"
    assert wd_place["name"] == "Cerro Rico"
    assert wd_place["coords"] == {"lat": -19.5847, "lng": -65.7534}


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
