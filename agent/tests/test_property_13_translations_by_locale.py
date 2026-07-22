"""Prueba de propiedad para las traducciones por locale configurado (Property 13).

# Feature: agent-tools, Property 13: Traducciones por locale configurado
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
# Cumple el protocolo `LLMProvider`: su `complete()` devuelve SIEMPRE texto no
# vacio, de modo que `enrich`/`generate_translations` puedan producir la
# traduccion de cada texto no vacio sin tocar Amazon Bedrock ni Ollama. El
# texto centinela permite verificar que la traduccion efectivamente se genero.
_SENTINEL = "__LLM_FAKE_TRANSLATION__"


class _FakeProvider:
    def complete(self, prompt: str) -> str:
        return _SENTINEL


# --- estrategias -----------------------------------------------------------
_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"
_slug = st.text(alphabet=_SLUG_ALPHABET, min_size=1, max_size=10)
_name = st.text(min_size=1, max_size=15)

# Pool de codigos de Locale ISO 639-1 plausibles para un sitio de turismo.
_LOCALE_POOL = ["es", "en", "pt", "fr", "de", "it", "qu", "ay", "gn"]

# Texto no vacio (segun `_is_blank`): al menos un caracter no-espacio, y nunca
# igual al centinela, para que el contraste con la traduccion sea real.
_non_blank = (
    st.text(min_size=1, max_size=40)
    .filter(lambda s: bool(s.strip()) and s != _SENTINEL)
)


@st.composite
def _place(draw):
    """Genera un Place con `description` no vacia (traducible)."""
    return {
        "id": draw(_slug),
        "name": draw(_name),
        "description": draw(_non_blank),
    }


@st.composite
def _event(draw):
    """Genera un Event con `description` no vacia (traducible)."""
    return {
        "id": draw(_slug),
        "name": draw(_name),
        "description": draw(_non_blank),
    }


@st.composite
def _locales(draw):
    """Genera (defaultLocale, locales) con `locales` de mas de un elemento.

    Garantiza que `locales` contenga el `defaultLocale` y al menos un Locale
    distinto, de modo que exista contenido traducible a un Locale extra.
    """
    default = draw(st.sampled_from(_LOCALE_POOL))
    otros_pool = [loc for loc in _LOCALE_POOL if loc != default]
    extras = draw(
        st.lists(st.sampled_from(otros_pool), min_size=1, max_size=4, unique=True)
    )
    locales = [default] + extras
    # Mezclar el orden para no asumir que el default va primero.
    draw(st.randoms()).shuffle(locales)
    return default, locales


@st.composite
def _tourism_data(draw):
    """Genera un Tourism_Data con mas de un Locale y contenido traducible.

    `site.description` se deja no vacio (traducible) para que cada Locale extra
    reciba, como minimo, la traduccion del sitio.
    """
    default, locales = draw(_locales())
    return {
        "site": {
            "name": draw(st.text(min_size=1, max_size=20)),
            "region": draw(st.text(min_size=1, max_size=20)),
            "description": draw(_non_blank),
            "defaultLocale": default,
            "locales": locales,
        },
        "places": draw(st.lists(_place(), max_size=4)),
        "events": draw(st.lists(_event(), max_size=4)),
    }


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 13: Traducciones por locale configurado
@settings(max_examples=200, deadline=None)
@given(data=_tourism_data())
def test_enrich_translates_for_each_extra_locale(data):
    """`enrich` produce contenido traducido para cada Locale != defaultLocale.

    Para todo Tourism_Data cuyo `site.locales` tenga mas de un elemento, tras
    `enrich` (con un LLM_Provider que responde texto no vacio) el documento
    companion `i18n` contiene una entrada por cada Locale distinto del
    `defaultLocale`, con la traduccion del texto no vacio del sitio (Req 3.6).

    Validates: Requirements 3.6
    """
    original = copy.deepcopy(data)
    site = original["site"]
    default_locale = site["defaultLocale"]
    expected_locales = {loc for loc in site["locales"] if loc and loc != default_locale}

    with mock.patch.object(
        generate_content, "get_provider", return_value=_FakeProvider()
    ):
        result = generate_content.enrich(copy.deepcopy(original))

    i18n = result.get(generate_content.I18N_KEY, {})

    # Se genera exactamente una entrada por Locale extra (sin el defaultLocale).
    assert set(i18n.keys()) == expected_locales
    assert default_locale not in i18n

    # Cada Locale extra trae la traduccion del texto no vacio del sitio (Req 3.6).
    for locale in expected_locales:
        bloque = i18n[locale]
        assert bloque["site"].get("description") == _SENTINEL
        # Cada Place con description no vacia queda traducido bajo su id.
        for place in original["places"]:
            assert bloque["places"][place["id"]]["description"] == _SENTINEL
        # Cada Event con description no vacia queda traducido bajo su id.
        for event in original["events"]:
            assert bloque["events"][event["id"]]["description"] == _SENTINEL


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
