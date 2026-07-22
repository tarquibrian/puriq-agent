"""Constructor puro de `Site_Config.modules` (Tarea 4.1, DD-1).

Este modulo NO hace E/S ni valida contra el esquema: solo transforma la
seleccion de modulos elegida por el usuario en el sub-documento
`Site_Config.modules` con la forma que espera `schemas/site-config.schema.json`
(cada modulo con `enabled` y `order`; `chatweb` admite ademas `persona` y
`knowledgeSource`). Al ser puro es apto para property-based testing (Property 8).

La escritura real y la validacion contra el esquema las hace la capa de
contrato (`save_contract` -> `schemas.validate`) en el endpoint `PUT
/api/site-config` (Tarea 9.3). Aqui solo se construye la estructura y se
restringe al catalogo soportado.

Requisitos: 2.1 (escribir `enabled`), 2.2 (`order` entero >= 1 consistente con
el orden elegido), 2.3 (restringir al catalogo soportado).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Catalogo soportado (Req 2.3). El orden aqui es solo el orden de referencia del
# catalogo; el `order` efectivo lo define la seleccion del usuario.
MODULE_CATALOG: tuple[str, ...] = ("map", "places", "events", "blog", "chatweb")

# Modulo que admite campos extra (persona / knowledgeSource) segun el esquema.
CHATWEB = "chatweb"

# Campos extra permitidos unicamente en el modulo chatweb.
_CHATWEB_EXTRA_FIELDS: tuple[str, ...] = ("persona", "knowledgeSource")


class ModuleCatalogError(ValueError):
    """Error accionable: la seleccion contiene una clave fuera del catalogo,
    un modulo repetido, o un valor de `enabled`/`order`/extras invalido.

    El mensaje nombra el problema y, cuando aplica, lista el catalogo soportado
    para que el Wizard_UI pueda mostrar una correccion (Req 2.3, 7.3).
    """


def _catalog_hint() -> str:
    return ", ".join(MODULE_CATALOG)


def build_modules(selection: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Construye `Site_Config.modules` de forma pura a partir de una seleccion ordenada.

    `selection` es una secuencia **ordenada** de descriptores de modulo; el orden
    de la secuencia define el `order` asignado a cada modulo (1-based), de modo
    que el resultado es siempre consistente con el orden elegido (Req 2.2).

    Cada descriptor es un mapping con:
      - ``key`` (str, requerido): clave del modulo, debe pertenecer a
        ``MODULE_CATALOG`` (Req 2.3).
      - ``enabled`` (bool, opcional, default ``True``): estado on/off (Req 2.1).
      - ``persona`` / ``knowledgeSource`` (str, opcionales): solo validos para
        ``chatweb``; se copian al modulo si estan presentes.

    Devuelve un dict ``{key: {"enabled": bool, "order": int, ...}}`` donde cada
    ``order`` es un entero >= 1 (Req 2.2). La funcion es pura: no lee ni escribe
    disco y no muta la entrada.

    Lanza ``ModuleCatalogError`` si:
      - un descriptor no trae ``key`` o esta fuera del catalogo,
      - una ``key`` se repite en la seleccion,
      - ``enabled`` no es booleano,
      - se pasan campos extra (``persona``/``knowledgeSource``) a un modulo que
        no es ``chatweb`` o con un valor no-string.

    Ejemplo::

        build_modules([
            {"key": "places", "enabled": True},
            {"key": "map", "enabled": True},
            {"key": "chatweb", "enabled": False, "persona": "amable"},
        ])
        # -> {
        #   "places": {"enabled": True, "order": 1},
        #   "map": {"enabled": True, "order": 2},
        #   "chatweb": {"enabled": False, "order": 3, "persona": "amable"},
        # }
    """
    modules: dict[str, dict[str, Any]] = {}

    for position, descriptor in enumerate(selection, start=1):
        if not isinstance(descriptor, Mapping):
            raise ModuleCatalogError(
                "Cada modulo debe describirse con un objeto que incluya 'key'; "
                f"catalogo soportado: {_catalog_hint()}"
            )

        key = descriptor.get("key")
        if not isinstance(key, str) or not key:
            raise ModuleCatalogError(
                "Falta la clave 'key' del modulo; "
                f"catalogo soportado: {_catalog_hint()}"
            )

        # Req 2.3: restringir al catalogo soportado.
        if key not in MODULE_CATALOG:
            raise ModuleCatalogError(
                f"Modulo no soportado: '{key}'. "
                f"Catalogo soportado: {_catalog_hint()}"
            )

        if key in modules:
            raise ModuleCatalogError(
                f"Modulo repetido en la seleccion: '{key}'"
            )

        # Req 2.1: escribir `enabled` (bool). En Python bool es subclase de int,
        # por eso se comprueba el tipo exacto para rechazar 0/1 u otros valores.
        enabled = descriptor.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ModuleCatalogError(
                f"El campo 'enabled' de '{key}' debe ser booleano (true/false)"
            )

        # Req 2.2: `order` entero >= 1 consistente con el orden de la seleccion.
        module: dict[str, Any] = {"enabled": enabled, "order": position}

        # Campos extra: solo validos en chatweb; se copian si vienen presentes.
        for field in _CHATWEB_EXTRA_FIELDS:
            if field in descriptor:
                if key != CHATWEB:
                    raise ModuleCatalogError(
                        f"El campo '{field}' solo es valido para el modulo "
                        f"'{CHATWEB}', no para '{key}'"
                    )
                value = descriptor[field]
                if not isinstance(value, str):
                    raise ModuleCatalogError(
                        f"El campo '{field}' de '{CHATWEB}' debe ser texto"
                    )
                module[field] = value

        modules[key] = module

    return modules
