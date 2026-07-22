"""Pruebas de propiedad para la garantía de coords del pipeline (Property 22).

# Feature: agent-tools, Property 22: Coords garantizadas tras geocode o error accionable que nombra el Place
"""
from __future__ import annotations

from unittest import mock

import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq import schemas
from puriq.tools import geocode


# --- proveedor de geocoding falso (sin red) --------------------------------
#
# El comportamiento se codifica en el prefijo de la dirección para que el
# proveedor sea determinista a partir de la dirección sola (sin mapeos
# externos que puedan colisionar):
#   - "RESOLVE:..." -> devuelve coords válidas
#   - "OOR:..."     -> devuelve coords fuera de rango (se tratan como no resueltas)
#   - "FAIL:..." (o cualquier otra) -> devuelve None (dirección irresoluble)
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
    """Genera un Place en uno de cinco estados de coordenadas.

    Estados: ya tiene coords, dirección resoluble, dirección irresoluble,
    dirección que resuelve fuera de rango, y sin dirección ni coords.
    """
    state = draw(st.sampled_from(["has", "resolve", "fail", "oor", "none"]))
    place: dict = {
        "id": draw(_slug),
        "name": draw(_name),
        "category": draw(st.text(max_size=10)),
    }
    if state == "has":
        place["coords"] = draw(_coords)
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
    """Genera un Tourism_Data por lo demás válido, con Places en estados variados."""
    return {
        "site": {
            "name": draw(st.text(min_size=1, max_size=20)),
            "region": draw(st.text(min_size=1, max_size=20)),
            "defaultLocale": draw(
                st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=2)
            ),
            "center": draw(_coords),
        },
        "places": draw(st.lists(_place(), max_size=6)),
    }


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 22: Coords garantizadas tras geocode o error
# accionable que nombra el Place
@settings(max_examples=200, deadline=None)
@given(data=_tourism_data())
def test_coords_guaranteed_or_actionable_error(data):
    """Tras geocode, o todos los Places tienen coords válidas, o el pipeline
    produce un error accionable que nombra cada Place sin coords (no un error
    de esquema crudo).

    Validates: Requirements 1.9, 4.1, 4.7, 9.4
    """
    with mock.patch.object(geocode, "get_provider", return_value=_FakeProvider()):
        # Paso 1 del pipeline (DD-1): geocode completa las coords faltantes.
        filled = geocode.fill_missing_coords(data)

    # Places que siguen sin coords tras geocode, con la misma etiqueta que usa
    # el helper accionable del pipeline.
    missing_labels = [
        schemas._place_label(p)
        for p in filled.get("places", [])
        if not p.get("coords")
    ]

    # Pasos 2 y 3 del pipeline, en orden: comprobación accionable y luego
    # validación estricta contra el esquema. Capturamos el PRIMER error para
    # verificar cuál aflora.
    raised_kind = None
    raised_msg = ""
    try:
        schemas.check_places_have_coords(filled)
        schemas.validate(filled, "tourism-data")
    except schemas.MissingCoordsError as exc:
        raised_kind = "missing"
        raised_msg = str(exc)
    except jsonschema.ValidationError as exc:  # pragma: no cover - no debería ocurrir
        raised_kind = "schema"
        raised_msg = str(exc)

    if missing_labels:
        # Condición (b): error accionable que nombra cada Place sin coords, en
        # lugar de un error de validación de esquema crudo.
        assert raised_kind == "missing", (
            f"Se esperaba MissingCoordsError, afloró {raised_kind}: {raised_msg}"
        )
        for label in missing_labels:
            assert label in raised_msg
    else:
        # Condición (a): todos los Places tienen coords válidas y el documento
        # pasa la validación estricta sin error.
        assert raised_kind is None, f"Error inesperado del pipeline: {raised_msg}"
        for place in filled.get("places", []):
            assert geocode._coords_in_range(place.get("coords"))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
