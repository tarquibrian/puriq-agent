"""Resuelve los recursos que Puriq necesita en disco: la plantilla y los esquemas.

Puriq se usa de dos formas y los archivos no están en el mismo lugar en cada una:

  - **Instalado** (`pipx install puriq`): la plantilla Astro y los JSON Schema
    viajan dentro del paquete, en `puriq/_bundled/`. No hay repositorio alrededor.
  - **En el repositorio** (`pip install -e agent`): viven donde siempre,
    `template/` y `schemas/` en la raíz, y editarlos tiene que surtir efecto sin
    reinstalar nada.

Este módulo es el único lugar que conoce esa diferencia: busca primero la copia
empaquetada y cae a la del repositorio. Antes cada módulo contaba niveles de
`parents[...]` por su cuenta, lo que ataba el proyecto a estar clonado.
"""
from __future__ import annotations

from pathlib import Path

#: Raíz del paquete instalado (`.../site-packages/puriq` o `agent/puriq`).
_PAQUETE = Path(__file__).resolve().parent

#: Donde el build deja la plantilla y los esquemas al construir la rueda.
_EMPAQUETADO = _PAQUETE / "_bundled"

#: Raíz del repositorio cuando se trabaja sobre el clon (`agent/puriq` -> raíz).
_REPO = _PAQUETE.parents[1]


def _resolver(nombre: str) -> Path:
    """Devuelve el recurso `nombre`, priorizando la copia empaquetada.

    El orden importa: si existe la copia dentro del paquete es porque se instaló
    una rueda, y esa es la buena. La del repositorio sólo aplica en desarrollo.
    """
    empaquetado = _EMPAQUETADO / nombre
    if empaquetado.is_dir():
        return empaquetado
    return _REPO / nombre


def template_dir() -> Path:
    """Plantilla Astro que `build_site` clona para generar el sitio."""
    return _resolver("template")


def schema_dir() -> Path:
    """JSON Schema contra los que se valida el contrato."""
    return _resolver("schemas")


def examples_dir() -> Path:
    """Proyectos de ejemplo, para poder ver un sitio real sin cargar nada."""
    return _resolver("examples")
