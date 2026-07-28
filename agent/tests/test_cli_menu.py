"""Pruebas del menú interactivo del CLI (`puriq` sin subcomando).

Lo que se cuida acá es sobre todo que el menú no estorbe: un CLI que abre un
prompt cuando lo llama un script se cuelga esperando una respuesta que nunca
llega, así que sin terminal tiene que comportarse como siempre.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq import cli  # noqa: E402

runner = CliRunner()


@pytest.fixture()
def interactivo():
    """Simula una terminal, que es cuando el menú aparece."""
    with patch.object(cli, "_interactivo", return_value=True):
        yield


def test_sin_terminal_muestra_la_ayuda_y_no_pregunta():
    """Llamado desde un script, `puriq` no debe abrir un prompt (colgaría)."""
    with patch.object(cli, "_interactivo", return_value=False), patch.object(
        cli.typer, "prompt"
    ) as prompt:
        resultado = runner.invoke(cli.app, [])

    assert resultado.exit_code == 0
    assert "Usage" in resultado.output
    assert not prompt.called


def test_el_menu_muestra_el_estado_antes_de_las_opciones(interactivo):
    """Saber qué hay configurado es lo que orienta cuál opción elegir."""
    with patch.object(cli.typer, "prompt", return_value="q"):
        resultado = runner.invoke(cli.app, [])

    for etiqueta in ("Proyecto", "Clientes MCP", "Clave de LLM"):
        assert etiqueta in resultado.output
    for etiqueta, _, _ in cli._OPCIONES:
        assert etiqueta in resultado.output


@pytest.mark.parametrize(
    ("eleccion", "comando"),
    [("1", "demo"), ("2", "init"), ("3", "mcp_connect"), ("4", "config_llm")],
)
def test_cada_opcion_invoca_su_comando(interactivo, eleccion, comando):
    """El menú delega en los mismos comandos del CLI, sin reimplementarlos."""
    with patch.object(cli.typer, "prompt", return_value=eleccion), patch.object(
        cli, comando
    ) as doble:
        runner.invoke(cli.app, [], catch_exceptions=False)

    assert doble.called, comando


def test_salir_no_ejecuta_nada(interactivo):
    with patch.object(cli.typer, "prompt", return_value="q"), patch.object(
        cli, "demo"
    ) as doble:
        resultado = runner.invoke(cli.app, [])

    assert resultado.exit_code == 0
    assert not doble.called


def test_una_opcion_fuera_de_rango_se_rechaza(interactivo):
    with patch.object(cli.typer, "prompt", return_value="9"):
        resultado = runner.invoke(cli.app, [])

    assert resultado.exit_code == 1
    assert "inválida" in resultado.output.lower()


def test_los_subcomandos_siguen_funcionando_sin_pasar_por_el_menu(interactivo):
    """Con subcomando el menú no se muestra, aunque haya terminal."""
    with patch.object(cli.typer, "prompt") as prompt:
        resultado = runner.invoke(cli.app, ["--help"])

    assert resultado.exit_code == 0
    assert not prompt.called


def test_slug_normaliza_el_nombre_del_sitio():
    assert cli._slug("Turismo Tarija") == "turismo-tarija"
    assert cli._slug("  Hostal Kori Wasi  ") == "hostal-kori-wasi"
    assert cli._slug("Potosí ¡2026!") == "potosi-2026"
    # Sin nada aprovechable se cae a un nombre por defecto en vez de crear "".
    assert cli._slug("¿?") == "mi-sitio"
