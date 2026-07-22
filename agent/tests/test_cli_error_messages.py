"""Pruebas unitarias de los mensajes de error del CLI (Req 9.1, 9.5).

Cubren el manejo de errores transversal de `puriq.cli`:

  - Req 9.1: cuando una tool/core lanza un error durante un comando, el CLI
    muestra un mensaje DESCRIPTIVO que indica la causa y, cuando puede
    inferirse, una accion sugerida. Ademas termina con codigo de salida != 0.
  - Req 9.5: cuando falta una variable de entorno requerida, el mensaje del CLI
    NOMBRA la variable faltante.

Estrategia: se invocan los comandos del CLI con `typer.testing.CliRunner` y se
sustituye `puriq.core.Puriq` por un doble de prueba cuyo constructor lanza la
excepcion deseada (un error de tool generico, un error de red y un
`MissingEnvVarError`). Asi se ejercita el decorador `manejar_errores` real sin
tocar la red ni servicios externos. Tambien se prueba directamente la funcion
traductora `_describir_error` para fijar el contrato causa/accion.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from puriq import cli
from puriq.config import MissingEnvVarError

runner = CliRunner()


def _make_raising_puriq(exc: BaseException):
    """Devuelve un reemplazo de `Puriq` cuyo constructor lanza `exc`.

    Todos los comandos del CLI instancian `Puriq(project)` antes de invocar una
    operacion, por lo que lanzar en `__init__` simula que "una tool lanza un
    error durante un comando" de forma independiente del comando elegido.
    """

    class _RaisingPuriq:
        def __init__(self, *args, **kwargs):
            raise exc

    return _RaisingPuriq


def _flat(text: str) -> str:
    """Aplana la salida (rich puede envolver lineas) para asertar subcadenas.

    Colapsa cualquier secuencia de espacios/saltos de linea en un solo espacio,
    de modo que las aserciones no dependan del ancho de la terminal simulada.
    """
    return " ".join(text.split())


# --- Req 9.1: error de tool -> mensaje descriptivo (causa) -----------------

def test_tool_error_shows_descriptive_cause_and_exit_code(monkeypatch):
    monkeypatch.setattr(
        "puriq.core.Puriq",
        _make_raising_puriq(RuntimeError("no se pudo generar el contenido")),
    )

    result = runner.invoke(cli.app, ["build"])

    # No se falla en silencio: se sale con codigo distinto de cero.
    assert result.exit_code == 1
    out = _flat(result.output)
    # Mensaje descriptivo: prefijo "Error:" + causa legible (no una traza cruda).
    assert "Error:" in out
    assert "no se pudo generar el contenido" in out


# --- Req 9.1: error de tool -> causa + accion sugerida ---------------------

def test_tool_error_includes_cause_and_suggested_action(monkeypatch):
    # Excepcion con nombre de clase reconocido como fallo de red/servicio, que
    # `_describir_error` traduce a causa + accion sugerida.
    class ConnectError(Exception):
        pass

    monkeypatch.setattr(
        "puriq.core.Puriq",
        _make_raising_puriq(ConnectError("no se pudo conectar al endpoint")),
    )

    result = runner.invoke(cli.app, ["deploy"])

    assert result.exit_code == 1
    out = _flat(result.output)
    # Causa descriptiva.
    assert "Error:" in out
    assert "Fallo de red o servicio externo" in out
    assert "no se pudo conectar al endpoint" in out
    # Accion sugerida presente (causa + accion, Req 9.1).
    assert "Sugerencia:" in out


# --- Req 9.5: variable de entorno requerida ausente se nombra --------------

def test_missing_env_var_is_named_in_message(monkeypatch):
    var_name = "PURIQ_LLM_MODEL"
    monkeypatch.setattr(
        "puriq.core.Puriq",
        _make_raising_puriq(MissingEnvVarError(var_name)),
    )

    result = runner.invoke(cli.app, ["build"])

    assert result.exit_code == 1
    out = _flat(result.output)
    # El mensaje NOMBRA la variable faltante (Req 9.5).
    assert var_name in out
    # Y ofrece una accion accionable (definirla en agent/.env).
    assert "Sugerencia:" in out
    assert "agent/.env" in out


# --- Pruebas directas de la funcion traductora (contrato causa/accion) -----

def test_describir_error_missing_env_var_returns_named_cause_and_action():
    causa, accion = cli._describir_error(MissingEnvVarError("PURIQ_DEPLOY_TARGET"))
    # La causa nombra la variable (Req 9.5) y hay una accion sugerida (Req 9.1).
    assert "PURIQ_DEPLOY_TARGET" in causa
    assert accion is not None
    assert "agent/.env" in accion


def test_describir_error_generic_exception_returns_descriptive_cause():
    causa, accion = cli._describir_error(RuntimeError("algo salio mal"))
    # Mensaje descriptivo con el tipo y el detalle, sin traza cruda.
    assert "RuntimeError" in causa
    assert "algo salio mal" in causa
    # Un error generico no infiere accion sugerida.
    assert accion is None
