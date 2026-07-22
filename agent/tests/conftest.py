"""Configuracion de pruebas.

Asegura que el paquete `puriq` sea importable y aisla la unica dependencia de
red que se importa a nivel de modulo (`httpx`, usada por
`puriq.tools.import_open_data`) con un stub, de modo que las pruebas no toquen
la red ni requieran la instalacion completa de fronteras externas. Ninguna
prueba ejercita esas rutas de red; el stub solo permite el import.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# Hacer importable el paquete `puriq` (raiz del agente).
AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

# Stub de la frontera de red `httpx` si no esta instalada: solo habilita el
# import de nivel de modulo; nunca se invocan sus funciones en estas pruebas.
if "httpx" not in sys.modules:
    try:  # pragma: no cover - depende del entorno
        import httpx  # noqa: F401
    except ModuleNotFoundError:  # pragma: no cover
        sys.modules["httpx"] = types.ModuleType("httpx")
