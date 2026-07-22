"""Utilidad compartida de slugificación (DD-2).

Extraída de scan_resources para que import_open_data (y otras tools) generen
ids con la misma normalización, sin duplicar código.
"""
from __future__ import annotations

import re
import unicodedata


def slugify(text: str) -> str:
    """Convierte un texto en un id kebab-case ASCII: 'Cerro Rico' -> 'cerro-rico'."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-")
