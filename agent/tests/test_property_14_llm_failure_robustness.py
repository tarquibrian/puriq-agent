"""Prueba de propiedad para la robustez ante fallo del LLM por ítem (Property 14).

# Feature: agent-tools, Property 14: Robustez ante fallo del LLM por ítem
"""
from __future__ import annotations

import copy
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.tools import generate_content


# --- proveedor de LLM falso con fallo selectivo por ítem -------------------
#
# El proveedor cumple el protocolo `LLMProvider`, pero su `complete()` FALLA
# (levanta una excepción) cuando el prompt menciona alguno de los tokens de
# fallo, y devuelve un texto centinela no vacío en caso contrario. Como cada
# prompt por ítem incluye el `name` del ítem (que embebe su token único), el
# proveedor decide de forma determinista qué ítems fallan. No realiza E/S ni
# toca la red/LLM real (DD-3).
_SENTINEL = "Descripcion generada por el LLM."


class _FakeSelectiveProvider:
    def __init__(self, fail_tokens: set[str]):
        self._fail_tokens = fail_tokens

    def complete(self, prompt: str) -> str:
        for token in self._fail_tokens:
            if token in prompt:
                raise RuntimeError(f"fallo simulado del LLM para {token}")
        return _SENTINEL


# --- estrategias -----------------------------------------------------------
#
# Cada ítem recibe un token único e inequívoco (índice acotado a 0..5 para
# evitar colisiones de subcadena, p. ej. "place-1" dentro de "place-10"). Los
# ítems se generan SIN `description` (en blanco), de modo que `enrich` intente
# generarla y el fallo/éxito del proveedor sea observable por ítem.
def _build_scenario(place_fails: list[bool], event_fails: list[bool]):
    """Construye un Tourism_Data y el conjunto de tokens que deben fallar."""
    fail_tokens: set[str] = set()

    places = []
    for i, fails in enumerate(place_fails):
        token = f"place-{i}"
        places.append({"id": token, "name": f"Lugar {token}"})
        if fails:
            fail_tokens.add(token)

    events = []
    for i, fails in enumerate(event_fails):
        token = f"event-{i}"
        events.append({"id": token, "name": f"Evento {token}"})
        if fails:
            fail_tokens.add(token)

    data = {
        "site": {
            "name": "Sitio de prueba",
            "region": "Region de prueba",
            # `description` no vacía para no invocar la rama SEO.
            "description": "Descripcion de sitio ya definida.",
            "defaultLocale": "es",
            "locales": ["es"],  # un solo Locale: sin traducciones extra.
        },
        "places": places,
        "events": events,
    }
    return data, fail_tokens


_fail_flags = st.lists(st.booleans(), max_size=6)


def _is_blank(value: object) -> bool:
    return not (isinstance(value, str) and value.strip())


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 14: Robustez ante fallo del LLM por ítem
@settings(max_examples=100, deadline=None)
@given(place_fails=_fail_flags, event_fails=_fail_flags)
def test_llm_failure_preserves_item_and_processes_rest(place_fails, event_fails):
    """Un fallo del LLM por ítem conserva ese ítem y procesa los demás (DD-3).

    Para todo conjunto de ítems, si la invocación al LLM_Provider falla para
    algunos, `generate_content.enrich` conserva el valor previo de esos ítems
    (su `description` en blanco se mantiene sin completar) y procesa
    correctamente el resto (su `description` queda completada). Un fallo por
    ítem nunca aborta el enriquecimiento del resto.

    Validates: Requirements 3.10
    """
    data, fail_tokens = _build_scenario(place_fails, event_fails)
    original = copy.deepcopy(data)

    provider = _FakeSelectiveProvider(fail_tokens)
    with mock.patch.object(
        generate_content, "get_provider", return_value=provider
    ):
        result = generate_content.enrich(data)

    # La estructura se preserva: mismo número de ítems, en el mismo orden.
    assert len(result["places"]) == len(original["places"])
    assert len(result["events"]) == len(original["events"])

    for kind in ("places", "events"):
        for item in result[kind]:
            token = item["id"]
            if token in fail_tokens:
                # Fallo del LLM: se conserva el valor previo (seguía en blanco).
                assert _is_blank(item.get("description")), (
                    f"{token} debía conservar su description en blanco tras el fallo"
                )
            else:
                # Éxito: el resto se procesa y la description queda completada.
                assert item.get("description") == _SENTINEL, (
                    f"{token} debía tener su description completada por el LLM"
                )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
