"""Pruebas del Intake_Prompt del chat web (spec conversational-web-chat).

Cubre las tareas OPCIONALES 4.2 y 4.3 del plan, sobre
`agent/puriq/intake/prompt.py` (`build_system_prompt`, `INTAKE_PALETTES`):

- **Property 7 (task 4.2)** — prueba de propiedad con Hypothesis (>=100 iteraciones):
  para todo Contract_State (con listas `missing` variadas), el system prompt
  construido por `build_system_prompt` contiene todas las claves de
  `MODULE_CATALOG`, todos los nombres del catálogo de paletas (`INTAKE_PALETTES`)
  y refleja los `missing` del Contract_State inyectado.
  **Validates: Requirements 2.2, 2.4**

- **Ejemplos (task 4.3)** — el prompt contiene las fases del `INTAKE_GUION`
  (2.1), instruye a pedir archivos (2.3) e instruye a invocar las tools (2.5).
  _Requirements: 2.1, 2.3, 2.5_

Estas pruebas son puras: `build_system_prompt` no hace E/S ni llama al LLM, por
lo que no requieren proyecto temporal ni mocks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio (mismo patrón que las pruebas existentes).
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.intake.prompt import INTAKE_PALETTES, build_system_prompt  # noqa: E402
from puriq.intake.tools import INTAKE_GUION  # noqa: E402
from puriq.wizard.modules import MODULE_CATALOG  # noqa: E402

#: Configuración común de PBT: >=100 iteraciones. No hay E/S ni fixtures de
#: función, así que no se necesita suprimir health-checks ni desactivar deadline.
pbt = settings(max_examples=100)


# --- Estrategias para generar Contract_State con `missing` variados ----------
#: Piezas realistas que `get_state` puede reportar como faltantes (ver
#: `puriq.intake.tools.get_state`), más algunas extra para variar el espacio.
_PIECES = ["site", "modules", "places", "brand", "events", "landing"]
_FIELDS = ["name", "region", "center", "colors"]


@st.composite
def _missing_item(draw) -> dict:
    """Genera un elemento de `missing`: `{"piece": ...}` con `field` opcional.

    Refleja la forma que produce `get_state`: cada faltante es un dict con
    `piece` y, opcionalmente, `field`.
    """
    item: dict = {"piece": draw(st.sampled_from(_PIECES))}
    field = draw(st.one_of(st.none(), st.sampled_from(_FIELDS)))
    if field is not None:
        item["field"] = field
    return item


@st.composite
def _contract_state(draw) -> dict:
    """Genera un Contract_State variado con una lista `missing` arbitraria.

    Incluye los tres documentos del contrato con contenido variado (para
    ejercitar el resumen del prompt) y una lista `missing` de 0 a 8 elementos.
    """
    missing = draw(st.lists(_missing_item(), min_size=0, max_size=8))

    n_places = draw(st.integers(min_value=0, max_value=3))
    n_events = draw(st.integers(min_value=0, max_value=3))
    site_name = draw(st.one_of(st.none(), st.text(max_size=20)))

    tourism: dict = {
        "site": {"name": site_name} if site_name is not None else {},
        "places": [{"id": f"p{i}"} for i in range(n_places)],
        "events": [{"id": f"e{i}"} for i in range(n_events)],
    }
    site_config = {"modules": {}}
    theme: dict = {"colors": {}}

    return {
        "tourism-data": tourism,
        "site-config": site_config,
        "theme-tokens": theme,
        "missing": missing,
    }


def _expected_missing_line(item: dict) -> str:
    """Reconstruye la línea que `prompt._format_missing` produce para un faltante.

    Debe coincidir carácter a carácter con el renderizado del módulo bajo prueba
    para poder verificarla como substring del prompt.
    """
    piece = item.get("piece", "?")
    field = item.get("field")
    if field:
        return f"- Falta `{piece}` (campo: {field})"
    return f"- Falta `{piece}`"


# =============================================================================
# Property 7 (task 4.2)
# =============================================================================
# Feature: conversational-web-chat, Property 7: El Intake_Prompt refleja los catálogos y los faltantes vigentes
@pbt
@given(contract_state=_contract_state())
def test_p7_prompt_reflects_catalogs_and_missing(contract_state):
    """El system prompt contiene los catálogos (módulos, paletas) y los `missing`.

    Para todo Contract_State, el prompt construido contiene todas las claves de
    `MODULE_CATALOG`, todos los nombres de `INTAKE_PALETTES` y refleja cada
    faltante del `missing` inyectado.

    Validates: Requirements 2.2, 2.4
    """
    prompt = build_system_prompt(contract_state)

    # Catálogo de módulos: todas las claves de MODULE_CATALOG están presentes.
    for key in MODULE_CATALOG:
        assert key in prompt, f"falta la clave de módulo {key!r} en el prompt"

    # Catálogo de paletas: todos los nombres de INTAKE_PALETTES están presentes.
    for palette in INTAKE_PALETTES:
        assert palette["name"] in prompt, (
            f"falta el nombre de paleta {palette['name']!r} en el prompt"
        )

    # Reflejo de los faltantes vigentes del Contract_State inyectado.
    missing = contract_state["missing"]
    if missing:
        for item in missing:
            linea = _expected_missing_line(item)
            assert linea in prompt, f"el prompt no refleja el faltante: {linea!r}"
    else:
        # Sin faltantes, el prompt lo indica explícitamente (no hay líneas "Falta").
        assert "No hay piezas esenciales pendientes" in prompt


# =============================================================================
# Ejemplos (task 4.3) — Requirements 2.1, 2.3, 2.5
# =============================================================================
def test_prompt_contains_intake_guion_phases():
    """El prompt embebe el `INTAKE_GUION` con sus fases 1–9 (Req 2.1)."""
    prompt = build_system_prompt({})

    # El guion oficial se embebe íntegro para no divergir del núcleo.
    assert INTAKE_GUION in prompt
    # Y se reconocen las fases de inicio y fin del guion.
    assert "Fase 1 — Sitio" in prompt
    assert "Fase 9 — Generar" in prompt


def test_prompt_instructs_to_request_files():
    """El prompt instruye a pedir archivos de forma proactiva: fotos y logo (Req 2.3)."""
    prompt = build_system_prompt({})
    lowered = prompt.lower()

    # Sección dedicada a pedir archivos activamente.
    assert "archivos" in lowered
    assert "proactiv" in lowered or "activamente" in lowered
    # Menciona explícitamente fotos y el logo.
    assert "foto" in lowered
    assert "logo" in lowered


def test_prompt_instructs_to_invoke_tools():
    """El prompt instruye a INVOCAR las intake tools, no solo a describir cambios (Req 2.5)."""
    prompt = build_system_prompt({})
    lowered = prompt.lower()

    # Regla de oro: invocar/llamar las herramientas de intake para registrar.
    assert "invoc" in lowered  # "INVOCÁ", "invocá", "invocar"
    assert "herramienta" in lowered or "intake tool" in lowered
    # Refuerza que no se limite a describir el cambio sin ejecutarlo.
    assert "no te limites" in lowered or "sin ejecutar" in lowered


# =============================================================================
# Ingesta multimodal: qué se escribe y cuándo (corrección post-validación E2E)
# =============================================================================
def test_prompt_does_not_instruct_writing_alt_field():
    """El prompt/guion NO instruye escribir un campo `alt` en el ítem.

    `place`/`event` de `schemas/tourism-data.schema.json` no tienen `alt` y usan
    `additionalProperties: false`: instruirlo hacía fallar `edit_item` y, por
    atomicidad, perdía también la `description` del mismo llamado. En su lugar el
    prompt propone `description`/`shortDescription`.
    """
    prompt = build_system_prompt({})
    lowered = prompt.lower()

    # No se propone `alt` como campo escribible del ítem.
    assert "(`alt`)" not in lowered
    assert "el `alt`" not in lowered
    assert '"alt"' not in lowered
    # Y sí se nombran los campos que el esquema acepta.
    assert "description" in prompt
    assert "shortdescription" in lowered
    # Se aclara explícitamente que no se intente escribir `alt`.
    assert "no intentes escribir un campo `alt`" in lowered


def test_prompt_instructs_saving_asset_in_same_turn():
    """El prompt instruye guardar el archivo con `attach_asset` en el MISMO turno.

    Los bytes de los binarios solo existen en el turno en que llegan (DD-M4), así
    que diferir `attach_asset` al turno de confirmación obliga al usuario a
    reenviar la imagen.
    """
    prompt = build_system_prompt({})
    lowered = prompt.lower()

    assert "attach_asset" in lowered
    assert "mismo turno" in lowered
    assert "sin pedir confirmación" in lowered
    # Distingue guardar el archivo de escribir el contenido derivado.
    assert "contenido derivado" in lowered


def test_prompt_forbids_writing_qa_in_pdf_turn():
    """El prompt prohíbe invocar `add_qa` en el turno en que llega el texto del PDF.

    Sin esa prohibición el modelo escribía la Q&A al recibir el PDF y la repetía
    al confirmar, dejándola duplicada en `content/qa.json`.
    """
    prompt = build_system_prompt({})
    lowered = prompt.lower()

    assert "no invoques `add_qa`" in lowered
    assert "en el mismo turno en que llega el texto de un pdf" in lowered


def test_guion_matches_prompt_multimodal_rules():
    """El `INTAKE_GUION` (superficie MCP) lleva las mismas reglas que el prompt web."""
    lowered = INTAKE_GUION.lower()

    # Nada de escribir `alt` en el ítem.
    assert "no intentes escribir un campo `alt`" in lowered
    assert "(`alt`)" not in lowered
    # Guardar el archivo en el mismo turno, sin confirmación.
    assert "mismo turno" in lowered
    assert "sin pedir confirmación" in lowered
    # Prohibición explícita en el camino PDF.
    assert "no invoques `add_qa`" in lowered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
