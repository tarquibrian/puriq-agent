"""E/S de assets del intake, sin dependencia de FastAPI (DD-3).

Este módulo alberga los helpers de asset que hasta ahora vivían dentro de
``wizard/server.py`` (que importa FastAPI). Al aislarlos aquí, tanto el servidor
web del wizard como ``intake/tools.py`` pueden reutilizar **exactamente la misma
implementación** de asociación de imágenes sin acoplarse al servidor HTTP ni
levantar la app.

- ``next_available_asset(project, name)``: devuelve un nombre libre dentro de
  ``<project>/assets`` (desambiguando colisiones con sufijo numérico) y su ruta
  resuelta, verificando la contención con ``resolve_within_assets`` (Req 11.4).
- ``append_image(project, entity_key, entity_id, rel_path)``: anexa la ruta
  relativa a la lista ``images`` de un Place/Event por ``id`` vía
  load-merge-save, sin duplicar la ruta (Req 11.5).

La lógica se preserva idéntica a la de ``server.py``; este módulo solo la
reubica a un lugar neutral (sin FastAPI).
"""
from __future__ import annotations

from pathlib import Path

from puriq.wizard import contracts
from puriq.wizard.assets import resolve_within_assets

# Clave del documento de contenido turístico donde viven Places y Events.
_TOURISM_DOC = "tourism-data"


def next_available_asset(project: Path, name: str) -> tuple[str, Path]:
    """Devuelve un nombre libre dentro de `/assets` y su ruta resuelta (Req 4.6, 11.4).

    Verifica la contencion con `resolve_within_assets` (Req 12.4). Si `name` ya
    existe, desambigua anexando un sufijo numerico al *stem* (``slug-1.ext``,
    ``slug-2.ext``, ...) hasta hallar uno libre, de modo que los Assets previos
    nunca se sobreescriben (Req 11.4).
    """
    target = resolve_within_assets(project, name)
    if not target.exists():
        return name, target

    stem, _, ext = name.rpartition(".")
    ext = f".{ext}"
    counter = 1
    while True:
        candidate = f"{stem}-{counter}{ext}"
        candidate_path = resolve_within_assets(project, candidate)
        if not candidate_path.exists():
            return candidate, candidate_path
        counter += 1


def append_image(project: Path, entity_key: str, entity_id: str, rel_path: str) -> dict:
    """Anexa `rel_path` a `images` de un Place/Event por `id` via load-merge-save (Req 4.2).

    Carga `tourism-data`, ubica la entidad (`places`/`events`) por `id`, calcula
    la nueva lista de `images` (sin duplicar la ruta) y persiste el parche con
    `merge_document`+`save_contract`. Si la entidad no existe, lanza `ValueError`
    accionable (el llamador lo mapea a `422`) en vez de crear una entidad
    incompleta que no cumpliria el esquema.
    """
    base = contracts._load_contract(project, _TOURISM_DOC)
    entidades = base.get(entity_key) or []
    actual = next((e for e in entidades if e.get("id") == entity_id), None)
    if actual is None:
        raise ValueError(
            f"No existe un {entity_key[:-1]} con id '{entity_id}' para asociar la "
            f"imagen. Crea la entrada antes de subir su imagen."
        )
    images = list(actual.get("images") or [])
    if rel_path not in images:
        images.append(rel_path)
    patch = {entity_key: [{"id": entity_id, "images": images}]}
    merged = contracts.merge_document(base, patch)
    contracts.save_contract(project, _TOURISM_DOC, merged)
    return merged
