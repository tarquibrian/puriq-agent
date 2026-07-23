"""Constructor puro de `Site_Config.landing` (Tarea 11.1, DD-6).

Este modulo NO hace E/S ni valida contra el esquema: solo transforma la
seleccion **ordenada** de secciones de portada elegida por el usuario en el
sub-documento `Site_Config.landing` con la forma que espera
`schemas/site-config.schema.json` (cada seccion con `type`, `enabled`, `order`
y, opcionalmente, `content`). Al ser puro es apto para property-based testing
(Property 11).

La escritura real y la validacion contra el esquema las hace la capa de
contrato (`contracts.merge_document` -> `save_contract` -> `schemas.validate`)
en el endpoint `PUT /api/site-config` (Tarea 11.2). Aqui solo se construye la
estructura, se asigna el `order` segun la posicion y se restringe `type` al
catalogo soportado.

Requisitos: 14.2 (`order` que refleja el orden elegido), 10.4 (restringir
`type` al catalogo soportado del Landing_Module).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Catalogo soportado de tipos de Landing_Section (Req 10.4). El orden aqui es
# solo el orden de referencia del catalogo; el `order` efectivo de cada seccion
# lo define su posicion en la seleccion del usuario.
LANDING_CATALOG: tuple[str, ...] = ("hero", "features", "cta", "gallery", "stats")


class LandingCatalogError(ValueError):
    """Error accionable: la seleccion contiene una seccion sin `type`, con un
    `type` fuera del catalogo, o con un valor de `enabled`/`content` invalido.

    El mensaje nombra el problema y, cuando aplica, lista el catalogo soportado
    para que el Wizard_UI pueda mostrar una correccion (Req 10.4, 14.4).
    """


def _catalog_hint() -> str:
    return ", ".join(LANDING_CATALOG)


def build_landing(selection: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Construye `Site_Config.landing` de forma pura a partir de una seleccion ordenada.

    `selection` es una secuencia **ordenada** de descriptores de seccion; el
    orden de la secuencia define el `order` asignado a cada seccion (1-based),
    de modo que el resultado es siempre consistente con el orden elegido por el
    usuario en el Wizard (Req 14.2).

    Cada descriptor es un mapping con:
      - ``type`` (str, requerido): tipo de la seccion, debe pertenecer a
        ``LANDING_CATALOG`` (Req 10.4).
      - ``enabled`` (bool, opcional, default ``True``): estado on/off.
      - ``content`` (mapping, opcional): campos de copy por tipo; se conserva
        tal cual (no se fabrica ni se descarta copy). Si no se provee, el item
        se emite sin la clave ``content``.

    Devuelve una lista de items ``{"type": str, "enabled": bool, "order": int,
    "content": {...}}`` donde cada ``order`` es un entero >= 1 estrictamente
    creciente y coherente con la posicion (Req 14.2). La funcion es pura: no lee
    ni escribe disco y no muta la entrada.

    Lanza ``LandingCatalogError`` si:
      - un descriptor no es un mapping o no trae ``type``,
      - un ``type`` esta fuera del catalogo,
      - ``enabled`` no es booleano,
      - ``content`` esta presente pero no es un mapping.

    Ejemplo::

        build_landing([
            {"type": "hero", "enabled": True, "content": {"headline": "Potosi"}},
            {"type": "features", "enabled": True},
            {"type": "cta", "enabled": False, "content": {"message": ""}},
        ])
        # -> [
        #   {"type": "hero", "enabled": True, "order": 1,
        #    "content": {"headline": "Potosi"}},
        #   {"type": "features", "enabled": True, "order": 2},
        #   {"type": "cta", "enabled": False, "order": 3, "content": {"message": ""}},
        # ]
    """
    landing: list[dict[str, Any]] = []

    for position, descriptor in enumerate(selection, start=1):
        if not isinstance(descriptor, Mapping):
            raise LandingCatalogError(
                "Cada seccion debe describirse con un objeto que incluya 'type'; "
                f"catalogo soportado: {_catalog_hint()}"
            )

        section_type = descriptor.get("type")
        if not isinstance(section_type, str) or not section_type:
            raise LandingCatalogError(
                "Falta la clave 'type' de la seccion; "
                f"catalogo soportado: {_catalog_hint()}"
            )

        # Req 10.4: restringir al catalogo soportado.
        if section_type not in LANDING_CATALOG:
            raise LandingCatalogError(
                f"Tipo de seccion no soportado: '{section_type}'. "
                f"Catalogo soportado: {_catalog_hint()}"
            )

        # `enabled` (bool, default True). En Python bool es subclase de int, por
        # eso se comprueba el tipo exacto para rechazar 0/1 u otros valores.
        enabled = descriptor.get("enabled", True)
        if not isinstance(enabled, bool):
            raise LandingCatalogError(
                f"El campo 'enabled' de la seccion '{section_type}' debe ser "
                "booleano (true/false)"
            )

        # Req 14.2: `order` entero >= 1 estrictamente creciente y coherente con
        # la posicion en la seleccion.
        section: dict[str, Any] = {
            "type": section_type,
            "enabled": enabled,
            "order": position,
        }

        # `content` opcional: se conserva tal cual si viene presente (no se
        # fabrica ni se descarta copy). Se copia a un dict propio para no mutar
        # la entrada ni compartir referencias con el descriptor original.
        if "content" in descriptor:
            content = descriptor["content"]
            if not isinstance(content, Mapping):
                raise LandingCatalogError(
                    f"El campo 'content' de la seccion '{section_type}' debe ser "
                    "un objeto"
                )
            section["content"] = dict(content)

        landing.append(section)

    return landing
