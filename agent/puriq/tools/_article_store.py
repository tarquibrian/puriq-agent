"""ArticleStore: la colección de artículos vive en `/content` como archivos `.md` (DD-1).

Este módulo implementa el acceso CRUD de bajo nivel a los Articles del
Content_Store. Siguiendo DD-1 del diseño, **no existe un índice separado**: la
colección se **deriva escaneando** `<content_dir>/*.md` y parseando el
frontmatter de cada archivo con `puriq.tools._frontmatter`. Los archivos reales
son la única fuente de verdad (editar a mano y reconstruir), evitando una
segunda fuente que pueda desincronizarse.

Representación de un Article
----------------------------
Un Article se representa como un **dict plano** que combina los campos del
frontmatter con el cuerpo markdown bajo la clave reservada ``"body"``::

    {
        "id": "cerro-rico",
        "title": "Cerro Rico",
        "date": "2024-01-01",
        "tags": ["mina", "colonial"],
        "category": "historia",
        "summary": "Un resumen",
        "body": "# Cerro Rico\n\nCuerpo markdown libre...",
    }

Es decir, ``article == {**frontmatter, "body": body}``. La clave ``"body"`` está
**reservada**: al persistir se separa del frontmatter (no forma parte del
frontmatter validado contra `article.schema.json`) y el resto de claves
constituyen el frontmatter. Un Article sin cuerpo tiene ``"body": ""``.

Relación id ↔ nombre de archivo
-------------------------------
El nombre de archivo se deriva del ``id``: el Article con ``id == "<id>"`` vive
en ``<content_dir>/<id>.md``. `read`/`delete` localizan el archivo por ese
mapeo; `write` escribe en esa ruta.
"""
from __future__ import annotations

from pathlib import Path

from puriq.tools import _frontmatter
from puriq.tools._persist import validate_then_write

# Un Article es un dict plano: frontmatter + la clave reservada "body".
Article = dict

# Clave reservada del cuerpo markdown dentro del dict Article.
BODY_KEY = "body"


def _md_path(content_dir: Path | str, id: str) -> Path:
    """Deriva la ruta del `.md` de un Article a partir de su `id`."""
    return Path(content_dir) / f"{id}.md"


def _to_article(frontmatter: dict, body: str) -> Article:
    """Combina frontmatter + body en la representación de Article."""
    return {**frontmatter, BODY_KEY: body}


def _split_article(article: Article) -> tuple[dict, str]:
    """Separa un Article en (frontmatter, body).

    La clave reservada ``"body"`` se extrae como cuerpo markdown; el resto de
    las claves conforman el frontmatter (lo que se valida contra el esquema).
    """
    frontmatter = {k: v for k, v in article.items() if k != BODY_KEY}
    body = article.get(BODY_KEY, "")
    return frontmatter, body


def read_all(content_dir: Path | str) -> list[Article]:
    """Escanea `<content_dir>/*.md` y devuelve la colección de Articles.

    Deriva la colección leyendo cada archivo markdown y parseando su
    frontmatter con `_frontmatter.parse` (DD-1: sin índice separado). El orden
    es **determinista**: los archivos se recorren ordenados por nombre. Si el
    directorio no existe, devuelve una lista vacía (aún no hay contenido).

    Cada elemento es un dict ``{**frontmatter, "body": body}`` (ver docstring del
    módulo). El parseo es tolerante: un archivo sin bloque frontmatter válido
    produce ``{"body": <texto completo>}`` (frontmatter vacío) en lugar de un
    error; la validación estricta del contrato ocurre en `write` (Req 1.5/1.6).
    """
    base = Path(content_dir)
    if not base.is_dir():
        return []
    articles: list[Article] = []
    for path in sorted(base.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = _frontmatter.parse(text)
        articles.append(_to_article(frontmatter, body))
    return articles


def read(content_dir: Path | str, id: str) -> Article | None:
    """Localiza el Article con ese `id` y lo devuelve, o `None` si no existe.

    El archivo se busca en ``<content_dir>/<id>.md``. Si no existe, devuelve
    ``None`` (contrato: ausencia se comunica con `None`, no con excepción). Si
    existe, parsea su frontmatter + cuerpo y devuelve la representación Article.
    """
    path = _md_path(content_dir, id)
    if not path.is_file():
        return None
    frontmatter, body = _frontmatter.parse(path.read_text(encoding="utf-8"))
    return _to_article(frontmatter, body)


def write(content_dir: Path | str, article: Article) -> Path:
    """Valida el frontmatter y persiste el Article de forma atómica.

    Separa el Article en (frontmatter, body). El archivo destino se deriva del
    ``id`` del frontmatter (``<content_dir>/<id>.md``). Se delega en
    `validate_then_write` (DD-6): valida el frontmatter contra el esquema
    ``"article"`` **antes** de tocar disco y, solo si pasa, escribe el markdown
    completo (``_frontmatter.serialize(frontmatter, body)``) de forma atómica.

    Un frontmatter inválido (p. ej. sin `id`/`title`/`date`, `id` que no cumple
    ``^[a-z0-9-]+$`` o un campo desconocido) provoca un `ValueError` que nombra
    el archivo y el campo que incumple, sin escritura parcial (Req 1.5, 1.6).

    Devuelve la ruta (`Path`) del archivo escrito.
    """
    frontmatter, body = _split_article(article)
    article_id = frontmatter.get("id")
    # La ruta se deriva del id. Si el id es inválido/ausente, se usa un nombre
    # marcador: validate_then_write valida ANTES de escribir, por lo que ese
    # archivo nunca llega a crearse (el error nombrará el campo `id`).
    filename = f"{article_id}.md" if isinstance(article_id, str) and article_id else "__invalid__.md"
    path = Path(content_dir) / filename
    try:
        return validate_then_write(
            frontmatter,
            "article",
            path,
            text=_frontmatter.serialize(frontmatter, body),
        )
    except ValueError as exc:
        # Enriquecer el mensaje nombrando el archivo destino (Req 1.6).
        raise ValueError(f"Article '{filename}': {exc}") from exc


def delete(content_dir: Path | str, id: str) -> Path:
    """Elimina el `.md` del Article con ese `id` y devuelve su ruta.

    El archivo se localiza en ``<content_dir>/<id>.md``. Si no existe, lanza
    `FileNotFoundError` (la capa superior lo traduce a "no encontrado", Req 5.2).
    Devuelve la ruta del archivo eliminado.
    """
    path = _md_path(content_dir, id)
    if not path.is_file():
        raise FileNotFoundError(f"No existe un artículo con id '{id}' en {content_dir}")
    path.unlink()
    return path
