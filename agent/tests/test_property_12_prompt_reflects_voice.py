"""Prueba de propiedad: el prompt refleja la voz de marca (Property 12).

# Feature: agent-tools, Property 12: El prompt refleja la voz de marca
"""
from __future__ import annotations

from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.tools import generate_content


# --- proveedor de LLM falso que CAPTURA los prompts ------------------------
#
# En lugar de invocar un LLM real, este proveedor registra cada prompt que
# recibe `complete()` y devuelve un texto no vacio para que `enrich` complete
# las descripciones. Asi capturamos exactamente los prompts que `generate_content`
# construye e inspeccionamos que reflejen la voz de marca. No realiza E/S.
class _RecordingProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "texto generado por el proveedor de prueba"


# --- estrategias -----------------------------------------------------------
_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"
_slug = st.text(alphabet=_SLUG_ALPHABET, min_size=1, max_size=10)
_name = st.text(min_size=1, max_size=15)

# Tono: cualquier texto cuyo `.strip()` sea no vacio, para que NO se caiga al
# tono por defecto (`_voice_directives` usa el default solo si el tono esta en
# blanco). Se compara siempre contra la version `.strip()`, que es la que el
# constructor de prompts inserta.
_tone = st.text(min_size=1, max_size=30).filter(lambda s: bool(s.strip()))

# Formalidad "definida" = valor verdadero (string no vacio). `_voice_directives`
# la incluye solo cuando es truthy (`if formality:`). El caso "no definida" se
# modela con None (o clave ausente) y no debe aparecer en el prompt.
_formality = st.text(min_size=1, max_size=20).filter(lambda s: bool(s.strip()))


@st.composite
def _voice(draw):
    """Genera un subdocumento `voice` con `tone` y, a veces, `formality`."""
    voice: dict = {"tone": draw(_tone)}
    choice = draw(st.sampled_from(["with_formality", "none", "missing"]))
    if choice == "with_formality":
        voice["formality"] = draw(_formality)
    elif choice == "none":
        voice["formality"] = None
    # "missing": sin clave `formality`
    return voice


@st.composite
def _tourism_data(draw):
    """Genera un Tourism_Data que fuerza la construccion de al menos un prompt.

    Se garantiza al menos un Place con `description` en blanco (para que `enrich`
    construya un prompt de descripcion). Un unico Locale evita traducciones y
    concentra la propiedad en la voz de marca.
    """
    places = draw(st.lists(
        st.builds(
            lambda i, n: {"id": i, "name": n, "description": ""},
            _slug, _name,
        ),
        min_size=1, max_size=4,
    ))
    return {
        "site": {
            "name": draw(st.text(min_size=1, max_size=20)),
            "region": draw(st.text(min_size=1, max_size=20)),
            "description": "",  # vacia -> tambien se genera el prompt SEO
            "defaultLocale": "es",
            "locales": ["es"],
        },
        "places": places,
        "events": [],
    }


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 12: El prompt refleja la voz de marca
@settings(max_examples=200, deadline=None)
@given(voice=_voice(), data=_tourism_data())
def test_prompt_reflects_brand_voice(voice, data):
    """Todo prompt construido por `generate_content` refleja la voz de marca.

    Para todo valor de `Theme_Tokens.voice.tone`, cada prompt que arma
    `generate_content` contiene ese tono (Req 3.4); y cuando `voice.formality`
    esta definida, el prompt tambien la incluye (Req 3.5). Cuando la formalidad
    no esta definida, no aparece un rotulo de formalidad en el prompt.

    Validates: Requirements 3.4, 3.5
    """
    provider = _RecordingProvider()

    with mock.patch.object(
        generate_content, "get_provider", return_value=provider
    ):
        generate_content.enrich(data, voice)

    # Debe haberse construido al menos un prompt (hay contenido faltante).
    assert provider.prompts, "se esperaba al menos un prompt construido"

    tone = voice["tone"].strip()
    formality = voice.get("formality")

    for prompt in provider.prompts:
        # Req 3.4: el tono siempre aparece en el prompt.
        assert tone in prompt
        if formality:
            # Req 3.5: la formalidad definida se refleja en el prompt.
            assert formality in prompt
            assert "Nivel de formalidad:" in prompt
        else:
            # Formalidad no definida -> no se agrega el rotulo de formalidad.
            assert "Nivel de formalidad:" not in prompt


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
