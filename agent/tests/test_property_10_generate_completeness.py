"""Prueba de propiedad para la completitud de descripciones (Property 10).

# Feature: agent-tools, Property 10: Completitud de descripciones tras la generación
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
# Un proveedor "exitoso" cumple el protocolo `LLMProvider`: su `complete()`
# devuelve SIEMPRE texto no vacio, de modo que `enrich` pueda completar toda
# descripcion faltante sin tocar Amazon Bedrock ni Ollama.
class _FakeSuccessProvider:
    def complete(self, prompt: str) -> str:
        # Texto no vacio y deterministicamente no-blanco (Req 3.1, 3.2).
        return "Descripcion generada por el LLM."


# --- estrategias -----------------------------------------------------------
_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789-"
_slug = st.text(alphabet=_SLUG_ALPHABET, min_size=1, max_size=10)
_name = st.text(min_size=1, max_size=15)

# Descripciones: mezcla de valores "en blanco" (que enrich debe completar) y
# valores no vacios (que enrich debe conservar). Se cubren varias formas de
# "vacio": ausente, cadena vacia, solo espacios y None.
_blank_description = st.sampled_from(["", "   ", "\t", "\n"])
_nonblank_description = st.text(min_size=1, max_size=30).filter(lambda s: s.strip())


@st.composite
def _item(draw):
    """Genera un Place/Event con description en blanco, ausente o no vacia."""
    item: dict = {
        "id": draw(_slug),
        "name": draw(_name),
        "category": draw(st.text(max_size=10)),
    }
    kind = draw(st.sampled_from(["blank", "absent", "none", "nonblank"]))
    if kind == "blank":
        item["description"] = draw(_blank_description)
    elif kind == "none":
        item["description"] = None
    elif kind == "nonblank":
        item["description"] = draw(_nonblank_description)
    # kind == "absent": sin clave `description`
    return item


@st.composite
def _tourism_data(draw):
    """Genera un Tourism_Data con Places y Events en estados variados.

    Se usa un unico Locale (defaultLocale sin extras) para acotar la propiedad
    a la completitud de descripciones y evitar la rama de traducciones.
    """
    return {
        "site": {
            "name": draw(st.text(min_size=1, max_size=20)),
            "region": draw(st.text(min_size=1, max_size=20)),
            "defaultLocale": "es",
            "description": draw(_nonblank_description),
        },
        "places": draw(st.lists(_item(), max_size=5)),
        "events": draw(st.lists(_item(), max_size=5)),
    }


def _is_blank(value: object) -> bool:
    return not (isinstance(value, str) and value.strip())


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 10: Completitud de descripciones tras la generación
@settings(max_examples=200, deadline=None)
@given(data=_tourism_data())
def test_descriptions_complete_after_generation(data):
    """Tras `enrich` con un LLM_Provider exitoso, ninguna description queda vacia.

    Para todo Place o Event con `description` vacia, luego de `enrich` con un
    proveedor que responde texto no vacio, su `description` deja de estar vacia
    (Req 3.1, 3.2). Se mockea `get_provider` para no invocar un LLM real.

    Validates: Requirements 3.1, 3.2
    """
    original = copy.deepcopy(data)

    with mock.patch.object(
        generate_content, "get_provider", return_value=_FakeSuccessProvider()
    ):
        result = generate_content.enrich(copy.deepcopy(original))

    for kind in ("places", "events"):
        items = result.get(kind, [])
        assert len(items) == len(original.get(kind, []))
        for item in items:
            # Req 3.1 / 3.2: toda description quedo completa (no vacia).
            assert not _is_blank(item.get("description")), (
                f"{kind[:-1]} '{item.get('id')}' quedo con description vacia"
            )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
