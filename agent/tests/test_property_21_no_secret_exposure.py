"""Prueba de propiedad: no exposición de secretos (Property 21).

# Feature: agent-tools, Property 21: No exposición de secretos

*Para todo* error o salida producidos por las tools, el CLI o el MCP_Server,
ningún valor de secreto configurado (credenciales AWS, etc.) aparece en el texto.
El enmascarado transversal lo aplica `puriq.config.redact`, que usan tanto el
decorador de errores del CLI (`puriq.cli.manejar_errores`) como el handler de
error del servidor MCP (`puriq.mcp.server`).

Validates: Requirements 7.7, 8.4, 9.3
"""
from __future__ import annotations

import io
import os

import pytest
import typer
from hypothesis import given, settings
from hypothesis import strategies as st
from rich.console import Console

from puriq import cli, config
from puriq.config import redact


# --- estrategias -----------------------------------------------------------
# Máscara con la que `redact` reemplaza los secretos.
_MASK = config._MASK  # "***"

# Valores de secreto genéricos. Se excluye '*' del alfabeto: la máscara está
# compuesta por '*', de modo que un secreto que contuviera '*' podría "reaparecer"
# dentro de la propia máscara y producir un falso negativo ajeno a la propiedad.
# Las credenciales reales (AWS, tokens) no contienen '*'.
_secret_char = st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="*")
_secret_values = st.text(alphabet=_secret_char, min_size=1, max_size=60)

# Valores de secreto "seguros para markup": se usan en la ruta del CLI (rich),
# que interpreta '[' ']' como marcado. Se restringe al alfabeto realista de
# credenciales AWS/tokens (letras, dígitos y '/', '+', '=', '-', '_'). Además se
# exige una longitud mínima realista (>=16, como una credencial AWS): el CLI
# imprime chrome estático propio ("Error:", "Sugerencia:", texto de sugerencia)
# que no pasa por `redact`; un secreto de 1-2 caracteres coincidiría con esas
# palabras fijas y produciría un falso negativo ajeno a la propiedad.
_cli_secret_char = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/+=-_"
)
_cli_secret_values = st.text(alphabet=_cli_secret_char, min_size=16, max_size=60)

# Sufijos para nombres de variables de entorno de secreto. Se antepone un prefijo
# fijo para no colisionar con variables reales del entorno.
_name_suffix = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_", min_size=1, max_size=12
)
_NAME_PREFIX = "PURIQ_TEST_SECRET_"


@st.composite
def _secret_registry(draw, value_strategy=_secret_values):
    """Genera una lista de pares (nombre_env, valor) con nombres únicos.

    Cada nombre lleva el prefijo `PURIQ_TEST_SECRET_` y representa una variable
    de entorno tratada como secreto (registrada vía `get_env(..., secret=True)`).
    """
    n = draw(st.integers(min_value=1, max_value=4))
    suffixes = draw(st.lists(_name_suffix, min_size=n, max_size=n, unique=True))
    values = draw(st.lists(value_strategy, min_size=n, max_size=n))
    return [(_NAME_PREFIX + s, v) for s, v in zip(suffixes, values)]


class _register_secrets:
    """Context manager que registra secretos y restaura el estado al salir.

    Fija los valores en `os.environ`, los registra como secreto vía
    `config.get_env(name, secret=True)` y, al salir, restaura tanto el entorno
    como el registro interno `config._secret_names` para no filtrar estado entre
    ejemplos de Hypothesis.
    """

    def __init__(self, entries):
        self._entries = entries
        self._saved_env: dict[str, str | None] = {}
        self._saved_names: set[str] = set()

    def __enter__(self):
        self._saved_names = set(config._secret_names)
        for name, value in self._entries:
            self._saved_env[name] = os.environ.get(name)
            os.environ[name] = value
            # Registra el nombre como secreto (misma vía que usa el código real).
            assert config.get_env(name, secret=True) == value
        return self

    def __exit__(self, *exc):
        config._secret_names = self._saved_names
        for name, old in self._saved_env.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        return False


# --- Propiedad 21a: redact enmascara todo valor de secreto -----------------
# Feature: agent-tools, Property 21: No exposición de secretos
@settings(max_examples=200, deadline=None)
@given(entries=_secret_registry(), template=st.text(max_size=120))
def test_redact_removes_every_configured_secret(entries, template):
    """`redact` elimina todo valor de secreto configurado de cualquier texto.

    Para todo conjunto de secretos registrados y todo texto que los incruste
    (en cualquier posición), la salida de `redact` no contiene ninguno de los
    valores de secreto.

    Validates: Requirements 7.7, 8.4, 9.3
    """
    with _register_secrets(entries):
        parts = [template]
        for _, value in entries:
            parts.append(f"antes-{value}-despues repetido:{value}")
        text = " ".join(parts)

        redacted = redact(text)

        for _, value in entries:
            assert value not in redacted


# --- Propiedad 21b: la salida de error del CLI no expone secretos ----------
# Feature: agent-tools, Property 21: No exposición de secretos
@settings(max_examples=120, deadline=None)
@given(
    entries=_secret_registry(value_strategy=_cli_secret_values),
    noise=st.text(alphabet=_cli_secret_char, max_size=40),
    kind=st.sampled_from(["value", "file", "network", "generic"]),
)
def test_cli_error_output_hides_secrets(entries, noise, kind):
    """La salida de error del CLI (vía `manejar_errores`) nunca imprime secretos.

    Para toda excepción de tool cuyo mensaje incruste valores de secreto, el
    decorador transversal del CLI presenta un mensaje descriptivo enmascarado con
    `redact`: ningún valor de secreto aparece en lo impreso a stderr.

    Validates: Requirements 8.4, 9.3
    """
    saved_console = cli._err_console
    with _register_secrets(entries):
        secret_blob = " ".join(v for _, v in entries)
        message = f"{noise} {secret_blob} fin"

        if kind == "value":
            exc: Exception = ValueError(message)
        elif kind == "file":
            exc = FileNotFoundError(message)
        elif kind == "network":
            # Nombre de clase que el CLI clasifica como fallo de red/servicio.
            exc = type("FakeClientError", (Exception,), {})(message)
        else:
            exc = RuntimeError(message)

        buf = io.StringIO()
        # width grande evita que rich parta un secreto entre líneas (falso negativo).
        cli._err_console = Console(file=buf, force_terminal=False, no_color=True, width=100000)
        try:

            @cli.manejar_errores
            def _boom():
                raise exc

            with pytest.raises(typer.Exit):
                _boom()

            output = buf.getvalue()
            for _, value in entries:
                assert value not in output
        finally:
            cli._err_console = saved_console


# --- Propiedad 21c: el mensaje de error del MCP no expone secretos ---------
# Feature: agent-tools, Property 21: No exposición de secretos
#
# Refleja exactamente la ruta de error del servidor MCP
# (`puriq.mcp.server.build_server._call_tool`), que construye el mensaje de error
# del cliente como `redact(f"La tool '{name}' falló: {exc}")`.
@settings(max_examples=120, deadline=None)
@given(
    entries=_secret_registry(),
    tool_name=st.sampled_from(
        ["scan_resources", "import_open_data", "generate_content", "build_site", "deploy"]
    ),
    noise=st.text(max_size=40),
)
def test_mcp_error_message_hides_secrets(entries, tool_name, noise):
    """El mensaje de error que el MCP devuelve al cliente no contiene secretos.

    Para toda excepción de tool que incruste valores de secreto, el mensaje que
    el servidor MCP entrega al cliente (enmascarado con `redact`) no expone
    ninguno de esos valores.

    Validates: Requirements 8.4, 9.3
    """
    with _register_secrets(entries):
        secret_blob = " ".join(v for _, v in entries)
        exc = RuntimeError(f"{noise} {secret_blob}")

        # Misma expresión que usa el handler de error del servidor MCP.
        mensaje = redact(f"La tool '{tool_name}' falló: {exc}")

        for _, value in entries:
            assert value not in mensaje


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
