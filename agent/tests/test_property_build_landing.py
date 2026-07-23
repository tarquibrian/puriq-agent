"""Prueba de propiedad para `build_landing` (Tarea 11.4, DD-6).

# Feature: landing-and-design-system, Property 11: `build_landing` asigna un orden coherente con la posicion

Property 11 (Validates: Requirements 14.2, 10.4): para toda seleccion **ordenada**
de secciones cuyo `type` pertenece al catalogo soportado, `build_landing` produce
una lista donde cada seccion recibe un `order` entero >= 1 estrictamente creciente
y coherente con su posicion (`order == indice+1`), todo `type` de salida pertenece
al catalogo, y el `content` provisto se conserva sin alteraciones. Ademas, si la
seleccion contiene un `type` FUERA del catalogo, `build_landing` rechaza la
entrada con `LandingCatalogError`.

Se usa Hypothesis (>= 100 iteraciones), una sola propiedad por prueba, etiquetada
con la propiedad del diseno. La funcion bajo prueba es pura, por lo que no se
mockea nada.
"""
from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.wizard.landing import (
    LANDING_CATALOG,
    LandingCatalogError,
    build_landing,
)

# Un tipo garantizado fuera del catalogo soportado, para el caso de rechazo.
_OUT_OF_CATALOG = "carousel"
assert _OUT_OF_CATALOG not in LANDING_CATALOG


@st.composite
def _content(draw):
    """Genera un `content` opcional: un mapping de copy por seccion.

    Se mantiene simple (claves/valores de texto) porque `build_landing` solo lo
    conserva tal cual; su forma interna no afecta a la asignacion de `order`.
    """
    return draw(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=8),
            values=st.text(max_size=16),
            max_size=3,
        )
    )


@st.composite
def _section(draw):
    """Genera un descriptor de seccion con `type` DENTRO del catalogo.

    `enabled` es booleano (el catalogo admite on/off) y `content` es opcional
    para ejercitar ambas ramas (con y sin la clave `content`).
    """
    section: dict = {
        "type": draw(st.sampled_from(list(LANDING_CATALOG))),
        "enabled": draw(st.booleans()),
    }
    if draw(st.booleans()):
        section["content"] = draw(_content())
    return section


@st.composite
def _ordered_selection(draw):
    """Seleccion ordenada (posiblemente vacia) de secciones del catalogo."""
    return draw(st.lists(_section(), max_size=6))


# Feature: landing-and-design-system, Property 11: `build_landing` asigna un orden coherente con la posicion
@settings(max_examples=200, deadline=None)
@given(selection=_ordered_selection())
def test_build_landing_assigns_order_coherent_with_position(selection):
    """`build_landing` asigna un `order` coherente con la posicion (Property 11).

    Para toda seleccion ordenada de secciones del catalogo: el resultado tiene la
    misma longitud, cada `order` es entero >= 1, estrictamente creciente y igual a
    `indice+1`, todo `type` de salida esta en el catalogo, y el `content` provisto
    se conserva intacto.

    Validates: Requirements 14.2, 10.4
    """
    # Copia defensiva para verificar despues que la entrada no se muta.
    selection_before = copy.deepcopy(selection)

    result = build_landing(selection)

    # Misma cantidad de secciones que en la seleccion.
    assert len(result) == len(selection)

    orders = [section["order"] for section in result]

    # Req 14.2: cada `order` es entero >= 1 y coherente con su posicion (1-based).
    for index, order in enumerate(orders):
        assert isinstance(order, int) and not isinstance(order, bool)
        assert order >= 1
        assert order == index + 1

    # Estrictamente creciente (consecuencia de order == index+1, verificado aparte).
    assert all(a < b for a, b in zip(orders, orders[1:]))

    # Req 10.4: todo `type` de salida pertenece al catalogo soportado.
    for section in result:
        assert section["type"] in LANDING_CATALOG
        assert isinstance(section["enabled"], bool)

    # El `content` provisto se conserva sin alteraciones (igualdad de valor).
    for original, produced in zip(selection, result):
        if "content" in original:
            assert produced["content"] == original["content"]
        else:
            assert "content" not in produced

    # La funcion es pura: no muta la entrada.
    assert selection == selection_before


# Feature: landing-and-design-system, Property 11: `build_landing` asigna un orden coherente con la posicion
@settings(max_examples=200, deadline=None)
@given(
    prefix=_ordered_selection(),
    suffix=_ordered_selection(),
)
def test_build_landing_rejects_type_outside_catalog(prefix, suffix):
    """Un `type` fuera del catalogo hace fallar `build_landing` (Property 11, Req 10.4).

    Se intercala una seccion cuyo `type` no pertenece al catalogo entre secciones
    validas; `build_landing` debe rechazar la seleccion completa con
    `LandingCatalogError` (no se construye una portada parcial).

    Validates: Requirements 10.4
    """
    invalid = {"type": _OUT_OF_CATALOG, "enabled": True}
    selection = [*prefix, invalid, *suffix]

    with pytest.raises(LandingCatalogError):
        build_landing(selection)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
