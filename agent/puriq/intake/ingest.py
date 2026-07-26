"""intake/ingest.py: Ingest_Router + PDF_Extractor (Pieza 5, Hitos 3-4).

Router de ingesta multimodal por tipo de archivo (Imagen / PDF / no soportado) y
extractor de texto de PDF **en memoria**. Este módulo es deliberadamente delgado y
no escribe el contrato: clasifica y valida los Archivos_Entrantes de un turno y
produce los artefactos que el `Chat_Agent` usa (bloques de imagen para la visión,
texto extraído de PDF como contexto, y mensajes accionables de rechazo). Toda
escritura del contrato se delega en las intake tools existentes vía
`run_intake_tool` (DD-M1, Req 1.5).

Frontera de imports (para evitar ciclos): este módulo importa SOLO de
`puriq.wizard.assets` (`IMAGE_EXTS`, `MAX_ASSET_BYTES`, `normalize_asset_name`) y
`puriq.config` (redacción). NO importa `intake/tools.py` porque, al revés,
`tools.py` importará `extract_pdf_text` desde aquí (la Extract_PDF_Tool comparte el
PDF_Extractor). La PDF_Library (`pypdf`) se importa de forma **diferida** dentro de
`extract_pdf_text` para que el resto del módulo (clasificación, ingesta de
imágenes) no dependa del extra opcional (Req 9.4, DD-M6).
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath

from puriq import config
from puriq.wizard.assets import (
    IMAGE_EXTS,
    MAX_ASSET_BYTES,
    normalize_asset_name,
)


class FileKind(Enum):
    """Tipo de un Archivo_Entrante determinado por su extensión (Req 1.1)."""

    IMAGE = "image"
    PDF = "pdf"
    UNSUPPORTED = "unsupported"


@dataclass
class IncomingFile:
    """Un Archivo_Entrante del turno: nombre + bytes ya decodificados + media type.

    Los bytes vienen **decodificados** (el endpoint decodifica el multipart; el MCP
    decodifica el base64 en el handler de `extract_pdf`), de modo que el tamaño se
    valida sobre ellos (Req 10.5). `media_type` se deriva de la extensión si falta.
    """

    filename: str
    content: bytes
    media_type: str | None = None


@dataclass
class ImageBlock:
    """Bloque de imagen para el modelo de mensajes neutral (DD-M2)."""

    media_type: str  # p. ej. "image/jpeg"
    data: str  # bytes de la imagen en base64


@dataclass
class IngestResult:
    """Resultado de preparar los binarios de un turno para el Chat_Agent."""

    image_blocks: list[ImageBlock] = field(default_factory=list)
    asset_binaries: dict[str, bytes] = field(default_factory=dict)
    pdf_texts: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


#: Límite de tamaño de un PDF entrante (Req 10.4). En MiB para el mensaje de rechazo.
MAX_PDF_BYTES = 20 * 1024 * 1024

#: Media types de imagen que un modelo de visión acepta como raster (DD-M7).
#: SVG y AVIF quedan fuera a propósito: no son formatos raster que los modelos de
#: visión soportados acepten, por lo que se guardan como asset pero no se envían.
_VISION_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

#: Nombre del extra opcional a instalar si falta la PDF_Library (Req 9.4).
_PDF_EXTRA = "pdf"


def _suffix(filename: str) -> str:
    """Extensión (en minúsculas, con punto) del último segmento de `filename`."""
    # Descartar componentes de directorio para clasificar solo por el nombre base,
    # de forma robusta ante separadores POSIX y Windows.
    base = filename.replace("\\", "/")
    return PurePosixPath(base).suffix.lower()


def _supported_types_message() -> str:
    """Mensaje accionable que lista los tipos de archivo soportados (Req 1.4)."""
    imagenes = ", ".join(sorted(IMAGE_EXTS))
    return (
        "Tipo de archivo no soportado. Puriq acepta imágenes "
        f"({imagenes}) y PDF (.pdf). Adjuntá un archivo de esos tipos."
    )


def classify_file(filename: str) -> FileKind:
    """Clasifica un Archivo_Entrante por su extensión (Req 1.1, DD-M1).

    Devuelve `IMAGE` si la extensión (en minúsculas) pertenece a `IMAGE_EXTS`,
    `PDF` si es `.pdf`, y `UNSUPPORTED` en cualquier otro caso. Función pura sobre
    el nombre: no lee ni escribe nada.
    """
    suffix = _suffix(filename)
    if suffix in IMAGE_EXTS:
        return FileKind.IMAGE
    if suffix == ".pdf":
        return FileKind.PDF
    return FileKind.UNSUPPORTED


def extract_pdf_text(data: bytes) -> str:
    """Extrae el Texto_Extraido de un PDF en memoria con la PDF_Library (Req 3.1, 9.3).

    Usa `pypdf.PdfReader(io.BytesIO(data))` y concatena `page.extract_text()` de
    cada página. El import de `pypdf` es **diferido**: si el extra no está
    instalado, se lanza un error que NOMBRA el extra a instalar
    (`pip install puriq[pdf]`, Req 9.4). Si el PDF no contiene texto legible
    (p. ej. escaneado), se lanza un `ValueError` accionable que indica que no se
    pudo extraer texto y sugiere una acción (Req 3.6). El binario se procesa solo
    en memoria; nunca se persiste (Req 11.5, 3.5).

    Raises:
        ModuleNotFoundError: si la PDF_Library no está instalada; el mensaje nombra
            el extra a instalar.
        ValueError: si el PDF no contiene texto legible.
    """
    try:
        from pypdf import PdfReader  # import diferido (Req 9.4, DD-M6)
    except ModuleNotFoundError as exc:  # pragma: no cover - depende del entorno
        raise ModuleNotFoundError(
            "La extracción de PDF requiere la biblioteca 'pypdf', que no está "
            f"instalada. Instalá el extra con: pip install puriq[{_PDF_EXTRA}]."
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    partes: list[str] = []
    for page in reader.pages:
        texto = page.extract_text() or ""
        if texto:
            partes.append(texto)

    resultado = "\n".join(partes).strip()
    if not resultado:
        raise ValueError(
            "No se pudo extraer texto legible del PDF (podría ser un documento "
            "escaneado o solo de imágenes). Probá con un PDF con texto "
            "seleccionable, o describí su contenido en el chat."
        )
    return resultado


def prepare_incoming(
    files: list[IncomingFile], *, supports_vision: bool
) -> IngestResult:
    """Enruta y valida los binarios del turno (Req 1.1-1.4, 2.2, 3, 10, DD-M1/5/7/9).

    Por cada archivo, según `classify_file`:
      - UNSUPPORTED -> agrega un mensaje accionable a `rejected` que lista los tipos
        soportados (Req 1.4); no produce efectos.
      - IMAGE -> valida la extensión con `normalize_asset_name(filename, IMAGE_EXTS)`
        (Req 10.1, 10.2) y el tamaño contra `MAX_ASSET_BYTES` sobre los bytes
        decodificados (Req 10.3, 10.5); si es válida, registra los bytes en
        `asset_binaries[nombre_normalizado]` (para la inyección de DD-M4) y, si
        `supports_vision` y la extensión tiene media type raster en
        `_VISION_MEDIA_TYPES`, agrega un `ImageBlock` (base64 + media type) a
        `image_blocks` (Req 2.2).
      - PDF -> valida el tamaño contra `MAX_PDF_BYTES` sobre los bytes decodificados
        (Req 10.4, 10.5); si es válido, extrae el texto con `extract_pdf_text` EN
        MEMORIA y agrega `config.redact_value(texto)` a `pdf_texts` (Req 3.1, 3.2,
        11.2, 11.5). Un PDF sin texto o un error de extracción produce un mensaje
        accionable en `rejected` sin abortar el turno (Req 3.6).

    NO escribe el contrato ni assets: eso lo hace el Chat_Agent vía las intake tools
    (Req 1.5). Un archivo inválido no aborta el turno: se acumula en `rejected`.
    """
    result = IngestResult()

    for incoming in files:
        kind = classify_file(incoming.filename)

        if kind is FileKind.UNSUPPORTED:
            result.rejected.append(_supported_types_message())
            continue

        if kind is FileKind.IMAGE:
            _prepare_image(incoming, supports_vision=supports_vision, result=result)
            continue

        # kind is FileKind.PDF
        _prepare_pdf(incoming, result=result)

    return result


def _prepare_image(
    incoming: IncomingFile, *, supports_vision: bool, result: IngestResult
) -> None:
    """Valida y registra una Imagen del turno (Req 2.2, 10.1-10.3, 10.5, DD-M4/M7)."""
    # 1) Validar la extensión reutilizando la normalización del núcleo (Req 10.1, 10.2).
    try:
        normalized = normalize_asset_name(incoming.filename, IMAGE_EXTS)
    except ValueError as exc:
        result.rejected.append(str(exc))
        return

    # 2) Validar el tamaño sobre bytes decodificados ANTES de cualquier efecto
    #    (Req 10.3, 10.5, DD-M9).
    if len(incoming.content) > MAX_ASSET_BYTES:
        limite_mib = MAX_ASSET_BYTES // (1024 * 1024)
        result.rejected.append(
            f"La imagen '{incoming.filename}' excede el tamaño máximo permitido "
            f"({limite_mib} MiB). Reducí su tamaño e intentá de nuevo."
        )
        return

    # 3) Registrar los bytes por nombre normalizado para la inyección del agente
    #    (DD-M4). El guardado real lo hace attach_asset vía run_intake_tool (Req 1.5).
    result.asset_binaries[normalized] = incoming.content

    # 4) Si hay visión y el formato es raster soportado, adjuntar un bloque de
    #    imagen para complete_chat (Req 2.2, DD-M7). Sin visión, la imagen NO se
    #    envía al modelo (se guarda igual como asset y el agente lo informa).
    if supports_vision:
        suffix = _suffix(normalized)
        media_type = _VISION_MEDIA_TYPES.get(suffix)
        if media_type is not None:
            data_b64 = base64.b64encode(incoming.content).decode("ascii")
            result.image_blocks.append(
                ImageBlock(media_type=media_type, data=data_b64)
            )


def _prepare_pdf(incoming: IncomingFile, *, result: IngestResult) -> None:
    """Valida el tamaño y extrae el texto de un PDF en memoria (Req 3, 10.4, 10.5)."""
    # 1) Validar el tamaño sobre bytes decodificados ANTES de extraer (Req 10.4, 10.5).
    if len(incoming.content) > MAX_PDF_BYTES:
        limite_mib = MAX_PDF_BYTES // (1024 * 1024)
        result.rejected.append(
            f"El PDF '{incoming.filename}' excede el tamaño máximo permitido "
            f"({limite_mib} MiB). Reducí su tamaño e intentá de nuevo."
        )
        return

    # 2) Extraer el texto en memoria (Req 3.1, 3.5, 11.5). Un PDF sin texto o un
    #    error de extracción no aborta el turno: se acumula un mensaje accionable
    #    en `rejected` (Req 3.6).
    try:
        texto = extract_pdf_text(incoming.content)
    except (ValueError, ModuleNotFoundError) as exc:
        result.rejected.append(str(exc))
        return

    # 3) Agregar el Texto_Extraido REDACTADO al contexto del turno (Req 3.2, 11.2).
    result.pdf_texts.append(config.redact_value(texto))
