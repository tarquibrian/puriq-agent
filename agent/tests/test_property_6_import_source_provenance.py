"""Property 6: procedencia de los datos importados por import_open_data.

Se ejercita `merge` sustituyendo las fronteras de red por dobles de prueba
(`_query_overpass`, `_query_wikidata`, `_image_from_commons`), de modo que no hay
llamadas HTTP reales; los POIs de OSM y los ítems de Wikidata los genera
Hypothesis. El foco es la procedencia (`source`) de los Places importados, que
los distingue de los Places del usuario para su revisión (Req 2.2, 2.3, 2.7).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import import_open_data  # noqa: E402


# --- Estrategias ------------------------------------------------------------

# Coordenadas finitas dentro del rango geográfico válido.
_lat = st.floats(allow_nan=False, allow_infinity=False, min_value=-90, max_value=90)
_lng = st.floats(allow_nan=False, allow_infinity=False, min_value=-180, max_value=180)

# Nombres: mezcla de válidos (no vacíos) y vacíos/espacios, para ejercitar tanto
# los POIs que se mapean a Place como los que se descartan al no tener nombre.
_name = st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), max_size=10)


@st.composite
def _osm_poi(draw):
    """POI de OpenStreetMap en formato intermedio (salida de `_query_overpass`).

    ``lat``/``lng`` pueden faltar (None) para ejercitar el descarte de POIs sin
    coordenadas; las etiquetas son un dict OSM simple.
    """
    has_coords = draw(st.booleans())
    return {
        "name": draw(_name),
        "lat": draw(_lat) if has_coords else None,
        "lng": draw(_lng) if has_coords else None,
        "tags": draw(
            st.dictionaries(
                keys=st.sampled_from(["tourism", "historic", "natural", "name"]),
                values=st.text(
                    alphabet=st.characters(min_codepoint=97, max_codepoint=122),
                    max_size=8,
                ),
                max_size=3,
            )
        ),
        "osm_type": draw(st.sampled_from(["node", "way"])),
        "osm_id": draw(st.integers(min_value=1, max_value=10**9)),
        "wikidata": None,
    }


@st.composite
def _wd_item(draw):
    """Ítem de Wikidata en formato intermedio (salida de `_query_wikidata`)."""
    has_coords = draw(st.booleans())
    return {
        "name": draw(_name),
        "lat": draw(_lat) if has_coords else None,
        "lng": draw(_lng) if has_coords else None,
        "qid": "Q" + str(draw(st.integers(min_value=1, max_value=10**7))),
        "image": None,
    }


@st.composite
def _existing_place(draw):
    """Place del usuario ya presente en Tourism_Data (fuente distinta de import)."""
    return {
        "id": draw(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=8)
        ),
        "name": draw(_name.filter(lambda s: s.strip() != "")),
        "category": "atractivo-turistico",
        "coords": {"lat": draw(_lat), "lng": draw(_lng)},
        "source": "user",
    }


# Fuentes válidas de procedencia de un Place importado (Req 2.2, 2.3).
_IMPORT_SOURCES = {"osm", "wikidata"}


# --- Propiedad --------------------------------------------------------------

# Feature: agent-tools, Property 6: Procedencia de los datos importados
# Validates: Requirements 2.2, 2.3, 2.7
@settings(max_examples=100, deadline=None)
@given(
    existing=st.lists(_existing_place(), max_size=5),
    osm_pois=st.lists(_osm_poi(), max_size=6),
    wd_items=st.lists(_wd_item(), max_size=6),
)
def test_imported_places_have_source_provenance(existing, osm_pois, wd_items):
    """Todo Place agregado por `import_open_data` lleva `source` en {"osm",
    "wikidata"} (según la fuente), lo que lo distingue de los Places del usuario
    para su revisión (Req 2.2, 2.3, 2.7)."""
    data = {
        "site": {"name": "Región", "center": {"lat": 0.0, "lng": 0.0}},
        "places": copy.deepcopy(existing),
    }

    with mock.patch.object(import_open_data, "_query_overpass", return_value=copy.deepcopy(osm_pois)), \
         mock.patch.object(import_open_data, "_query_wikidata", return_value=copy.deepcopy(wd_items)), \
         mock.patch.object(import_open_data, "_image_from_commons", return_value=None):
        result = import_open_data.merge(data)

    places = result["places"]
    n_existing = len(existing)

    # (1) Los Places del usuario se conservan al frente y sin modificarse: merge
    # solo agrega al final (Req 2.7).
    assert places[:n_existing] == existing

    # (2) Todo Place agregado (los añadidos tras los existentes) tiene
    # procedencia importada: su `source` es "osm" o "wikidata" (Req 2.2, 2.3).
    added = places[n_existing:]
    for place in added:
        assert place.get("source") in _IMPORT_SOURCES, (
            f"Place importado sin procedencia válida: {place!r}"
        )

    # (3) La procedencia distingue lo importado de lo del usuario: ningún Place
    # del usuario adquiere una `source` de importación (Req 2.7).
    for place in places[:n_existing]:
        assert place.get("source") not in _IMPORT_SOURCES


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
