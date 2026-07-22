"""query_content: consulta de solo lectura sobre el contrato tourism-data (Req 6).

Filtra Places o Events de un dict ``tourism-data`` ya cargado en memoria. Es una
función **pura**: no lee ni escribe disco y no muta la entrada; opera únicamente
sobre el ``data`` recibido, devolviendo una lista con los elementos que satisfacen
todos los filtros indicados (conjunción). Sin filtros devuelve todos los elementos
del tipo consultado; sin coincidencias devuelve una lista vacía (nunca lanza error).

Las fechas son cadenas ISO ``YYYY-MM-DD`` (Event.startDate, ``format: date`` en el
esquema). El rango de fechas se compara **lexicográficamente**: como el formato
ISO ``YYYY-MM-DD`` tiene ancho fijo y ordena de forma cronológica bajo comparación
de cadenas, ``date_from <= startDate <= date_to`` (inclusive en ambos extremos)
es equivalente a la comparación por fecha, sin necesidad de parsear.
"""
from __future__ import annotations

_PLACES = "places"
_EVENTS = "events"


def query(
    data: dict,
    *,
    kind: str,
    category: str | None = None,
    tag: str | None = None,
    name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Filtra Places o Events de ``data`` (tourism-data). Solo lectura.

    Args:
        data: dict conforme a ``tourism-data.schema.json`` (o subconjunto con las
            claves ``places``/``events``).
        kind: ``"places"`` o ``"events"``, el tipo de elemento a consultar.
        category: si se indica, solo Places cuyo ``category`` sea exactamente igual
            (Req 6.3).
        tag: si se indica, solo Places cuyo array ``tags`` contenga esa etiqueta
            (Req 6.4).
        name: si se indica, solo elementos cuyo ``name`` contenga el texto, sin
            distinguir mayúsculas/minúsculas (Req 6.5).
        date_from: extremo inferior (inclusive) del rango de ``startDate`` para
            Events, ISO ``YYYY-MM-DD`` (Req 6.6).
        date_to: extremo superior (inclusive) del rango de ``startDate`` para
            Events, ISO ``YYYY-MM-DD`` (Req 6.6).

    Returns:
        Lista (posiblemente vacía) de los elementos del tipo ``kind`` que cumplen
        todos los filtros. Los elementos se devuelven tal cual aparecen en
        ``data`` (mismas referencias; la función no los copia ni los muta).

    Raises:
        ValueError: si ``kind`` no es ``"places"`` ni ``"events"``.
    """
    if kind not in (_PLACES, _EVENTS):
        raise ValueError(
            f"kind inválido: {kind!r}; debe ser {_PLACES!r} o {_EVENTS!r}"
        )

    items = list(data.get(kind) or [])
    needle = name.lower() if name is not None else None

    result: list[dict] = []
    for item in items:
        # Búsqueda por nombre: 'name' contiene el texto, case-insensitive (Req 6.5).
        # Aplica a Places y Events.
        if needle is not None:
            if needle not in str(item.get("name") or "").lower():
                continue

        # Filtro por categoría: igualdad exacta (Places, Req 6.3).
        if category is not None:
            if item.get("category") != category:
                continue

        # Filtro por etiqueta: 'tags' contiene la etiqueta (Places, Req 6.4).
        if tag is not None:
            if tag not in (item.get("tags") or []):
                continue

        # Rango de fechas sobre startDate (Events, inclusive en ambos extremos,
        # Req 6.6). Comparación lexicográfica válida para ISO YYYY-MM-DD.
        start = item.get("startDate")
        if date_from is not None:
            if start is None or start < date_from:
                continue
        if date_to is not None:
            if start is None or start > date_to:
                continue

        result.append(item)

    return result
