"""scan_resources: convierte los recursos crudos del gobierno en el contrato.

Lee una carpeta de recursos con:
  site.json    -> metadatos del sitio + categorias (obligatorio)
  places.csv   -> lugares turisticos (obligatorio)
  events.csv   -> eventos/festividades (opcional)

Produce un dict conforme a schemas/tourism-data.schema.json. NO llama al LLM ni
geocodifica: solo estructura y normaliza (ids en kebab-case, tipos correctos).
Las descripciones vacias las rellena luego generate_content; las coords faltantes,
geocode.

Formato de places.csv (encabezados):
  name,category,address,lat,lng,short_description,hours,tags,image
    - lat/lng: opcionales (si faltan, los completa geocode)
    - tags: separadas por ';'
    - image: ruta relativa a assets/ (opcional)

Formato de events.csv (encabezados):
  name,start_date,end_date,place,description,recurring
    - start_date/end_date: ISO YYYY-MM-DD
    - place: id o nombre de un lugar (se resuelve a placeId)
    - recurring: none|yearly|monthly|weekly (default none)
"""
from __future__ import annotations

import json
from pathlib import Path

# Helpers de parseo/normalización de CSV extraídos a un módulo neutro compartido
# (DD-4 del spec content-management). Se reexportan aquí para preservar la API
# pública histórica de este módulo (``scan_resources._read_csv``, etc.).
from puriq.tools._csv import (
    _event,
    _parse_coord,
    _place,
    _read_csv,
    _split_tags,
)

__all__ = [
    "run",
    "_event",
    "_parse_coord",
    "_place",
    "_read_csv",
    "_split_tags",
]


def run(resources_dir: Path) -> dict:
    """Lee site.json + places.csv (+ events.csv) y devuelve el dict tourism-data."""
    resources_dir = Path(resources_dir)

    site_file = resources_dir / "site.json"
    if not site_file.exists():
        raise FileNotFoundError(f"Falta site.json en {resources_dir}")
    site_doc = json.loads(site_file.read_text(encoding="utf-8"))

    places_file = resources_dir / "places.csv"
    if not places_file.exists():
        raise FileNotFoundError(f"Falta places.csv en {resources_dir}")
    # enumerate desde 2: la fila 1 del CSV es el encabezado. Se conserva el
    # número de fila real aunque se omitan filas con name vacío (Req 1.7),
    # para que los errores de coordenadas (Req 1.11) apunten a la fila correcta.
    places = [
        _place(r, row_num)
        for row_num, r in enumerate(_read_csv(places_file), start=2)
        if (r.get("name") or "").strip()
    ]
    place_ids = {p["id"] for p in places}

    events: list[dict] = []
    events_file = resources_dir / "events.csv"
    if events_file.exists():
        events = [_event(r, place_ids) for r in _read_csv(events_file) if (r.get("name") or "").strip()]

    return {
        "site": site_doc.get("site", {}),
        "categories": site_doc.get("categories", []),
        "places": places,
        "events": events,
    }
