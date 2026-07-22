"""Prueba de propiedad: los tokens de marca se materializan como variables CSS (Property 18).

# Feature: agent-tools, Property 18: Los tokens de marca se materializan como variables CSS
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.tools import build_site


# --- estrategias de Theme_Tokens ------------------------------------------
# Colores admitidos por el esquema theme-tokens (colors.properties). El esquema
# exige primary/background/text y admite secondary/accent como opcionales.
_REQUIRED_COLORS = ("primary", "background", "text")
_OPTIONAL_COLORS = ("secondary", "accent")

# Mapeo de cada token de marca -> nombre de la variable CSS que emite
# `_theme_to_css`. Es la relacion que la propiedad exige verificar.
_COLOR_VAR = {
    "primary": "--color-primary",
    "secondary": "--color-secondary",
    "background": "--color-background",
    "text": "--color-text",
    "accent": "--color-accent",
}
_FONT_VAR = {
    "headingFont": "--font-heading",
    "bodyFont": "--font-body",
    "baseSize": "--font-base-size",
}


@st.composite
def _hex_color(draw):
    """Genera un color hexadecimal valido (#RGB o #RRGGBB) conforme al esquema."""
    digits = "0123456789abcdefABCDEF"
    length = draw(st.sampled_from([3, 6]))
    cuerpo = "".join(draw(st.sampled_from(list(digits))) for _ in range(length))
    return "#" + cuerpo


# Fuentes: strings no vacios (el esquema exige headingFont/bodyFont). Se evitan
# caracteres de control para reflejar valores de fuente realistas y mantener la
# comprobacion de substring exacta sobre una sola linea CSS.
_font_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=1,
    max_size=24,
).filter(lambda s: s.strip() != "")


@st.composite
def _theme_tokens(draw):
    """Genera un Theme_Tokens valido con un subconjunto variable de tokens de marca.

    Siempre incluye los colores/fuentes obligatorios del esquema y agrega de
    forma opcional los colores `secondary`/`accent`, `typography.baseSize` y
    `radius`, para ejercitar tanto los tokens presentes como los ausentes.
    """
    colors = {clave: draw(_hex_color()) for clave in _REQUIRED_COLORS}
    for clave in _OPTIONAL_COLORS:
        if draw(st.booleans()):
            colors[clave] = draw(_hex_color())

    typography = {
        "headingFont": draw(_font_text),
        "bodyFont": draw(_font_text),
    }
    if draw(st.booleans()):
        typography["baseSize"] = draw(_font_text)

    theme = {"colors": colors, "typography": typography}
    if draw(st.booleans()):
        theme["radius"] = draw(_font_text)
    return theme


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 18: Los tokens de marca se materializan como variables CSS
@settings(max_examples=200, deadline=None)
@given(theme=_theme_tokens())
def test_brand_tokens_materialize_as_css_variables(theme):
    """Cada color de `colors` y cada fuente de `typography` aparece como variable CSS.

    Para todo Theme_Tokens, `build_site._theme_to_css` produce un bloque `:root`
    en el que cada color definido en `colors` se emite como `--color-*: <valor>;`
    y cada fuente de `typography` se emite como su variable `--font-*: <valor>;`
    (Req 5.6). Tokens ausentes no fuerzan variables vacias.

    Validates: Requirements 5.6
    """
    css = build_site._theme_to_css(theme)

    # Estructura minima: un bloque :root que envuelve las variables.
    assert ":root {" in css
    assert "}" in css

    # Cada color definido aparece como su variable CSS con el valor exacto.
    for clave, valor in theme["colors"].items():
        var = _COLOR_VAR[clave]
        assert f"{var}: {valor};" in css, (
            f"Falta la variable CSS del color '{clave}': se esperaba "
            f"'{var}: {valor};' en la salida."
        )

    # Cada fuente/tamano definido aparece como su variable CSS con el valor exacto.
    for clave, valor in theme["typography"].items():
        var = _FONT_VAR[clave]
        assert f"{var}: {valor};" in css, (
            f"Falta la variable CSS de tipografia '{clave}': se esperaba "
            f"'{var}: {valor};' en la salida."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
