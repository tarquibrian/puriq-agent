"""Prueba de propiedad para la resolucion de modulos habilitados y ordenados (Property 17).

# Feature: agent-tools, Property 17: Resolución de módulos = subconjunto habilitado y ordenado
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.tools import build_site


# --- estrategias de Site_Config -------------------------------------------
# Claves de modulo admitidas por el esquema site-config (modules.properties).
_MODULE_KEYS = ["map", "places", "events", "blog", "chatweb"]


@st.composite
def _module_spec(draw):
    """Genera la spec de un modulo: `enabled` (bool) y `order` (entero >= 1).

    Incluye opcionalmente `label` para reflejar que _resolve_modules debe
    conservar las propiedades del modulo junto a su `key`. El rango de `order`
    admite colisiones a proposito, para ejercitar el orden estable ante empates.
    """
    spec = {
        "enabled": draw(st.booleans()),
        "order": draw(st.integers(min_value=1, max_value=10)),
    }
    if draw(st.booleans()):
        spec["label"] = draw(st.text(min_size=1, max_size=12))
    return spec


@st.composite
def _site_config(draw):
    """Genera un Site_Config valido con un subconjunto de modulos.

    Elige un subconjunto de las claves de modulo admitidas y le asigna a cada una
    una spec generada. El resto de campos (layout) se fija para producir un
    documento conforme a la estructura de Site_Config.
    """
    keys = draw(st.lists(st.sampled_from(_MODULE_KEYS), unique=True, max_size=len(_MODULE_KEYS)))
    modules = {key: draw(_module_spec()) for key in keys}
    return {
        "layout": draw(st.sampled_from(["clasico", "moderno"])),
        "modules": modules,
    }


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 17: Resolución de módulos = subconjunto habilitado y ordenado
@settings(max_examples=200, deadline=None)
@given(config=_site_config())
def test_module_resolution_is_enabled_subset_ordered(config):
    """La resolucion de modulos es exactamente el subconjunto habilitado y ordenado.

    Para toda Site_Config, `build_site._resolve_modules` devuelve exactamente los
    modulos con `enabled=true` (Req 5.3), excluye los `enabled=false` (Req 5.4) y
    los dispone en orden ascendente de `order` (Req 5.5).

    Validates: Requirements 5.3, 5.4, 5.5
    """
    modules = config["modules"]
    resolved = build_site._resolve_modules(config)

    resolved_keys = [m["key"] for m in resolved]

    # Conjunto esperado: exactamente las claves con enabled=true.
    enabled_keys = {k for k, spec in modules.items() if spec.get("enabled")}
    disabled_keys = {k for k, spec in modules.items() if not spec.get("enabled")}

    # Req 5.3 + 5.4: el conjunto activado es exactamente el habilitado; ninguna
    # clave se repite y ningun modulo deshabilitado aparece.
    assert set(resolved_keys) == enabled_keys
    assert len(resolved_keys) == len(enabled_keys)  # sin duplicados
    assert disabled_keys.isdisjoint(resolved_keys)

    # Req 5.5: los modulos activados quedan dispuestos en orden ascendente de `order`.
    orders = [m["order"] for m in resolved]
    assert orders == sorted(orders)

    # Cada modulo resuelto conserva las propiedades de su spec original (order/label).
    for m in resolved:
        original = modules[m["key"]]
        assert m["order"] == original["order"]
        assert m["enabled"] is True
        if "label" in original:
            assert m["label"] == original["label"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
