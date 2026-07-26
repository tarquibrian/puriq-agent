"""Paquete `intake`: núcleo compartido de las intake tools y su superficie pública.

Reexporta la superficie pública de `intake.tools` para importaciones cómodas
(`from puriq.intake import INTAKE_TOOL_SPECS`, etc.), tal como la consume la capa
MCP (`mcp/server.py`) y, más adelante, el loop web.
"""
from __future__ import annotations

from puriq.intake.tools import (
    INTAKE_GUION,
    INTAKE_TOOL_NAMES,
    INTAKE_TOOL_SPECS,
    run_intake_tool,
)

__all__ = [
    "INTAKE_TOOL_SPECS",
    "INTAKE_TOOL_NAMES",
    "INTAKE_GUION",
    "run_intake_tool",
]
