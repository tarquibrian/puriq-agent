"""Helpers neutros de parseo/normalización de CSV compartidos por las tools.

Extraídos de ``scan_resources`` (DD-4 del spec content-management) para que tanto
``scan_resources`` como ``bulk_update`` compartan las MISMAS reglas de:

  - lectura de CSV (``_read_csv``: ``csv.DictReader`` con encoding ``utf-8-sig``),
  - separación de etiquetas (``_split_tags``: separadas por ``;``),
  - parseo de coordenadas (``_parse_coord``: error que nombra fila/columna ante
    valores no numéricos, Req 1.11),
  - normalización de una fila a Place/Event del contrato (``_place``/``_event``),
    con ids derivados en kebab-case vía ``slugify``.

Este módulo es neutro: no depende de ``scan_resources`` ni de ``bulk_update``;
ambas tools dependen de él y no una de la otra.
"""
from __future__ import annotations

import csv
from pathlib import Path

from puriq.tools._slug import slugify


def _split_tags(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(";") if t.strip()]


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f)]


def _parse_coord(raw: str, *, column: str, row_num: int) -> float:
    """Convierte un valor de coordenada a float.

    Si el valor no es numérico, relanza un error claro que identifica la fila
    del CSV (``row_num``, contando el encabezado como fila 1) y la columna
    inválida (``lat`` o ``lng``), en lugar del ``ValueError`` genérico de
    ``float()`` (Req 1.11).
    """
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(
            f"Valor no numérico en la columna '{column}' de places.csv "
            f"(fila {row_num}): {raw!r}"
        ) from exc


def _place(row: dict, row_num: int) -> dict:
    """Normaliza una fila de places.csv a un Place del contrato.

    ``row_num`` es el número de fila en el CSV (el encabezado es la fila 1),
    usado para reportar errores de coordenadas no numéricas (Req 1.11).
    """
    name = (row.get("name") or "").strip()
    place: dict = {
        "id": slugify(name),
        "name": name,
        "category": slugify(row.get("category") or ""),
        "shortDescription": (row.get("short_description") or "").strip(),
        "description": "",
        "images": [img] if (img := (row.get("image") or "").strip()) else [],
        "tags": _split_tags(row.get("tags", "")),
        "source": "user",
    }
    if (addr := (row.get("address") or "").strip()):
        place["address"] = addr
    if (hours := (row.get("hours") or "").strip()):
        place["hours"] = hours
    lat, lng = (row.get("lat") or "").strip(), (row.get("lng") or "").strip()
    if lat and lng:
        place["coords"] = {
            "lat": _parse_coord(lat, column="lat", row_num=row_num),
            "lng": _parse_coord(lng, column="lng", row_num=row_num),
        }
    return place


def _event(row: dict, place_ids: set[str]) -> dict:
    name = (row.get("name") or "").strip()
    event: dict = {
        "id": slugify(name),
        "name": name,
        "startDate": (row.get("start_date") or "").strip(),
        "description": (row.get("description") or "").strip(),
        "images": [],
        "recurring": (row.get("recurring") or "none").strip() or "none",
    }
    if (end := (row.get("end_date") or "").strip()):
        event["endDate"] = end
    ref = (row.get("place") or "").strip()
    if ref:
        pid = ref if ref in place_ids else slugify(ref)
        if pid in place_ids:
            event["placeId"] = pid
    return event
