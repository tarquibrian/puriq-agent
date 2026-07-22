"""Escritura atómica validada del contrato (DD-6).

Helper transversal que implementa la invariante DD-6 del diseño: **toda mutación
del contrato se valida contra su esquema ANTES de tocar disco**; si la validación
falla, no se escribe nada (rechazo sin escritura parcial) y se reporta el campo
que incumple; en caso de éxito la escritura es **atómica** (archivo temporal en
el mismo directorio + `os.replace`) para que un fallo a mitad de escritura no
deje el archivo corrupto (Req 12.1, 12.3).

Es la pieza de E/S que reutilizan `edit_content`, `delete_content`,
`bulk_update` y `manage_articles`. Generaliza el patrón ya presente en
`puriq.wizard.contracts.save_contract` (validar → escribir atómico → nombrar el
campo infractor) para que sirva tanto a:

- **documentos del contrato JSON** (Tourism_Data): el `doc` validado se serializa
  con `puriq.schemas.dumps` (comportamiento por defecto), y
- **artículos markdown**: el `doc` validado es el Article_Frontmatter, pero lo
  que se escribe a disco es el markdown completo (frontmatter + cuerpo); en ese
  caso el llamador aporta el texto ya serializado vía `text=...` (o una función
  `serialize` que reciba el frontmatter y devuelva el markdown completo).
"""
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from typing import Callable

import jsonschema

from puriq import schemas


def _field_from_validation_error(exc: jsonschema.ValidationError) -> str:
    """Deriva una ruta legible del campo infractor a partir del error de jsonschema.

    Usa `absolute_path` (la ruta del nodo que incumple dentro del documento). Si
    está vacía —p. ej. una clave requerida ausente a nivel raíz— cae en el
    mensaje del propio error, que nombra la clave faltante.
    """
    path = list(exc.absolute_path)
    if not path:
        return exc.message
    return ".".join(str(p) for p in path)


def validate_then_write(
    doc: dict,
    schema_name: str,
    path: Path | str,
    *,
    serialize: Callable[[dict], str] | None = None,
    text: str | None = None,
) -> Path:
    """Valida `doc` contra su esquema y **solo entonces** escribe de forma atómica.

    Orden garantizado (DD-6, Req 12.1, 12.3):
    1. Valida `doc` contra `schema_name` con `puriq.schemas.validate` **antes** de
       tocar disco. Si falla, **no se escribe nada** y se lanza `ValueError`
       nombrando el documento y el campo que incumple (derivado de
       `jsonschema.ValidationError.absolute_path`).
    2. Solo si la validación pasa, resuelve el texto a escribir y lo persiste de
       forma atómica: escribe a un archivo temporal en el **mismo directorio** de
       destino y luego hace `os.replace` (rename atómico dentro del mismo sistema
       de archivos). Si algo falla tras crear el temporal, lo borra y no toca el
       destino.

    Resolución del payload a escribir (exactamente una fuente):
    - `text` no nulo  -> se escribe `text` tal cual (caso markdown: el frontmatter
      `doc` es lo validado, pero a disco va el markdown completo ya serializado).
    - `serialize` dado -> se escribe `serialize(doc)` (serializador a medida).
    - ninguno de los dos -> se escribe `schemas.dumps(doc)` (caso JSON del
      contrato, comportamiento por defecto).
    `text` y `serialize` son mutuamente excluyentes.

    Args:
        doc: El documento a validar (dict). Para JSON del contrato es el propio
            contenido a escribir; para artículos es el Article_Frontmatter.
        schema_name: Clave del esquema en `puriq.schemas` (p. ej. `"tourism-data"`,
            `"article"`).
        path: Ruta de destino del archivo.
        serialize: Serializador opcional `doc -> str`. Por defecto
            `schemas.dumps`.
        text: Texto ya serializado a escribir tal cual (alternativa a
            `serialize`).

    Returns:
        La ruta de destino (`Path`) donde se escribió el archivo.

    Raises:
        ValueError: si `doc` no cumple el esquema (no se escribe nada), o si se
            pasan `text` y `serialize` a la vez.
    """
    if text is not None and serialize is not None:
        raise ValueError(
            "validate_then_write: 'text' y 'serialize' son mutuamente excluyentes."
        )

    dest = Path(path)

    # 1) Validar ANTES de tocar disco (Req 12.1). Si falla, no se escribe nada.
    try:
        schemas.validate(doc, schema_name)
    except jsonschema.ValidationError as exc:
        field = _field_from_validation_error(exc)
        raise ValueError(
            f"Documento '{schema_name}' inválido en el campo '{field}': "
            f"{exc.message}. No se escribió nada."
        ) from exc

    # 2) Resolver el texto a escribir.
    if text is not None:
        payload = text
    elif serialize is not None:
        payload = serialize(doc)
    else:
        payload = schemas.dumps(doc)

    # 3) Escritura atómica: temporal en el mismo directorio + os.replace.
    _atomic_write(dest, payload)
    return dest


def _atomic_write(dest: Path, payload: str) -> None:
    """Escribe `payload` en `dest` de forma atómica (temporal + os.replace).

    Escribe primero a un archivo temporal en el **mismo directorio** de destino y
    luego hace `os.replace` (rename atómico dentro del mismo sistema de archivos),
    de modo que un fallo a mitad de escritura no deje el archivo corrupto. Si algo
    falla tras crear el temporal, lo borra y no toca el destino.
    """
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, dest)
    except BaseException:
        # Si algo falla tras crear el temporal, no dejar basura ni tocar el destino.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _relaxed_tourism_schema() -> dict:
    """Esquema de `tourism-data` con `coords` **opcional** en cada Place (DD-1).

    Al persistir un `tourism-data` mutado por `edit_content`/`delete_content`/
    `bulk_update`, un Place puede tener legítimamente solo `address` y todavía
    **sin** `coords`: `geocode` completará las coordenadas después, durante el
    build (mismo criterio que el flujo del wizard y de `core.build`). Por eso la
    validación previa a escribir un **draft** NO debe exigir `coords` en cada
    Place. Todas las demás reglas estructurales se conservan intactas (requeridos
    `id`/`name`/`category`, patrón de `id`, tipos, `additionalProperties`, rangos
    de `lat`/`lng` cuando hay `coords`, campos de evento, etc.).

    Se parte de una copia profunda del esquema en disco (`schemas.get_schema` ya
    devuelve una instancia fresca; el `deepcopy` es defensa explícita) y solo se
    elimina `"coords"` de la lista `required` del `$defs.place`. No se muta el
    esquema en disco ni ningún estado compartido.
    """
    schema = copy.deepcopy(schemas.get_schema("tourism-data"))
    place = schema.get("$defs", {}).get("place", {})
    required = place.get("required")
    if isinstance(required, list):
        place["required"] = [key for key in required if key != "coords"]
    return schema


def save_tourism_draft(data: dict, path: Path | str) -> Path:
    """Valida `data` como **draft** de tourism-data y **solo entonces** lo escribe.

    Igual que `validate_then_write` (validar-antes-de-escribir + escritura
    atómica, DD-6, Req 12.1, 12.3), pero valida contra `_relaxed_tourism_schema()`:
    un Place puede quedar con solo `address` y sin `coords` (un draft válido; las
    coords las completará `geocode` en el build). Todo el resto de la estructura
    se sigue exigiendo. Si la validación falla, **no se escribe nada** y se lanza
    un `ValueError` que nombra el campo infractor.

    Este helper existe para que `puriq.core` persista los tourism-data mutados por
    las tools de contenido **sin** depender del paquete `wizard` (que tiene su
    propia relajación equivalente para el flujo del wizard).

    Args:
        data: `tourism-data` mutado a persistir como draft.
        path: ruta de destino de `tourism-data.json`.

    Returns:
        La ruta de destino (`Path`) donde se escribió el archivo.

    Raises:
        ValueError: si `data` no cumple el esquema relajado (no se escribe nada).
    """
    dest = Path(path)
    try:
        jsonschema.validate(instance=data, schema=_relaxed_tourism_schema())
    except jsonschema.ValidationError as exc:
        field = _field_from_validation_error(exc)
        raise ValueError(
            f"Documento 'tourism-data' inválido en el campo '{field}': "
            f"{exc.message}. No se escribió nada."
        ) from exc

    _atomic_write(dest, schemas.dumps(data))
    return dest
