"""Prueba de propiedad para el rango valido de coords asignadas (Property 16).

# Feature: agent-tools, Property 16: Las coordenadas asignadas están en rango válido
"""
from __future__ import annotations

from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.tools import geocode


# --- estrategias de coordenadas -------------------------------------------
# Coords dentro del rango geografico valido (lat en [-90, 90], lng en [-180, 180]).
_valid_coords = st.fixed_dictionaries(
    {
        "lat": st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False),
        "lng": st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False),
    }
)


@st.composite
def _oor_coords(draw):
    """Genera coords que un proveedor podria devolver fuera de rango.

    Se fuerza que al menos uno de los ejes quede fuera de su rango valido, para
    ejercitar el caso en que el proveedor entrega coordenadas invalidas.
    """
    lat_oor = draw(
        st.one_of(
            st.floats(min_value=90.0001, max_value=1e6, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-1e6, max_value=-90.0001, allow_nan=False, allow_infinity=False),
        )
    )
    lng_oor = draw(
        st.one_of(
            st.floats(min_value=180.0001, max_value=1e6, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-1e6, max_value=-180.0001, allow_nan=False, allow_infinity=False),
        )
    )
    lat_valid = draw(st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False))
    lng_valid = draw(st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False))
    which = draw(st.sampled_from(["lat", "lng", "both"]))
    if which == "lat":
        return {"lat": lat_oor, "lng": lng_valid}
    if which == "lng":
        return {"lat": lat_valid, "lng": lng_oor}
    return {"lat": lat_oor, "lng": lng_oor}


# Resultado que el proveedor puede devolver para una direccion: coords validas,
# coords fuera de rango, o None (direccion irresoluble).
_provider_result = st.one_of(st.none(), _valid_coords, _oor_coords())


# --- proveedor de geocoding falso (sin red) --------------------------------
#
# Devuelve las coords generadas por Hypothesis para cada direccion, mapeadas por
# direccion. Reutiliza el patron de fake-provider (mock de geocode.get_provider)
# de test_geocode_coords_pipeline_property.py, pero las coords las dirige
# Hypothesis (incluyendo valores fuera de rango) en lugar de estar fijas.
class _FakeProvider:
    def __init__(self, results_by_address: dict):
        self._results = results_by_address

    def geocode(self, address: str) -> dict | None:
        return self._results.get(address)


@st.composite
def _scenario(draw):
    """Genera un Tourism_Data cuyos Places tienen direccion y sin coords, junto
    con el mapeo direccion -> resultado del proveedor.

    Cada Place recibe una direccion unica (por indice) para evitar colisiones en
    el mapeo del proveedor.
    """
    results = draw(st.lists(_provider_result, max_size=6))
    places = []
    results_by_address: dict = {}
    for i, res in enumerate(results):
        address = f"addr-{i}"
        places.append(
            {
                "id": f"p{i}",
                "name": f"Place {i}",
                "category": "cat",
                "address": address,
            }
        )
        results_by_address[address] = res
    data = {
        "site": {
            "name": draw(st.text(min_size=1, max_size=20)),
            "region": draw(st.text(min_size=1, max_size=20)),
            "defaultLocale": draw(
                st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=2)
            ),
            "center": draw(_valid_coords),
        },
        "places": places,
    }
    return data, results_by_address


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 16: Las coordenadas asignadas están en rango válido
@settings(max_examples=200, deadline=None)
@given(scenario=_scenario())
def test_assigned_coords_are_in_valid_range(scenario):
    """Toda coordenada asignada por geocode esta en rango valido.

    Para toda direccion resuelta, las `coords` asignadas cumplen
    `lat ∈ [-90, 90]` y `lng ∈ [-180, 180]` (Req 4.1). Si el proveedor devuelve
    coords fuera de rango, el Place queda sin `coords` (se trata como no
    resuelta) (Req 4.4).

    Validates: Requirements 4.1, 4.4
    """
    data, results_by_address = scenario

    with mock.patch.object(geocode, "get_provider", return_value=_FakeProvider(results_by_address)):
        filled = geocode.fill_missing_coords(data)

    for place in filled["places"]:
        address = place["address"]
        provider_result = results_by_address[address]
        coords = place.get("coords")

        if coords is not None:
            # Req 4.1: toda coord asignada esta dentro del rango geografico valido.
            assert -90.0 <= coords["lat"] <= 90.0
            assert -180.0 <= coords["lng"] <= 180.0
            assert geocode._coords_in_range(coords)

        if provider_result is None or not geocode._coords_in_range(provider_result):
            # Req 4.4: direccion irresoluble o coords fuera de rango -> sin coords.
            assert coords is None
        else:
            # Direccion resuelta con coords validas -> se asignan (normalizadas a float).
            assert coords == {
                "lat": float(provider_result["lat"]),
                "lng": float(provider_result["lng"]),
            }


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
