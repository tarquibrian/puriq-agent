"""bulk_update: actualización masiva de Places/Events desde un CSV (Req 9, DD-4, DD-5).

Fusiona las filas de un CSV de Places o Events en un dict ``tourism-data`` ya
cargado en memoria, **por ``id``**:

- Fila cuyo ``id`` no existe todavía  -> se agrega un nuevo elemento (Req 9.1).
- Fila cuyo ``id`` coincide con un elemento existente -> **merge a nivel de campo**
  (``_merge.merge_fields``): solo se actualizan los campos presentes en la fila y
  se preservan intactos los campos ausentes (Req 9.2, DD-5). La misma regla de
  fusión por ``id`` aplica a Places y Events (Req 9.3).
- Fila sin ``id`` y sin ``name`` del que derivar un Slug -> se omite y se registra
  su número de fila en ``skipped`` (Req 9.4).
- Fila con un valor tipado inválido (``lat``/``lng`` no numérico, fecha mal
  formada) -> error que identifica número de fila y columna (Req 9.5), reutilizando
  ``_csv._parse_coord`` para las coordenadas.

Esta función es **pura**: hace copia profunda de ``data``, no muta la entrada y no
toca disco salvo la lectura del CSV de entrada. **No valida ni persiste** el
resultado: la validación-antes-de-escribir contra ``tourism-data`` y la escritura
atómica las realiza el llamador (``puriq.core`` con ``_persist.validate_then_write``,
task 13.1). Por eso devuelve el ``data`` fusionado junto con el resumen
``{added, updated, skipped, data}`` (Req 9.8).

## Resolución de id (nota de diseño)

Los helpers compartidos ``_csv._place``/``_csv._event`` derivan el ``id`` de un
elemento como ``slugify(name)`` y **no** leen una columna ``id``. Para soportar la
fusión por ``id`` sin alterar ese comportamiento estable (del que depende
``scan_resources``), ``bulk_update`` resuelve el ``id`` de cada fila así:

1. si la fila trae una columna ``id`` no vacía -> ``id = slugify(valor)`` (se usa
   el ``id`` explícito, normalizándolo para garantizar el patrón ``^[a-z0-9-]+$``
   del esquema; ``slugify`` es idempotente sobre un slug ya válido);
2. si no hay ``id`` explícito pero sí ``name`` -> ``id = slugify(name)``;
3. si no hay ni ``id`` ni ``name`` (o ambos slugifican a cadena vacía) -> la fila
   se omite y se registra su número (Req 9.4).

Además, en lugar de normalizar la fila completa con ``_csv._place``/``_csv._event``
(que rellenan valores por defecto para campos ausentes), ``bulk_update`` construye
un dict **disperso** que contiene **solo** los campos presentes en la fila. Esto es
lo que hace posible el merge que preserva los campos ausentes (Req 9.2).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path

from puriq.tools._csv import _parse_coord, _read_csv, _split_tags
from puriq.tools._merge import merge_fields
from puriq.tools._slug import slugify

_PLACES = "places"
_EVENTS = "events"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(raw: str, *, column: str, row_num: int) -> str:
    """Valida que ``raw`` sea una fecha ISO ``YYYY-MM-DD`` bien formada.

    Devuelve la cadena tal cual si es válida; si no, lanza un ``ValueError`` que
    identifica la fila del CSV (``row_num``, con el encabezado como fila 1) y la
    columna inválida (Req 9.5). Análogo a ``_csv._parse_coord`` pero para fechas.
    """
    if not _ISO_DATE.match(raw):
        raise ValueError(
            f"Fecha mal formada en la columna '{column}' del CSV "
            f"(fila {row_num}): {raw!r}. Se espera 'YYYY-MM-DD'."
        )
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"Fecha inválida en la columna '{column}' del CSV "
            f"(fila {row_num}): {raw!r}. Se espera 'YYYY-MM-DD'."
        ) from exc
    return raw


def _place_fields(row: dict, row_num: int) -> dict:
    """Extrae de una fila de places.csv **solo** los campos presentes (merge disperso).

    A diferencia de ``_csv._place``, no rellena valores por defecto para campos
    ausentes: así el merge por ``id`` preserva los campos que la fila no toca
    (Req 9.2). ``lat``/``lng`` no numéricos producen un error con fila/columna
    reutilizando ``_csv._parse_coord`` (Req 9.5).
    """
    fields: dict = {}

    name = (row.get("name") or "").strip()
    if name:
        fields["name"] = name

    if (category := (row.get("category") or "").strip()):
        fields["category"] = slugify(category)

    if (addr := (row.get("address") or "").strip()):
        fields["address"] = addr

    if (short := (row.get("short_description") or "").strip()):
        fields["shortDescription"] = short

    if (hours := (row.get("hours") or "").strip()):
        fields["hours"] = hours

    if "tags" in row and (raw_tags := (row.get("tags") or "").strip()):
        fields["tags"] = _split_tags(raw_tags)

    if (img := (row.get("image") or "").strip()):
        fields["images"] = [img]

    # Coordenadas: validar cada valor presente (Req 9.5) y componer coords solo si
    # están ambos, ya que el esquema exige lat y lng juntos.
    lat_raw = (row.get("lat") or "").strip()
    lng_raw = (row.get("lng") or "").strip()
    lat = _parse_coord(lat_raw, column="lat", row_num=row_num) if lat_raw else None
    lng = _parse_coord(lng_raw, column="lng", row_num=row_num) if lng_raw else None
    if lat is not None and lng is not None:
        fields["coords"] = {"lat": lat, "lng": lng}

    return fields


def _event_fields(row: dict, row_num: int, place_ids: set[str]) -> dict:
    """Extrae de una fila de events.csv **solo** los campos presentes (merge disperso).

    Las fechas (``start_date``/``end_date``) se validan como ISO ``YYYY-MM-DD``;
    un valor mal formado produce un error con fila/columna (Req 9.5). La columna
    ``place`` se resuelve a ``placeId`` contra los ids de Places conocidos (mismo
    criterio que ``_csv._event``).
    """
    fields: dict = {}

    name = (row.get("name") or "").strip()
    if name:
        fields["name"] = name

    if (start := (row.get("start_date") or "").strip()):
        fields["startDate"] = _parse_date(start, column="start_date", row_num=row_num)

    if (end := (row.get("end_date") or "").strip()):
        fields["endDate"] = _parse_date(end, column="end_date", row_num=row_num)

    if (desc := (row.get("description") or "").strip()):
        fields["description"] = desc

    if (recurring := (row.get("recurring") or "").strip()):
        fields["recurring"] = recurring

    ref = (row.get("place") or "").strip()
    if ref:
        pid = ref if ref in place_ids else slugify(ref)
        if pid in place_ids:
            fields["placeId"] = pid

    return fields


def bulk_update(data: dict, csv_path: Path, *, kind: str) -> dict:
    """Fusiona las filas de un CSV de Places/Events en ``data`` por ``id``.

    Args:
        data: dict conforme a ``tourism-data.schema.json`` (o subconjunto con las
            claves ``places``/``events``). No se muta; se opera sobre una copia
            profunda.
        csv_path: ruta al CSV de entrada (Places o Events según ``kind``).
        kind: ``"places"`` o ``"events"``.

    Returns:
        ``{"added": int, "updated": int, "skipped": list[int], "data": dict}``:
        - ``added``: filas con ``id`` nuevo agregadas (Req 9.1);
        - ``updated``: filas con ``id`` existente fusionadas (Req 9.2, 9.3);
        - ``skipped``: números de fila omitidas por no tener ``id`` ni ``name``
          (Req 9.4);
        - ``data``: el ``tourism-data`` fusionado (copia profunda de la entrada).
          El llamador (core) valida y persiste este documento (Req 9.6, 9.7).

    Raises:
        ValueError: si ``kind`` no es ``"places"`` ni ``"events"``, o si una fila
            tiene un valor tipado inválido (``lat``/``lng`` no numérico, fecha mal
            formada), identificando fila y columna (Req 9.5).
    """
    if kind not in (_PLACES, _EVENTS):
        raise ValueError(
            f"kind inválido: {kind!r}; debe ser {_PLACES!r} o {_EVENTS!r}"
        )

    result = copy.deepcopy(data)
    items: list[dict] = result.setdefault(kind, [])

    # Índice id -> posición en la lista, para reemplazar el elemento fusionado.
    pos_by_id: dict[str, int] = {
        it["id"]: i for i, it in enumerate(items) if it.get("id")
    }
    # Ids de Places conocidos, para resolver placeId de los Events (Req 9.3).
    place_ids: set[str] = {
        p["id"] for p in result.get("places", []) if p.get("id")
    }

    added = 0
    updated = 0
    skipped: list[int] = []

    # enumerate desde 2: la fila 1 del CSV es el encabezado. El número de fila se
    # conserva para reportar omisiones (Req 9.4) y errores de tipo (Req 9.5).
    for row_num, row in enumerate(_read_csv(csv_path), start=2):
        explicit_id = (row.get("id") or "").strip()
        name = (row.get("name") or "").strip()

        # Fila sin id y sin name del que derivar un slug -> se omite (Req 9.4).
        if not explicit_id and not name:
            skipped.append(row_num)
            continue

        resolved_id = slugify(explicit_id) if explicit_id else slugify(name)
        if not resolved_id:
            # id/name presentes pero slugifican a cadena vacía (p. ej. solo
            # símbolos): no hay slug del que derivar el id -> se omite (Req 9.4).
            skipped.append(row_num)
            continue

        if kind == _PLACES:
            fields = _place_fields(row, row_num)
        else:
            fields = _event_fields(row, row_num, place_ids)

        if resolved_id in pos_by_id:
            # id existente -> merge de solo los campos presentes (Req 9.2, DD-5).
            pos = pos_by_id[resolved_id]
            items[pos] = merge_fields(items[pos], fields)
            updated += 1
        else:
            # id nuevo -> alta de un nuevo elemento a partir de la fila (Req 9.1).
            new_item = dict(fields)
            new_item["id"] = resolved_id
            items.append(new_item)
            pos_by_id[resolved_id] = len(items) - 1
            if kind == _PLACES:
                place_ids.add(resolved_id)
            added += 1

    return {"added": added, "updated": updated, "skipped": skipped, "data": result}
