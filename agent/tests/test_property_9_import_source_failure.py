"""Property 9: un fallo de fuente externa preserva el documento (import_open_data).

Se ejercita `merge` sustituyendo las fronteras de red (`_query_overpass`,
`_query_wikidata`) por dobles de prueba que **fallan** (lanzan una excepción o
simulan un timeout), de modo que no hay llamadas HTTP reales. El foco es la
robustez de DD-3 / Req 2.8: cuando las fuentes de datos abiertos fallan, el
enriquecimiento es opcional y `merge` debe devolver el Tourism_Data recibido sin
cambios.
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

# Texto imprimible corto para nombres/ids/categorías.
_text = st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), max_size=12)
_id = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=10)


@st.composite
def _existing_place(draw):
    """Place ya presente en Tourism_Data (del usuario o importado previamente)."""
    place = {
        "id": draw(_id),
        "name": draw(_text.filter(lambda s: s.strip() != "")),
        "category": draw(_text.filter(lambda s: s.strip() != "")),
        "coords": {"lat": draw(_lat), "lng": draw(_lng)},
        "source": draw(st.sampled_from(["user", "osm", "wikidata"])),
    }
    # Algunos Places traen imágenes/descripciones que también deben preservarse.
    if draw(st.booleans()):
        place["images"] = draw(st.lists(_text, max_size=2))
    if draw(st.booleans()):
        place["description"] = draw(_text)
    return place


@st.composite
def _event(draw):
    """Event simple presente en Tourism_Data."""
    return {
        "id": draw(_id),
        "name": draw(_text.filter(lambda s: s.strip() != "")),
        "startDate": "2025-01-01",
    }


@st.composite
def _tourism_data(draw):
    """Tourism_Data de entrada con `site.center` y una lista `places`.

    Se incluye siempre la clave `places` (como en un documento conforme al
    esquema) para que la comparación de "documento sin cambios" sea significativa;
    opcionalmente se agregan `events` y una `description` del sitio.
    """
    data = {
        "site": {
            "name": draw(_text.filter(lambda s: s.strip() != "")),
            "region": draw(_text.filter(lambda s: s.strip() != "")),
            "center": {"lat": draw(_lat), "lng": draw(_lng)},
        },
        "places": draw(st.lists(_existing_place(), max_size=6)),
    }
    if draw(st.booleans()):
        data["events"] = draw(st.lists(_event(), max_size=3))
    if draw(st.booleans()):
        data["site"]["description"] = draw(_text)
    return data


def _raise(exc: BaseException):
    """Devuelve un side_effect que siempre lanza `exc` (simula fallo/timeout)."""

    def _side_effect(*args, **kwargs):
        raise exc

    return _side_effect


# Excepciones que representan un fallo o timeout de la frontera de red. Se usan
# excepciones estándar (no dependemos de los internos de `httpx`, que en el
# entorno de pruebas puede estar sustituido por un stub): `TimeoutError` cubre el
# agotamiento del tiempo de espera; el resto, fallos de red/servicio genéricos.
def _failures():
    return st.sampled_from(
        [
            TimeoutError("tiempo de espera agotado"),
            ConnectionError("fallo de red"),
            RuntimeError("error de servicio"),
            ValueError("respuesta inválida"),
        ]
    )


# --- Propiedad --------------------------------------------------------------

# Feature: agent-tools, Property 9: Un fallo de fuente externa preserva el documento
# Validates: Requirements 2.8
@settings(max_examples=200, deadline=None)
@given(
    data=_tourism_data(),
    overpass_exc=_failures(),
    wikidata_exc=_failures(),
)
def test_source_failure_preserves_document(data, overpass_exc, wikidata_exc):
    """Para todo Tourism_Data de entrada, si las fuentes de datos abiertos fallan
    o agotan su tiempo de espera, `import_open_data.merge` devuelve el documento
    de entrada sin cambios (Req 2.8, DD-3)."""
    original = copy.deepcopy(data)

    with mock.patch.object(
        import_open_data, "_query_overpass", side_effect=_raise(overpass_exc)
    ), mock.patch.object(
        import_open_data, "_query_wikidata", side_effect=_raise(wikidata_exc)
    ), mock.patch.object(
        import_open_data, "_image_from_commons", return_value=None
    ):
        result = import_open_data.merge(data)

    # (1) El documento devuelto es igual al recibido: un fallo de fuente externa
    # no agrega, elimina ni modifica nada del contrato (Req 2.8).
    assert result == original

    # (2) El documento de entrada no se muta como efecto colateral.
    assert data == original


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
