#!/usr/bin/env bash
#
# Puriq — arranque en un solo comando.
#
#   ./start.sh                 pregunta el nombre del sitio y abre el asistente
#                              (la segunda vez retoma el ultimo, sin preguntar)
#   ./start.sh ruta/proyecto   trabaja sobre esa carpeta
#   ./start.sh --demo          construye y sirve el ejemplo de Potosi, sin asistente
#   ./start.sh --mcp           conecta Puriq a Claude Desktop / Kiro / Kiro CLI
#
# Prepara el entorno de Python, instala el agente y levanta el wizard. Es
# idempotente: correrlo de nuevo reutiliza lo que ya esta y arranca en segundos.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$REPO/agent"
VENV="$AGENT/.venv"
PY="$VENV/bin/python"
PURIQ="$VENV/bin/puriq"

rojo()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
gris()  { printf '\033[2m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }

# --- Requisitos ------------------------------------------------------------
# Se comprueban por adelantado y con el motivo a la vista: descubrir a mitad del
# build que falta npm es mucho peor que saberlo en el primer segundo.

if ! command -v python3 >/dev/null 2>&1; then
  rojo "Falta Python 3. Instalalo (https://www.python.org/downloads/) y volve a intentar."
  exit 1
fi

# El proyecto declara requires-python >=3.10.
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  rojo "Puriq necesita Python 3.10 o mas nuevo. Tenes $(python3 -V)."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  gris "Aviso: no se encontro npm. Podes cargar datos igual, pero generar el sitio"
  gris "necesita Node.js (https://nodejs.org). Instalalo antes de llegar al ultimo paso."
fi

# --- Entorno ---------------------------------------------------------------

if [ ! -x "$PY" ]; then
  gris "Preparando el entorno de Python (una sola vez)..."
  python3 -m venv "$VENV"
fi

# Se instala solo si falta o si `pyproject.toml` cambio despues de la ultima
# instalacion; asi el arranque habitual no paga el costo de pip.
MARCA="$VENV/.puriq-instalado"
if [ ! -f "$MARCA" ] || [ "$AGENT/pyproject.toml" -nt "$MARCA" ]; then
  gris "Instalando Puriq y sus dependencias..."
  "$PY" -m pip install --quiet --upgrade pip
  # El extra `mcp` viene incluido: es lo que permite conectar Puriq a Claude
  # Desktop o Kiro sin volver a instalar nada.
  "$PY" -m pip install --quiet -e "$AGENT[mcp]"
  touch "$MARCA"
fi

# --- Modo demo -------------------------------------------------------------

if [ "${1:-}" = "--mcp" ]; then
  exec "$PURIQ" mcp-connect
fi

if [ "${1:-}" = "--demo" ]; then
  exec "$PURIQ" demo
fi

# --- Proyecto --------------------------------------------------------------

# Los proyectos viven FUERA del repo, en ~/Puriq. El contenido que carga el
# usuario —sus textos, sus fotos, su marca— no es parte de la herramienta:
# guardarlo dentro del clon significaba que actualizar o volver a clonar Puriq se
# llevaba el sitio puesto. La ultima carpeta usada se recuerda en ~/.puriq para
# que el arranque siguiente no vuelva a preguntar.
SITIOS="$HOME/Puriq"
MEMORIA="$HOME/.puriq/ultimo-proyecto"

if [ -n "${1:-}" ]; then
  PROYECTO="$1"
elif [ -f "$MEMORIA" ] && [ -d "$(cat "$MEMORIA")" ]; then
  PROYECTO="$(cat "$MEMORIA")"
  gris "Continuando el ultimo proyecto. Para otro: ./start.sh ruta/al/proyecto"
elif [ -t 0 ]; then
  # Primera vez y hay terminal: se pregunta una sola vez, en lugar de inventar un
  # nombre generico que despues nadie cambia.
  echo
  printf '¿Como se llama el sitio que vas a armar? (ej: turismo-tarija): '
  read -r NOMBRE || NOMBRE=""
  NOMBRE="$(printf '%s' "$NOMBRE" | tr -cs '[:alnum:]._-' '-' | tr '[:upper:]' '[:lower:]' | sed 's/^-*//; s/-*$//')"
  [ -z "$NOMBRE" ] && NOMBRE="mi-sitio"
  PROYECTO="$SITIOS/$NOMBRE"
else
  PROYECTO="$SITIOS/mi-sitio"
fi

mkdir -p "$PROYECTO"
PROYECTO="$(cd "$PROYECTO" && pwd)"
mkdir -p "$(dirname "$MEMORIA")" && printf '%s\n' "$PROYECTO" > "$MEMORIA"

echo
verde "Puriq esta listo."
gris  "Proyecto: $PROYECTO"
gris  "Abrí http://127.0.0.1:4321 en tu navegador (Ctrl+C para cortar)."
echo

# `exec` para que Ctrl+C corte el servidor directamente, sin dejarlo huerfano.
PURIQ_PROJECT="$PROYECTO" exec "$PURIQ" init
