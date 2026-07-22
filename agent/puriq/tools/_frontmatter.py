"""Codec minimo de frontmatter para Articles (DD-2).

Sin dependencias nuevas (no PyYAML / no python-frontmatter). Parsea y serializa
un bloque delimitado por '---' al inicio de un markdown, con claves escalares
(id, title, date, category, summary) y listas simples (tags), separando el
frontmatter del cuerpo markdown (body).

Formato del bloque
------------------
    ---
    id: "cerro-rico"
    title: "Cerro Rico"
    date: "2024-01-01"
    category: "historia"
    summary: "Un resumen"
    tags: ["mina", "colonial"]
    ---
    <cuerpo markdown libre>

Cada valor se serializa con JSON (`json.dumps`). Los escalares quedan entre
comillas y las listas se escriben en **forma inline** `clave: ["a", "b"]`. Se
elige una unica forma de lista (inline JSON) para mantener `parse`/`serialize`
simetricos. El uso de JSON garantiza el round-trip incluso para strings con
caracteres especiales (`:`, comillas, corchetes, espacios al inicio/fin).

Reglas de parseo (tolerante para archivos escritos a mano):
  - Solo se parsea el PRIMER bloque frontmatter, ubicado al inicio del texto
    (la primera linea debe ser exactamente '---'). Cualquier '---' posterior
    forma parte del cuerpo.
  - Si el texto no comienza con un bloque frontmatter valido (sin '---' de
    apertura o sin '---' de cierre), `parse` devuelve ({}, texto_original).
  - Cada linea `clave: valor` intenta interpretarse como JSON; si no es JSON
    valido, se acepta como escalar sin comillas, y `[a, b]` como lista simple.

Propiedad de round-trip (Property 1): para todo frontmatter y body,
`parse(serialize(fm, body))` reproduce `(fm, body)`.
"""
from __future__ import annotations

import json

_DELIMITER = "---"


def _serialize_value(value: object) -> str:
    """Serializa un valor de frontmatter a su forma JSON en una linea."""
    return json.dumps(value, ensure_ascii=False)


def _parse_value(raw: str) -> object:
    """Interpreta el lado derecho de `clave: valor`.

    Prioriza JSON (comillas / arrays / numeros); si falla, acepta listas
    simples `[a, b]` y, en ultimo caso, un escalar sin comillas.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        pass
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [item.strip() for item in inner.split(",")]
    return raw


def parse(text: str) -> tuple[dict, str]:
    """Divide un markdown en (frontmatter dict, body).

    Solo se reconoce un bloque '---' al comienzo del texto. Si no hay un bloque
    frontmatter valido, devuelve ({}, text) sin modificar el texto.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _DELIMITER:
        return {}, text

    closing_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _DELIMITER:
            closing_index = i
            break
    if closing_index is None:
        return {}, text

    frontmatter: dict = {}
    for line in lines[1:closing_index]:
        if not line.strip():
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        frontmatter[key.strip()] = _parse_value(raw)

    body = "\n".join(lines[closing_index + 1:])
    return frontmatter, body


def serialize(frontmatter: dict, body: str) -> str:
    """Serializa (frontmatter, body) a markdown con el bloque '---' al inicio."""
    lines = [_DELIMITER]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {_serialize_value(value)}")
    lines.append(_DELIMITER)
    block = "\n".join(lines) + "\n"
    return block + body
