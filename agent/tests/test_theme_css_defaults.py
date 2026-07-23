"""Pruebas de ejemplo/integración: `_theme_to_css` materializa el conjunto
completo de variables del Design_System con defaults ante un theme parcial.

Cubren la tarea 8.4 (Req 16.2, 16.5): un `theme.tokens.json` que solo define los
colores/tipografía obligatorios (sin ningún token ampliado) igualmente produce
un `theme.css` con TODAS las variables del Design_System (`--space-*`,
`--fs-*`/`--lh-*`, `--shadow-*`, `--radius-*`, `--bp-*`, `--motion-*`,
`--container-*`) usando los valores por defecto de `DESIGN_DEFAULTS`, y la
materialización de marca (`_materialize_brand`, que escribe `src/data/theme.css`)
no falla. Son pruebas de EJEMPLO rápidas y offline: llaman a `_theme_to_css`
directamente y a `_materialize_brand` sobre un directorio temporal, sin ejecutar
Astro ni npm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import build_site  # noqa: E402


# Theme mínimo VÁLIDO: solo colores/tipografía obligatorios, SIN tokens
# ampliados (spacing, typeScale, shadows, radii, breakpoints, motion, container).
_PARTIAL_THEME = {
    "colors": {"primary": "#1a73e8", "background": "#ffffff", "text": "#111111"},
    "typography": {"headingFont": "Inter", "bodyFont": "Inter"},
}

# Variables representativas de cada grupo de tokens ampliados y su valor por
# defecto esperado (tomado de build_site.DESIGN_DEFAULTS). Si un default cambia,
# esta tabla debe reflejarlo.
_EXPECTED_DEFAULT_VARS = {
    "--space-md": "1rem",
    "--fs-h1": "2.5rem",
    "--lh-h1": "1.15",
    "--shadow-md": "0 4px 12px rgba(0,0,0,.12)",
    "--radius-md": "8px",
    "--bp-md": "768px",
    "--motion-duration-base": "240ms",
    "--motion-easing": "cubic-bezier(.4,0,.2,1)",
    "--container-lg": "1080px",
}


def test_partial_theme_emits_complete_default_variable_set():
    """Un theme sin tokens ampliados emite el conjunto COMPLETO de variables con
    defaults, además de los `--color-*`/`--font-*` definidos (Req 16.2)."""
    css = build_site._theme_to_css(_PARTIAL_THEME)

    # Estructura mínima.
    assert ":root {" in css and "}" in css

    # Una variable representativa de cada grupo ampliado, con su valor default.
    for var, valor in _EXPECTED_DEFAULT_VARS.items():
        assert f"{var}: {valor};" in css, (
            f"Falta la variable ampliada '{var}' con su default '{valor}'."
        )

    # Los tokens de marca definidos (sin default) siguen emitiéndose.
    assert "--color-primary: #1a73e8;" in css
    assert "--color-background: #ffffff;" in css
    assert "--color-text: #111111;" in css
    assert "--font-heading: Inter;" in css
    assert "--font-body: Inter;" in css


def test_overridden_tokens_win_and_absent_fall_back_to_defaults():
    """Los tokens que el usuario define ganan; los ausentes caen al default
    (merge parcial y coherente, Req 16.5)."""
    theme = {
        **_PARTIAL_THEME,
        # Solo se sobreescriben ALGUNOS tokens dentro de cada grupo.
        "spacing": {"md": "1.25rem"},
        "radii": {"md": "10px"},
        "motion": {"durationBase": "300ms"},
        # typeScale.h1 define solo `size`; `lineHeight` debe caer al default.
        "typeScale": {"h1": {"size": "3rem"}},
    }

    css = build_site._theme_to_css(theme)

    # Valores del usuario ganan.
    assert "--space-md: 1.25rem;" in css
    assert "--radius-md: 10px;" in css
    assert "--motion-duration-base: 300ms;" in css
    assert "--fs-h1: 3rem;" in css

    # Tokens ausentes dentro de los mismos grupos caen al default.
    assert "--space-lg: 2rem;" in css
    assert "--radius-lg: 16px;" in css
    assert "--motion-easing: cubic-bezier(.4,0,.2,1);" in css
    # lineHeight de h1 no fue definido por el usuario: default preservado.
    assert "--lh-h1: 1.15;" in css


def test_theme_to_css_is_idempotent_under_repeated_merge():
    """Materializar dos veces el mismo theme produce el mismo CSS (merge idempotente)."""
    theme = {**_PARTIAL_THEME, "spacing": {"md": "1.25rem"}}
    assert build_site._theme_to_css(theme) == build_site._theme_to_css(theme)


def test_materialize_brand_writes_theme_css_for_partial_theme(tmp_path):
    """La materialización de marca no falla ante un theme parcial y escribe
    `src/data/theme.css` con el conjunto completo de variables (Req 16.2, 16.5)."""
    work = tmp_path / "work"
    config = {
        "layout": "clasico",
        "modules": {"places": {"enabled": True, "order": 1}},
    }

    # No debe lanzar: escribe theme.css derivando defaults para lo ausente.
    build_site._materialize_brand(work, config, _PARTIAL_THEME)

    theme_css = work / build_site.DATA_SUBDIR / build_site.THEME_CSS_FILENAME
    assert theme_css.exists()

    contenido = theme_css.read_text()
    for var, valor in _EXPECTED_DEFAULT_VARS.items():
        assert f"{var}: {valor};" in contenido


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
