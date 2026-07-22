"""Prueba de propiedad 7 (spec agent-tools): unicidad de ids tras la importación.

Aísla la frontera de red (`_query_overpass`, `_query_wikidata`,
`_image_from_commons`) con stubs deterministas y ejercita la generación de ids
slug únicos de `merge`. Los POIs generados por Hypothesis tienen nombres que
pueden colisionar entre sí y con los `id` de Places ya existentes, forzando la
desambiguación por sufijo numérico (`_unique_id`).
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
from puriq.tools._slug import slugify  # noqa: E402


# --- Estrategias ------------------------------------------------------------

# Pequeño repertorio de nombres cuyos slugs colisionan entre sí y con los ids de
# los Places existentes. Al reusar un conjunto acotado, muchos POIs y Places
# comparten el mismo slug base, forzando la ruta de desambiguación de ids.
_NAME_POOL = [
    "Cerro Rico",
    "cerro rico",
    "CERRO  RICO!",
    "Plaza 10 de Noviembre",
    "plaza 10 de noviembre",
    "Casa de la Moneda",
    "casa-de-la-moneda",
    "Lugar",
    "@@@",  # slug vacío -> base "lugar"
    "  ",   # sólo espacios -> nombre inválido para POIs
]

_name = st.sampled_from(_NAME_POOL)

# Slugs base derivados del repertorio, para sembrar ids existentes que colisionen
# con los slugs de los POIs importados.
_SLUG_POOL = sorted({slugify(n) or "lugar" for n in _NAME_POOL})

_lat = st.floats(min_value=-89.0, max_value=89.0, allow_nan=False, allow_infinity=False)
_lng = st.floats(
    min_value=-179.0, max_value=179.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _existing_place(draw):
    """Un Place ya presente en Tourism_Data con un `id` que puede colisionar.

    El `id` se toma del repertorio de slugs (con posibles sufijos ya usados)
    para que la importación tenga que evitar chocar con ids existentes (Req 2.5).
    """
    base = draw(st.sampled_from(_SLUG_POOL))
    suffix = draw(st.sampled_from(["", "", "-2", "-3", "-10"]))
    return {
        "id": f"{base}{suffix}",
        "name": draw(_name),
        "category": "existente",
        "coords": {"lat": draw(_lat), "lng": draw(_lng)},
        "source": "user",
    }


@st.composite
def _osm_poi(draw):
    """POI de OSM (formato intermedio) con nombre potencialmente colisionante."""
    return {
        "name": draw(_name),
        "lat": draw(_lat),
        "lng": draw(_lng),
        "tags": {},
    }


@st.composite
def _wikidata_item(draw):
    """Ítem de Wikidata (formato intermedio) con nombre potencialmente colisionante."""
    return {
        "name": draw(_name),
        "lat": draw(_lat),
        "lng": draw(_lng),
        "qid": draw(st.text(alphabet="Q0123456789", min_size=2, max_size=8)),
        "image": None,
    }


@st.composite
def _scenario(draw):
    """Genera (existing_places, osm_pois, wikidata_items).

    Todos comparten un repertorio acotado de nombres/slugs, de modo que los ids
    generados para los importados chocan frecuentemente entre sí y con los ids
    existentes, ejercitando la desambiguación (Req 2.5).
    """
    # Precondición válida: los Places existentes ya tienen ids únicos entre sí
    # (Tourism_Data bien formado). `merge` garantiza que lo *importado* no rompa
    # esa unicidad; no puede reparar duplicados preexistentes en la entrada.
    existing = draw(
        st.lists(_existing_place(), max_size=6, unique_by=lambda p: p["id"])
    )
    osm_pois = draw(st.lists(_osm_poi(), max_size=8))
    wd_items = draw(st.lists(_wikidata_item(), max_size=8))
    return existing, osm_pois, wd_items


# --- Propiedad --------------------------------------------------------------

# Feature: agent-tools, Property 7: Unicidad de ids tras la importación
# Validates: Requirements 2.5
@settings(max_examples=200, deadline=None)
@given(scenario=_scenario())
def test_import_produces_unique_ids(scenario):
    """Para todo Tourism_Data resultante de `import_open_data.merge`, los `id` de
    todos los Places son únicos: ningún id importado colisiona con otro importado
    ni con uno ya existente (Req 2.5)."""
    existing, osm_pois, wd_items = scenario
    data = {
        "site": {"name": "T", "region": "R", "center": {"lat": 0.0, "lng": 0.0}},
        "places": copy.deepcopy(existing),
    }

    with mock.patch.object(import_open_data, "_query_overpass", return_value=osm_pois), \
         mock.patch.object(import_open_data, "_query_wikidata", return_value=wd_items), \
         mock.patch.object(import_open_data, "_image_from_commons", return_value=None):
        result = import_open_data.merge(data)

    ids = [p["id"] for p in result["places"] if isinstance(p, dict) and "id" in p]

    # (1) Todos los Places del resultado tienen `id` (los importados también).
    assert len(ids) == len(
        [p for p in result["places"] if isinstance(p, dict)]
    ), "Algún Place quedó sin `id` tras la importación"

    # (2) Los ids son globalmente únicos (sin colisiones importado/importado ni
    # importado/existente).
    assert len(ids) == len(set(ids)), (
        f"Se detectaron ids duplicados tras la importación: "
        f"{sorted([i for i in ids if ids.count(i) > 1])}"
    )


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
