"""edit_content: edición a nivel de campo de un Place o Event (Req 7).

Localiza un Place o Event por ``id`` dentro de un dict ``tourism-data`` ya
cargado en memoria y aplica un **merge a nivel de campo** (DD-5) reutilizando
``_merge.merge_fields``: solo se sobrescriben los campos indicados en ``fields``
y se preserva el resto ("editar sin pisar"); el ``id`` nunca se regenera aunque
``fields`` intente cambiarlo (Req 7.1, 7.2).

Esta función es **pura**: no realiza E/S y **no muta** el ``data`` de entrada.
Devuelve un ``data`` nuevo (copia profunda) con el elemento fusionado, siguiendo
la sección Components del diseño ("edit_content ... Devuelve el data mutado").
La validación estricta contra ``tourism-data.schema.json`` y la escritura atómica
(validar-antes-de-escribir, Req 7.4, 7.5, 7.6) ocurren en la capa core al
persistir, mediante ``_persist.validate_then_write`` contra ``"tourism-data"``;
aquí no se valida ni se escribe.

Si el ``id`` no existe ni en ``places`` ni en ``events`` se lanza un error
accionable "no encontrado" (Req 7.3), sin producir ningún ``data`` de salida.
"""
from __future__ import annotations

import copy
from typing import Any

from puriq.tools._merge import merge_fields

_PLACES = "places"
_EVENTS = "events"


def edit(data: dict, *, id: str, fields: dict) -> dict:
    """Fusiona ``fields`` sobre el Place o Event con ese ``id`` en ``data``.

    Busca primero en ``places`` y luego en ``events``. Sobre el elemento
    encontrado aplica ``merge_fields`` (sobrescribe solo los campos indicados,
    preserva el resto, nunca regenera el ``id``; Req 7.1, 7.2, DD-5) y devuelve un
    ``data`` **nuevo** con ese elemento reemplazado.

    Esta función es pura: hace una copia profunda de ``data`` y no muta la
    entrada. No valida ni escribe: la validación contra
    ``tourism-data.schema.json`` y la persistencia atómica las realiza la capa
    core con ``_persist.validate_then_write`` (Req 7.4, 7.5, 7.6).

    Args:
        data: dict conforme a ``tourism-data.schema.json`` (o subconjunto con las
            claves ``places``/``events``).
        id: identificador del Place o Event a editar.
        fields: campos a sobrescribir (merge a nivel de campo de primer nivel).

    Returns:
        Un ``data`` nuevo (copia profunda) con el elemento editado.

    Raises:
        ValueError: si ``id`` no existe ni en ``places`` ni en ``events``
            (error "no encontrado", Req 7.3).
    """
    result: dict[str, Any] = copy.deepcopy(data)

    for kind in (_PLACES, _EVENTS):
        items = result.get(kind)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict) and item.get("id") == id:
                items[index] = merge_fields(item, fields)
                return result

    raise ValueError(
        f"No se encontró ningún lugar ni evento con id '{id}' en tourism-data. "
        f"Verifica el id (por ejemplo consultando con query_content) e intentá "
        f"de nuevo."
    )
