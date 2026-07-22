"""delete_content: eliminación de un Place o Event por `id` con integridad
referencial (Req 8).

Elimina de un dict ``tourism-data`` ya cargado en memoria el Place o Event cuyo
`id` coincida, manejando la integridad referencial: al eliminar un Place, los
Events cuyo `placeId` referenciaba ese Place quedarían con una referencia
colgante; esta tool los detecta, los **informa** (``affectedEvents``) y **limpia**
su campo `placeId` de modo que el ``tourism-data`` resultante no contenga ninguna
referencia `placeId` que apunte al Place eliminado (Req 8.4, 8.5).

Función **pura**: no lee ni escribe disco y **no muta la entrada**; opera sobre
una copia profunda del ``data`` recibido y devuelve el documento resultante. La
**validación contra el esquema y la persistencia atómica** ocurren en el core
(`puriq.core`, task 13.1) usando `tools/_persist.validate_then_write`, que valida
el ``tourism-data`` resultante contra `tourism-data.schema.json` **antes** de
escribir (Req 8.6, 8.7, DD-6). Esta tool solo produce el nuevo documento; no
persiste ni valida por sí misma.

Forma de retorno (contrato de esta tool):

    {
        "id": <str>,                 # id del elemento eliminado
        "affectedEvents": [<str>],   # ids de los Events cuyo placeId fue limpiado
                                     #   (lista vacía si no había referencias o si
                                     #   se eliminó un Event, no un Place)
        "data": <dict>,              # tourism-data resultante (copia mutada); el
                                     #   core lo valida y persiste
    }

`affectedEvents` es siempre una lista (vacía cuando no aplica), para que el
llamador no tenga que distinguir casos.
"""
from __future__ import annotations

import copy

_PLACES = "places"
_EVENTS = "events"


def delete(data: dict, *, id: str) -> dict:
    """Elimina un Place o Event por `id`, manejando integridad referencial.

    Busca `id` primero entre los Places y luego entre los Events del ``data``
    recibido. Si es un Place, además limpia el campo `placeId` de todo Event que
    lo referenciaba, dejando el documento sin referencias colgantes (Req 8.5).

    Es una función pura: trabaja sobre una copia profunda de ``data`` y no muta
    la entrada. La validación contra `tourism-data.schema.json` y la escritura
    atómica las realiza el core al persistir (Req 8.6, 8.7, DD-6); esta función
    únicamente construye y devuelve el documento resultante.

    Args:
        data: dict conforme a ``tourism-data.schema.json`` (o subconjunto con las
            claves ``places``/``events``).
        id: identificador del Place o Event a eliminar.

    Returns:
        Un dict con la forma documentada a nivel de módulo:
        ``{"id", "affectedEvents", "data"}``. ``data`` es una copia profunda del
        documento con el elemento eliminado (y, si era un Place, con el `placeId`
        colgante removido de los Events afectados).

    Raises:
        ValueError: si `id` no corresponde a ningún Place ni Event (Req 8.3),
            con un mensaje accionable de "no encontrado".
    """
    result = copy.deepcopy(data)

    places = result.get(_PLACES) or []
    events = result.get(_EVENTS) or []

    # 1) Intentar eliminar un Place con ese id (Req 8.2).
    place_index = _find_index(places, id)
    if place_index is not None:
        del places[place_index]
        result[_PLACES] = places

        # Integridad referencial: limpiar el placeId colgante de los Events que
        # referenciaban al Place eliminado, informando cuáles fueron (Req 8.4, 8.5).
        affected: list[str] = []
        for event in events:
            if event.get("placeId") == id:
                affected.append(event.get("id"))
                # Quitar el campo placeId para no dejar una referencia colgante.
                event.pop("placeId", None)
        result[_EVENTS] = events

        return {"id": id, "affectedEvents": affected, "data": result}

    # 2) Intentar eliminar un Event con ese id (Req 8.1).
    event_index = _find_index(events, id)
    if event_index is not None:
        del events[event_index]
        result[_EVENTS] = events
        return {"id": id, "affectedEvents": [], "data": result}

    # 3) No existe ni como Place ni como Event -> error "no encontrado" (Req 8.3).
    raise ValueError(
        f"No se encontró ningún Place ni Event con id {id!r} en tourism-data; "
        f"verifica el identificador (consulta con query_content)."
    )


def _find_index(items: list[dict], id: str) -> int | None:
    """Devuelve el índice del primer elemento con ese `id`, o None si no existe."""
    for index, item in enumerate(items):
        if item.get("id") == id:
            return index
    return None
