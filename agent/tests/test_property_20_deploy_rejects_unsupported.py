"""Prueba de propiedad: deploy rechaza destinos no soportados (Property 20).

# Feature: agent-tools, Property 20: Deploy rechaza destinos no soportados
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from puriq.tools import deploy


# Conjunto de destinos validos (unica fuente de verdad: el registro de deploy).
_VALID_TARGETS = set(deploy.ADAPTERS)


# --- estrategia de destinos no soportados ---------------------------------
# Genera cadenas arbitrarias que representan destinos de deploy. Cubre texto
# libre, cadenas vacias y variantes cercanas a los destinos validos (mayusculas,
# con espacios) para ejercitar el rechazo de cualquier cosa fuera de ADAPTERS.
_arbitrary_targets = st.one_of(
    st.text(max_size=40),
    st.sampled_from([t.upper() for t in deploy.ADAPTERS]),
    st.sampled_from([f" {t} " for t in deploy.ADAPTERS]),
    st.sampled_from([f"{t}-x" for t in deploy.ADAPTERS]),
    st.just(""),
)


# --- propiedad -------------------------------------------------------------
# Feature: agent-tools, Property 20: Deploy rechaza destinos no soportados
@settings(max_examples=200, deadline=None)
@given(target=_arbitrary_targets)
def test_deploy_rejects_unsupported_targets(target):
    """Todo destino fuera de `ADAPTERS` es rechazado listando los destinos validos.

    Para toda cadena de destino que no pertenezca a `deploy.ADAPTERS`,
    `deploy.run` produce un `ValueError` cuyo mensaje enumera todos los destinos
    validos, antes de tocar el disco o cualquier frontera externa (Req 7.2).

    Validates: Requirements 7.2
    """
    assume(target not in _VALID_TARGETS)

    # La ruta del proyecto es irrelevante: la validacion del destino ocurre
    # antes de comprobar `dist/`, de modo que el error de destino no soportado
    # se dispara con cualquier ruta.
    with pytest.raises(ValueError) as excinfo:
        deploy.run(Path("/nonexistent/project"), target=target)

    message = str(excinfo.value)

    # El mensaje debe enumerar TODOS los destinos validos (Req 7.2).
    for valid in deploy.ADAPTERS:
        assert valid in message


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
