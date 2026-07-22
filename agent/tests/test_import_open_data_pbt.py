"""Pruebas de propiedad para import_open_data (spec agent-tools).

Aísla la frontera de red (`_query_overpass`, `_query_wikidata`,
`_image_from_commons`) con stubs deterministas y ejercita la lógica de
deduplicación por nombre + proximidad de `merge`.
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

# Nombres slug-ables: al menos una letra ASCII para que slugify() no quede vacío
# y la comparación de similitud sea estable.
_letters = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_name = st.text(alphabet=_letters, min_size=1, max_size=20).filter(
    lambda s: s.strip() != ""
)

# Coordenadas existentes acotadas lejos de los polos, para poder generar un
# duplicado "cercano" desplazando lat/lng sin salirse del rango [-90, 90].
_lat = st.floats(min_value=-80.0, max_value=80.0, allow_nan=False, allow_infinity=False)
_lng = st.floats(min_value=-179.0, max_value=179.0, allow_nan=False, allow_infinity=False)

# Desplazamiento pequeño (grados) para fabricar un POI dentro del radio de
# deduplicación (DEDUP_DISTANCE_M = 200 m). 0.0005° de latitud ~= 55 m; en
# longitud el metraje es <= al de latitud (cos(lat) <= 1), así que la distancia
# combinada se mantiene holgadamente por debajo de 200 m.
_small_offset = st.floats(
    min_value=-0.0005, max_value=0.0005, allow_nan=False, allow_infinity=False
)


@st.composite
def _existing_place(draw):
    """Un Place ya presente en Tourism_Data (source='user') con id y coords."""
    name = draw(_name)
    return {
        "id": draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
                           min_size=1, max_size=12)),
        "name": name,
        "category": "existente",
        "coords": {"lat": draw(_lat), "lng": draw(_lng)},
        "source": "user",
    }


def _poi_from_place(place: dict, dlat: float, dlng: float) -> dict:
    """POI de OSM (formato intermedio) cercano y homónimo de `place`.

    Mismo nombre (similitud 1.0) y coords desplazadas dentro del radio de
    deduplicación -> debe ser tratado como duplicado del Place existente.
    """
    lat = max(-90.0, min(90.0, place["coords"]["lat"] + dlat))
    lng = max(-180.0, min(180.0, place["coords"]["lng"] + dlng))
    return {"name": place["name"], "lat": lat, "lng": lng, "tags": {}}


@st.composite
def _random_poi(draw):
    """POI de OSM arbitrario (puede o no coincidir con un existente)."""
    return {
        "name": draw(_name),
        "lat": draw(st.floats(min_value=-90, max_value=90,
                              allow_nan=False, allow_infinity=False)),
        "lng": draw(st.floats(min_value=-180, max_value=180,
                              allow_nan=False, allow_infinity=False)),
        "tags": {},
    }


@st.composite
def _scenario(draw):
    """Genera (existing_places, osm_pois).

    Los POIs son una mezcla de: duplicados fabricados a partir de Places
    existentes (homónimos y cercanos) y POIs arbitrarios; así se cubren tanto
    la rama de omisión de duplicados como la de importación real.
    """
    existing = draw(st.lists(_existing_place(), max_size=5))

    pois: list[dict] = []
    # Duplicados derivados de los existentes.
    if existing:
        n_dupes = draw(st.integers(min_value=0, max_value=len(existing)))
        for _ in range(n_dupes):
            base = draw(st.sampled_from(existing))
            pois.append(_poi_from_place(base, draw(_small_offset), draw(_small_offset)))
    # POIs arbitrarios.
    pois.extend(draw(st.lists(_random_poi(), max_size=6)))
    # Barajado determinista por Hypothesis para no fijar un orden dupe/no-dupe.
    draw(st.randoms(use_true_random=False)).shuffle(pois)
    return existing, pois


# --- Propiedad --------------------------------------------------------------

# Feature: agent-tools, Property 8: La importación no duplica y preserva lo existente
# Validates: Requirements 2.6
@settings(max_examples=200, deadline=None)
@given(scenario=_scenario())
def test_import_dedups_and_preserves_existing(scenario):
    """Para todo POI importado que coincide con un Place existente por nombre y
    proximidad geográfica, `merge` omite el duplicado y conserva el Place
    existente sin modificarlo (Req 2.6)."""
    existing, osm_pois = scenario
    data = {
        "site": {"name": "T", "region": "R", "center": {"lat": 0.0, "lng": 0.0}},
        "places": copy.deepcopy(existing),
    }
    original_existing = copy.deepcopy(existing)

    with mock.patch.object(import_open_data, "_query_overpass", return_value=osm_pois), \
         mock.patch.object(import_open_data, "_query_wikidata", return_value=[]), \
         mock.patch.object(import_open_data, "_image_from_commons", return_value=None):
        result = import_open_data.merge(data)

    result_places = result["places"]

    # (1) Preservación: los Places existentes siguen presentes, en orden y sin
    # modificar (merge solo agrega al final).
    assert result_places[: len(original_existing)] == original_existing

    # (2) No duplicación: ningún Place agregado por la importación coincide con
    # un Place existente por nombre + proximidad. Si lo hiciera, debería haberse
    # omitido conservando el existente (Req 2.6).
    added = result_places[len(original_existing):]
    for place in added:
        assert not import_open_data._is_duplicate(place, original_existing), (
            f"Place importado {place.get('name')!r} @ {place.get('coords')} "
            f"es duplicado de un existente y no debió agregarse"
        )


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
