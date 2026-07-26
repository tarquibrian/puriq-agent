"""Lógica pura de assets del wizard (DD-3).

Dos funciones puras respecto a rutas, sin E/S, aptas para property-based testing:

- ``normalize_asset_name``: normaliza el nombre de un archivo subido a
  ``slug.ext`` (Slug ASCII + extensión soportada), descartando cualquier
  componente de directorio para evitar rutas conflictivas (Req 4.4, 4.6).
- ``resolve_within_assets``: resuelve la ruta destino dentro de ``<project>/assets``
  y verifica que sea descendiente de ese árbol, rechazando cualquier intento de
  escape (``../``, rutas absolutas, symlinks) (Req 12.4).

Reutiliza ``puriq.tools._slug.slugify`` (misma normalización que el resto de las
tools; no se duplica).
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from puriq.tools._slug import slugify

# Formatos de imagen soportados por defecto (Req 4.4). El endpoint de carga puede
# pasar un conjunto distinto (p. ej. para video) vía el parámetro allowed_exts.
IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif"}
)

# Límite de tamaño de un Asset (Req 4.5). 10 MiB es suficiente para fotos y
# logos de un sitio turístico; cargas mayores se rechazan con un mensaje que
# indica el máximo permitido. Se compara ANTES de escribir en disco. Vive en el
# módulo puro (junto a IMAGE_EXTS) para que tanto la capa web (wizard/server.py)
# como las intake tools reutilicen exactamente el mismo límite sin acoplarse al
# servidor web (DD-3).
MAX_ASSET_BYTES = 10 * 1024 * 1024

# Stem de reserva cuando el nombre original no aporta ningún carácter ASCII
# alfanumérico (p. ej. "___.png"), para que el resultado siempre cumpla el
# patrón Slug ``^[a-z0-9-]+$``.
_FALLBACK_STEM = "asset"


def _basename(filename: str) -> str:
    """Devuelve solo el nombre base, descartando componentes de directorio.

    Es robusto frente a separadores POSIX (``/``) y Windows (``\\``), de modo que
    ``../../etc/passwd`` o ``C:\\evil\\logo.png`` se reducen a su último segmento
    antes de cualquier otro procesamiento (Req 4.6, 12.4).
    """
    # Normalizar los separadores de Windows a POSIX y quedarse con el último
    # segmento no vacío.
    posix_name = PureWindowsPath(filename).name if "\\" in filename else filename
    return PurePosixPath(posix_name).name


def _normalize_allowed(allowed_exts: object) -> set[str]:
    """Normaliza el conjunto de extensiones aceptadas a la forma ``.ext`` en minúsculas."""
    normalized: set[str] = set()
    for ext in allowed_exts or ():
        ext = str(ext).strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        normalized.add(ext)
    return normalized


def normalize_asset_name(filename: str, allowed_exts: object = IMAGE_EXTS) -> str:
    """Normaliza ``filename`` a ``slug.ext`` con extensión soportada (Req 4.4, 4.6).

    Pasos:
      1. Tomar el nombre base (descartar cualquier componente de directorio).
      2. Separar la extensión del *stem*.
      3. Slugificar el *stem* (NFKD → ASCII → kebab-case); si queda vacío, usar
         un *stem* de reserva para garantizar un Slug válido.
      4. Revalidar la extensión (en minúsculas) contra ``allowed_exts``.
      5. Recomponer ``slug + ext``.

    Args:
        filename: nombre del archivo subido (puede incluir rutas o separadores).
        allowed_exts: iterable de extensiones aceptadas, con o sin punto inicial
            (p. ej. ``{"png", ".jpg"}``). Por defecto, los formatos de imagen.

    Returns:
        El nombre normalizado ``slug.ext``. El *stem* cumple ``^[a-z0-9-]+$`` y la
        extensión pertenece a ``allowed_exts``.

    Raises:
        ValueError: si la extensión del archivo no está entre las soportadas; el
            mensaje lista los formatos aceptados.
    """
    allowed = _normalize_allowed(allowed_exts)

    base = _basename(filename)
    suffix = PurePosixPath(base).suffix.lower()

    if not allowed:
        raise ValueError("No hay formatos de archivo soportados configurados.")

    if suffix not in allowed:
        aceptados = ", ".join(sorted(allowed))
        recibido = suffix or "(sin extensión)"
        raise ValueError(
            f"Formato de archivo no soportado: {recibido}. "
            f"Formatos aceptados: {aceptados}."
        )

    stem = base[: -len(suffix)] if suffix else base
    slug = slugify(stem) or _FALLBACK_STEM
    return f"{slug}{suffix}"


def resolve_within_assets(project: Path, name: str) -> Path:
    """Resuelve ``<project>/assets/<name>`` y verifica que quede contenido (Req 12.4).

    Resuelve la ruta destino (siguiendo ``..`` y symlinks) y confirma que sea el
    propio directorio ``assets`` o un descendiente suyo. Cualquier nombre que
    escape del árbol —vía ``../``, ruta absoluta o symlink— se rechaza.

    Args:
        project: raíz del proyecto; el árbol permitido es ``<project>/assets``.
        name: nombre o ruta relativa del asset dentro de ``assets``.

    Returns:
        La ruta absoluta resuelta, garantizada dentro de ``<project>/assets``.

    Raises:
        ValueError: si la ruta resuelta escapa del directorio ``assets``.
    """
    assets_root = (Path(project) / "assets").resolve()
    target = (assets_root / name).resolve()

    if target != assets_root and not target.is_relative_to(assets_root):
        raise ValueError(
            f"La ruta del asset escapa del directorio permitido /assets: {name!r}."
        )

    return target
