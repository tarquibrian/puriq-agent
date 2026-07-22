"""Prueba de propiedad para la idempotencia de geocode (Property 15).

# Feature: agent-tools, Property 15: Geocode solo completa lo faltante
"""
from __future__ import annotations

import copy
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.tools import geocode


# --- proveedor de geocoding falso (sin red) --------------------------------
#
# Determinista a partir del prefijo de la direccion, para no realizar llamadas
# de red reales (patron reutilizado de test_geocode_coords_pipeline_property.py):
#   - "RESOLVE:..." -> coords validas
#   - "OOR:..."     -> coords fuera de rango (tratadas como no resueltas)
#   - cualquier otra -> None (direccion irresoluble)
class _FakeProvider:
    def geocode(self, address: str) -> dict | None:
        if address.startswith("RESOLVE:"):
            return {"lat": 10.0, "lng": 20.0}
        if address.startswith("OOR:"):
            return {"lat": 999.0, "lng": 999.0}
        return None


# --- estrategias -----------------------------------------------------------
_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"

_coords = st.fixed_dictionaries(
    {
        "lat": st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False),
        "lng": st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False),
    }
)

_slug = st.text(alphabet=_SLUG_ALPHABET, min_size=1, max_size=10)
_name = st.text(min_size=1, max_size=15)
_suffix = st.text(min_size=0, max_size=5)


@st.composite
def _place(draw):
    """Genera un Place en uno de varios estados de coordenadas/direccion.

    Estados: ya tiene coords (con o sin address), direccion resoluble,
    direccion irresoluble, direccion que resuelve fuera de rango, y sin
    direccion ni coords. Se incluyen combinaciones que emulan documentos
    editados a mano cargados de forma tolerante en build().
    """
    state = draw(
        st.sampled_from(
            ["has", "has_with_addr", "resolve", "fail", "oor", "none"]
        )
    )
    place: dict = {
        "id": draw(_slug),
        "name": draw(_name),
        "category": draw(st.text(max_size=10)),
    }
    if state == "has":
        place["coords"] = draw(_coords)
    elif state == "has_with_addr":
        # Place editado a mano: ya tiene coords y ademas conserva un address.
        place["coords"] = draw(_coords)
        place["address"] = "RESOLVE:" + draw(_suffix)
    elif state == "resolve":
        place["address"] = "RESOLVE:" + draw(_suffix)
    elif state == "fail":
        place["address"] = "FAIL:" + draw(_suffix)
    elif state == "oor":
        place["address"] = "OOR:" + draw(_suffix)
    # state == "none": sin address ni coords
    return place


@st.composite
def _tourism_data(draw):
    """Genera un Tourism_Data con Places en estados variados.

    Modela tambien documentos cargados de forma tolerante (build()): la clave
    ``places`` puede faltar por completo.
    """
    data: dict = {
        "site": {
            "name": draw(st.text(min_size=1, max_size=20)),
            "region": draw(st.text(min_size=1, max_size=20)),
            "defaultLocale": draw(
                st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=2)
            ),
            "center": draw(_coords),
        },
    }
    if draw(st.booleans()):
        data["places"] = draw(st.lists(_place(), max_size=6))
    return data


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 15: Geocode solo completa lo faltante
@settings(max_examples=200, deadline=None)
@given(data=_tourism_data())
def test_geocode_fills_only_missing_and_is_idempotent(data):
    """geocode.fill_missing_coords solo completa lo faltante y es idempotente.

    Preserva las `coords` de Places que ya las tienen (Req 4.2), no modifica
    Places sin `address` (Req 4.3), y aplicarlo dos veces produce el mismo
    resultado (idempotencia).

    Validates: Requirements 4.2, 4.3
    """
    original = copy.deepcopy(data)

    with mock.patch.object(geocode, "get_provider", return_value=_FakeProvider()):
        first = geocode.fill_missing_coords(copy.deepcopy(original))

        # Req 4.2 / 4.3: revisar cada Place contra su estado original.
        orig_places = original.get("places", [])
        first_places = first.get("places", [])
        assert len(first_places) == len(orig_places)
        for orig_place, new_place in zip(orig_places, first_places):
            if orig_place.get("coords"):
                # Req 4.2: coords existentes se conservan sin recalcular.
                assert new_place.get("coords") == orig_place["coords"]
            elif not orig_place.get("address"):
                # Req 4.3: Place sin address queda sin modificar.
                assert new_place == orig_place

        # Idempotencia: aplicar de nuevo no cambia el resultado.
        second = geocode.fill_missing_coords(copy.deepcopy(first))
        assert second == first


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
