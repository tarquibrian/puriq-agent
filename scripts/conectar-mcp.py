#!/usr/bin/env python3
"""Registra Puriq como servidor MCP en los clientes que haya instalados.

Conectar Puriq a Claude Desktop o a Kiro significaba abrir un JSON escondido en
`Library/Application Support`, acertar la ruta absoluta al Python del entorno y
no romper la configuracion que ya estuviera ahi. Es la parte mas fragil de probar
el proyecto y la que menos tiene que ver con lo que Puriq hace.

Este script detecta los clientes presentes, muestra exactamente que va a escribir
y **pregunta antes de tocar nada**: es configuracion de aplicaciones del usuario,
no del repositorio. Fusiona con lo que ya exista (nunca reemplaza el archivo) y
deja una copia de respaldo junto al original.

Uso:
    python3 scripts/conectar-mcp.py [--python RUTA] [--si]

    --python  interprete del entorno de Puriq (por defecto, el de agent/.venv)
    --si      no preguntar; asumir que si (para automatizacion)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / "agent" / ".venv" / "bin" / "python"

#: Nombre con el que Puriq queda registrado en los clientes.
NOMBRE = "puriq"


def _c(texto: str, codigo: str) -> str:
    return f"\033[{codigo}m{texto}\033[0m"


def verde(t: str) -> str:
    return _c(t, "32")


def gris(t: str) -> str:
    return _c(t, "2")


def rojo(t: str) -> str:
    return _c(t, "31")


def preguntar(pregunta: str, asumir_si: bool) -> bool:
    """Confirmacion explicita antes de modificar la configuracion de una app."""
    if asumir_si:
        return True
    if not sys.stdin.isatty():
        return False
    return input(f"{pregunta} [s/N]: ").strip().lower() in {"s", "si", "sí", "y", "yes"}


def escribir_json_fusionado(ruta: Path, python: str) -> None:
    """Agrega la entrada `mcpServers.puriq` conservando el resto del archivo.

    El archivo puede tener otros servidores y preferencias del usuario, asi que se
    lee, se fusiona y se reescribe; nunca se sobrescribe entero. Antes de tocarlo
    se deja `<archivo>.puriq-backup`.
    """
    datos: dict = {}
    if ruta.exists():
        shutil.copy2(ruta, ruta.with_suffix(ruta.suffix + ".puriq-backup"))
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Un archivo corrupto no se pisa: es del usuario y puede tener cosas
            # que no queremos perder.
            raise RuntimeError(
                f"{ruta} no es un JSON valido. Revisalo a mano antes de continuar."
            )
    if not isinstance(datos, dict):
        raise RuntimeError(f"{ruta} no tiene la forma esperada.")

    servidores = datos.setdefault("mcpServers", {})
    servidores[NOMBRE] = {
        "command": python,
        "args": ["-m", "puriq.mcp.server"],
        # `get_state` es de solo lectura y el agente la consulta en cada turno:
        # aprobarla a mano cada vez vuelve la conversacion impracticable. Las que
        # escriben siguen pidiendo confirmacion.
        "disabled": False,
        "autoApprove": ["get_state"],
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def conectar_por_archivo(nombre_cliente: str, ruta: Path, python: str, asumir_si: bool) -> bool:
    print(f"\n{verde(nombre_cliente)}")
    print(gris(f"  Configuracion: {ruta}"))
    if not preguntar("  ¿Registro Puriq acá?", asumir_si):
        print(gris("  Omitido."))
        return False
    try:
        escribir_json_fusionado(ruta, python)
    except RuntimeError as exc:
        print(rojo(f"  {exc}"))
        return False
    print(verde("  Listo. Reinicia la aplicacion para que lo tome."))
    return True


def conectar_kiro_cli(python: str, asumir_si: bool) -> bool:
    """Usa `kiro-cli mcp add`, que gestiona su propio archivo de configuracion."""
    print(f"\n{verde('Kiro CLI')}")
    print(gris("  Se registra con: kiro-cli mcp add --name puriq --scope global"))
    if not preguntar("  ¿Registro Puriq acá?", asumir_si):
        print(gris("  Omitido."))
        return False
    try:
        subprocess.run(
            [
                "kiro-cli", "mcp", "add",
                "--name", NOMBRE,
                "--command", python,
                "--args", "-m", "--args", "puriq.mcp.server",
                "--scope", "global",
                "--force",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(rojo(f"  Fallo: {(exc.stderr or exc.stdout or '').strip()[:200]}"))
        return False
    except FileNotFoundError:
        print(rojo("  No se encontro el comando kiro-cli."))
        return False
    print(verde("  Listo."))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Conecta Puriq a los clientes MCP instalados.")
    ap.add_argument("--python", default=str(VENV_PY), help="interprete del entorno de Puriq")
    ap.add_argument("--si", action="store_true", help="no preguntar; asumir que si")
    args = ap.parse_args()

    # `os.path.abspath` en vez de `Path.resolve()`: el python del entorno es un
    # enlace al interprete del sistema, y resolverlo registraba ESE, con el que
    # `puriq` no es importable. Hace falta la ruta del entorno tal cual.
    python = os.path.abspath(args.python)
    if not Path(python).is_file():
        print(rojo(f"No existe el interprete {python}."))
        print(gris("Corre ./start.sh una vez para crear el entorno y volve a intentar."))
        return 1

    print("Puriq se expone como servidor MCP: cualquier cliente compatible puede")
    print("conducir el registro completo con SU propio modelo, sin credenciales acá.")
    print(gris(f"\nInterprete: {python}"))

    hogar = Path.home()
    candidatos: list[tuple[str, Path]] = [
        (
            "Claude Desktop",
            hogar / "Library/Application Support/Claude/claude_desktop_config.json",
        ),
        ("Kiro", hogar / ".kiro/settings/mcp.json"),
    ]

    conectados = 0
    for nombre, ruta in candidatos:
        # Se considera instalado si ya existe su carpeta de configuracion: crear
        # un archivo para una app ausente solo dejaria basura.
        if not ruta.parent.exists():
            continue
        if conectar_por_archivo(nombre, ruta, python, args.si):
            conectados += 1

    if shutil.which("kiro-cli") and conectar_kiro_cli(python, args.si):
        conectados += 1

    print()
    if conectados:
        print(verde(f"Puriq quedo conectado a {conectados} cliente(s)."))
        print("Empeza la conversacion diciendo sobre que carpeta trabajar:")
        print(gris('  "Trabajemos sobre ~/Puriq/mi-sitio. Llama a get_guion y ayudame a armar el sitio."'))
    else:
        print("No se conecto ningun cliente.")
        print(gris("Guia manual: docs/mcp-clientes.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
