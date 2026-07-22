"""Merge a nivel de campo (DD-5).

Semántica compartida de edición usada por `edit_article`, `edit_content` y
`bulk_update`: una edición sobrescribe **solo** los campos indicados y preserva
el resto de los campos del elemento existente ("editar sin pisar").

Este helper es **puro**: no realiza E/S, no muta sus entradas y devuelve un
diccionario nuevo (copia profunda). La regeneración del `id` nunca ocurre en una
edición; si `fields` intenta cambiar el `id`, se conserva el `id` original del
`target` (editar `title`/`name` no altera el `id`, Req 4.3).
"""
from __future__ import annotations

import copy
from typing import Any


def merge_fields(target: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Fusiona `fields` sobre `target` a nivel de campo, sin mutar las entradas.

    Reglas (DD-5):
    - Es un merge de campos de primer nivel: cada clave presente en `fields`
      reemplaza por completo el valor de esa clave en `target`. No se hace
      merge profundo de estructuras anidadas.
    - Las claves de `target` que no aparecen en `fields` se preservan intactas.
    - Nunca regenera el `id`: si `target` tiene `id`, ese `id` se conserva aunque
      `fields` incluya un `id` distinto.

    Args:
        target: El elemento existente (Article, Place o Event) a editar.
        fields: Los campos a sobrescribir.

    Returns:
        Un diccionario nuevo (copia profunda) con los campos fusionados.
    """
    result: dict[str, Any] = copy.deepcopy(target)

    original_id = target.get("id", None)
    had_id = "id" in target

    for key, value in fields.items():
        result[key] = copy.deepcopy(value)

    # Nunca regenerar/cambiar el id en una edición (Req 4.3).
    if had_id:
        result["id"] = copy.deepcopy(original_id)

    return result
