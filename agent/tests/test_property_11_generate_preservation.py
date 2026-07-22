"""Prueba de propiedad para la preservacion del contenido existente (Property 11).

# Feature: agent-tools, Property 11: Preservación del contenido existente
"""
from __future__ import annotations

import copy
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.tools import generate_content


# --- proveedor de LLM falso (sin llamadas reales) --------------------------
#
# Devuelve SIEMPRE un texto centinela distinto de cualquier `description`
# generada por las estrategias. Asi, si `enrich` sobreescribiera por error una
# descripcion ya existente, el texto centinela lo delataria (la asercion de
# preservacion fallaria). No realiza E/S ni toca la red/LLM.
_SENTINEL = "__LLM_FAKE_GENERATED_TEXT__"


class _FakeProvider:
    def complete(self, prompt: str) -> str:
        return _SENTINEL


# --- estrategias -----------------------------------------------------------
_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"
_slug = st.text(alphabet=_SLUG_ALPHABET, min_size=1, max_size=10)
_name = st.text(min_size=1, max_size=15)

# Descripcion "no vacia": al menos un caracter que no sea espacio en blanco, y
# nunca igual al centinela del proveedor falso (para que el contraste sea real).
_non_empty_desc = (
    st.text(min_size=1, max_size=40)
    .filter(lambda s: bool(s.strip()) and s != _SENTINEL)
)

# Descripcion "vacia" segun `_is_blank`: ausente (None), cadena vacia o solo
# espacios. Se modela con None y cadenas en blanco.
_blank_desc = st.one_of(st.none(), st.just(""), st.text(alphabet=" \t\n", max_size=4))


@st.composite
def _place(draw):
    """Genera un Place con `description` vacia o no vacia (o ausente)."""
    place: dict = {"id": draw(_slug), "name": draw(_name)}
    choice = draw(st.sampled_from(["non_empty", "blank", "missing"]))
    if choice == "non_empty":
        place["description"] = draw(_non_empty_desc)
    elif choice == "blank":
        place["description"] = draw(_blank_desc)
    # "missing": sin clave `description`
    return place


@st.composite
def _event(draw):
    """Genera un Event con `description` vacia o no vacia (o ausente)."""
    event: dict = {"id": draw(_slug), "name": draw(_name)}
    choice = draw(st.sampled_from(["non_empty", "blank", "missing"]))
    if choice == "non_empty":
        event["description"] = draw(_non_empty_desc)
    elif choice == "blank":
        event["description"] = draw(_blank_desc)
    return event


@st.composite
def _tourism_data(draw):
    """Genera un Tourism_Data con Places y Events en estados variados.

    Se usa un unico Locale (sin traducciones extra) para centrar la propiedad
    en la preservacion de descripciones; el bloque `site.description` se deja
    definido para no invocar la rama SEO.
    """
    return {
        "site": {
            "name": draw(st.text(min_size=1, max_size=20)),
            "region": draw(st.text(min_size=1, max_size=20)),
            "description": draw(_non_empty_desc),
            "defaultLocale": "es",
            "locales": ["es"],
        },
        "places": draw(st.lists(_place(), max_size=5)),
        "events": draw(st.lists(_event(), max_size=5)),
    }


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 11: Preservación del contenido existente
@settings(max_examples=200, deadline=None)
@given(data=_tourism_data())
def test_enrich_preserves_existing_descriptions(data):
    """`generate_content.enrich` conserva toda `description` no vacia sin tocarla.

    Para cada Place o Event cuyo `description` no esta en blanco, el texto se
    mantiene identico tras el enriquecimiento (Req 3.3). El proveedor de LLM se
    reemplaza por uno falso que devuelve un texto centinela, de modo que
    cualquier sobreescritura indebida seria detectable.

    Validates: Requirements 3.3
    """
    original = copy.deepcopy(data)

    def _has_text(item: dict) -> bool:
        value = item.get("description")
        return isinstance(value, str) and bool(value.strip())

    with mock.patch.object(
        generate_content, "get_provider", return_value=_FakeProvider()
    ):
        result = generate_content.enrich(data)

    for orig, new in zip(original["places"], result["places"]):
        if _has_text(orig):
            assert new["description"] == orig["description"]

    for orig, new in zip(original["events"], result["events"]):
        if _has_text(orig):
            assert new["description"] == orig["description"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
