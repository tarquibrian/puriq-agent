"""Pruebas de ejemplo de validez de esquema con/sin extensiones (Tarea 1.3).

Ejercitan el punto de entrada real de validacion del Contrato (`puriq.schemas.validate`,
que delega en jsonschema) sobre las extensiones aditivas y opcionales de las Tareas
1.1 y 1.2:

  - `theme-tokens.schema.json`: tokens de diseno ampliados opcionales (`spacing`,
    `typeScale`, `shadows`, `radii`, `breakpoints`, `motion`, `container`).
  - `site-config.schema.json`: propiedad opcional `landing` (secciones de portada).

Cada caso valido debe pasar `validate` sin excepcion; cada caso invalido debe ser
rechazado con un `jsonschema.ValidationError` cuyo camino (`absolute_path`) o mensaje
identifica el token/campo infractor. No hay red ni build: solo validacion en memoria.

Requisitos cubiertos: 1.1, 1.2, 1.3, 1.6, 13.1, 13.4, 16.1, 16.2.
"""
from __future__ import annotations

import copy

import jsonschema
import pytest

from puriq import schemas


# --- Documentos base minimos y validos (sin extensiones) -------------------

def _base_theme() -> dict:
    """Theme_Tokens minimo valido: solo lo requerido por el esquema (legacy)."""
    return {
        "colors": {"primary": "#123456", "background": "#ffffff", "text": "#000000"},
        "typography": {"headingFont": "Inter", "bodyFont": "Inter"},
    }


def _base_site_config() -> dict:
    """Site_Config minimo valido: solo `layout` y `modules` (legacy, sin `landing`)."""
    return {
        "layout": "clasico",
        "modules": {"places": {"enabled": True, "order": 1}},
    }


# Conjunto COMPLETO de tokens ampliados (los valores por defecto del Design_System).
_ALL_EXPANDED_TOKENS = {
    "spacing": {"xs": "0.25rem", "sm": "0.5rem", "md": "1rem", "lg": "2rem", "xl": "4rem", "2xl": "8rem"},
    "typeScale": {
        "h1": {"size": "2.5rem", "lineHeight": "1.15"},
        "h2": {"size": "2rem", "lineHeight": "1.25"},
        "h3": {"size": "1.5rem", "lineHeight": "1.3"},
        "body": {"size": "1rem", "lineHeight": "1.6"},
        "small": {"size": "0.875rem", "lineHeight": "1.5"},
    },
    "shadows": {"sm": "0 1px 2px rgba(0,0,0,.08)", "md": "0 4px 12px rgba(0,0,0,.12)", "lg": "0 12px 32px rgba(0,0,0,.18)"},
    "radii": {"sm": "4px", "md": "8px", "lg": "16px", "pill": "999px"},
    "breakpoints": {"sm": "640px", "md": "768px", "lg": "1024px"},
    "motion": {"durationFast": "120ms", "durationBase": "240ms", "easing": "cubic-bezier(.4,0,.2,1)"},
    "container": {"sm": "640px", "md": "768px", "lg": "1080px", "xl": "1280px"},
}


def _error_path(exc: jsonschema.ValidationError) -> list:
    """Camino (lista de claves/indices) del nodo infractor dentro del documento."""
    return list(exc.absolute_path)


# ===========================================================================
# Theme_Tokens: tokens de diseno ampliados (Req 1.1, 1.2, 1.3, 1.6, 16.2)
# ===========================================================================

def test_theme_with_all_expanded_tokens_validates():
    """Un Theme_Tokens con TODOS los tokens ampliados es valido (Req 1.1-1.3)."""
    theme = _base_theme()
    theme.update(copy.deepcopy(_ALL_EXPANDED_TOKENS))
    schemas.validate(theme, "theme-tokens")  # no debe lanzar


def test_theme_with_some_expanded_tokens_validates():
    """Un Theme_Tokens con SOLO ALGUNOS tokens ampliados es valido (opcionales)."""
    theme = _base_theme()
    theme["spacing"] = {"md": "1rem", "lg": "2rem"}
    theme["motion"] = {"durationBase": "240ms"}
    schemas.validate(theme, "theme-tokens")  # no debe lanzar


def test_theme_without_expanded_tokens_validates_legacy():
    """Un Theme_Tokens legacy (sin ningun token ampliado) sigue validando (Req 16.2)."""
    schemas.validate(_base_theme(), "theme-tokens")  # no debe lanzar


def test_theme_rejects_non_string_spacing_value_naming_token():
    """`spacing.md` numerico se rechaza; el error nombra el token infractor (Req 1.6)."""
    theme = _base_theme()
    theme["spacing"] = {"md": 16}  # deberia ser string
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        schemas.validate(theme, "theme-tokens")
    assert _error_path(excinfo.value) == ["spacing", "md"]


def test_theme_rejects_typescale_entry_missing_size_naming_field():
    """`typeScale.h1` sin `size` se rechaza; el error identifica el campo (Req 1.6)."""
    theme = _base_theme()
    theme["typeScale"] = {"h1": {"lineHeight": "1.15"}}  # falta 'size' (requerido)
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        schemas.validate(theme, "theme-tokens")
    err = excinfo.value
    # El nodo infractor es la entrada typeScale.h1 y el mensaje nombra 'size'.
    assert _error_path(err) == ["typeScale", "h1"]
    assert "size" in err.message


def test_theme_rejects_unknown_motion_key_naming_token():
    """Una clave de `motion` fuera del catalogo se rechaza nombrando el token (Req 1.6)."""
    theme = _base_theme()
    theme["motion"] = {"durationTurbo": "10ms"}  # clave no permitida
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        schemas.validate(theme, "theme-tokens")
    err = excinfo.value
    assert _error_path(err) == ["motion"]
    # El mensaje de additionalProperties nombra la propiedad espuria.
    assert "durationTurbo" in err.message


# ===========================================================================
# Site_Config: propiedad opcional `landing` (Req 13.1, 13.4, 16.1)
# ===========================================================================

def test_site_config_with_valid_landing_validates():
    """Un Site_Config con una seccion `landing` valida es aceptado (Req 13.1)."""
    config = _base_site_config()
    config["landing"] = [
        {
            "type": "hero",
            "enabled": True,
            "order": 1,
            "content": {"headline": "Potosi", "subheadline": "Plata y sal"},
        },
        {"type": "features", "enabled": False, "order": 2, "content": {"items": []}},
    ]
    schemas.validate(config, "site-config")  # no debe lanzar


def test_site_config_without_landing_validates_legacy():
    """Un Site_Config sin `landing` sigue validando: retrocompatibilidad (Req 16.1)."""
    schemas.validate(_base_site_config(), "site-config")  # no debe lanzar


def test_site_config_rejects_landing_type_out_of_catalog_naming_field():
    """Una seccion con `type` fuera del catalogo se rechaza nombrando el campo (Req 13.4)."""
    config = _base_site_config()
    config["landing"] = [{"type": "carousel", "enabled": True, "order": 1}]
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        schemas.validate(config, "site-config")
    assert _error_path(excinfo.value) == ["landing", 0, "type"]


def test_site_config_rejects_landing_order_below_one_naming_field():
    """Una seccion con `order < 1` se rechaza nombrando el campo (Req 13.4)."""
    config = _base_site_config()
    config["landing"] = [{"type": "hero", "enabled": True, "order": 0}]
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        schemas.validate(config, "site-config")
    assert _error_path(excinfo.value) == ["landing", 0, "order"]


def test_site_config_rejects_landing_non_boolean_enabled_naming_field():
    """Una seccion con `enabled` no booleano se rechaza nombrando el campo (Req 13.4)."""
    config = _base_site_config()
    config["landing"] = [{"type": "hero", "enabled": "yes", "order": 1}]
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        schemas.validate(config, "site-config")
    assert _error_path(excinfo.value) == ["landing", 0, "enabled"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
