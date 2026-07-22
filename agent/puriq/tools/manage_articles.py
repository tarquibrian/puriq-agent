"""manage_articles: CRUD de artículos del blog sobre el Content_Store (Req 2–5).

Capa fina que orquesta los helpers ya existentes para dar control CRUD sobre los
Articles (archivos markdown con frontmatter bajo ``/content``):

- ``ArticleStore`` (``tools/_article_store.py``, DD-1): lee/escribe/borra los
  ``.md`` derivando la colección por escaneo de ``/content`` (sin índice
  separado). ``ArticleStore.write`` valida el frontmatter contra el esquema
  ``"article"`` **antes** de tocar disco (DD-6), de forma atómica.
- ``merge_fields`` (``tools/_merge.py``, DD-5): merge a nivel de campo para la
  edición ("editar sin pisar"); nunca regenera el ``id``.
- ``slugify`` (``tools/_slug.py``): deriva el ``id`` del título, cumpliendo
  ``^[a-z0-9-]+$`` (Req 1.2, 2.1, 12.5).
- ``generate_content.get_provider`` (DD-3): proveedor de LLM que redacta el
  cuerpo del artículo cuando el usuario no lo aporta (Req 2.2). El LLM solo toca
  contenido.

Representación de un Article: un dict plano ``{**frontmatter, "body": body}``
(ver ``_article_store``). La clave ``"body"`` es el cuerpo markdown; el resto de
las claves conforman el frontmatter validado contra ``article.schema.json``.
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path

from puriq.tools import _article_store, generate_content
from puriq.tools._merge import merge_fields
from puriq.tools._slug import slugify

# Clave reservada del cuerpo markdown dentro del dict Article.
_BODY_KEY = "body"


def _today_iso() -> str:
    """Devuelve la fecha actual en formato ISO ``YYYY-MM-DD`` (Req 2.4)."""
    return _date.today().isoformat()


def _body_prompt(title: str, *, tags: list[str] | None, category: str | None,
                 summary: str | None) -> str:
    """Arma un prompt simple para que el LLM redacte el cuerpo del artículo.

    Usa el título y la información aportada por el usuario (categoría, etiquetas,
    resumen). Se mantiene deliberadamente sencillo (DD-3): el LLM solo redacta
    contenido a partir de lo que el usuario ya proporcionó.
    """
    partes = [
        "Sos un redactor de blog de turismo. Escribí el cuerpo de un artículo "
        "en markdown a partir de la siguiente información.",
        f"Título: {title}",
    ]
    if category:
        partes.append(f"Categoría: {category}")
    if tags:
        partes.append(f"Etiquetas: {', '.join(tags)}")
    if summary:
        partes.append(f"Resumen: {summary}")
    partes.append(
        "Devolvé solo el cuerpo del artículo en markdown (sin frontmatter ni "
        "el título como encabezado duplicado)."
    )
    return "\n".join(partes)


def create_article(
    content_dir: Path | str,
    *,
    title: str,
    body: str | None = None,
    date: str | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    summary: str | None = None,
) -> dict:
    """Crea un Article en el Content_Store. Devuelve ``{"id", "path"}``.

    Reglas (Req 2):
    - El ``id`` se deriva del título con ``slugify`` (Req 2.1, 12.5). Si el
      título está ausente/vacío → error "el título es obligatorio" (Req 2.6).
    - Si ya existe un Article con ese ``id`` → error "el artículo ya existe" y
      **no** se sobrescribe el archivo existente (Req 2.5).
    - Sin ``date`` → se asigna la fecha actual ``YYYY-MM-DD`` (Req 2.4).
    - Sin ``body`` → se genera con ``generate_content.get_provider().complete``
      (Req 2.2, DD-3). Con ``body`` aportado → se conserva sin invocar al LLM
      (Req 2.3).
    - En éxito, se escribe el ``.md`` vía ``ArticleStore.write`` (que valida el
      frontmatter contra ``article.schema.json`` antes de escribir) y se
      devuelve el ``id`` y la ruta del archivo (Req 2.7).

    Raises:
        ValueError: título vacío/ausente (Req 2.6), ``id`` duplicado (Req 2.5) o
            frontmatter inválido (propagado desde ``ArticleStore.write``).
    """
    if not (isinstance(title, str) and title.strip()):
        raise ValueError("el título es obligatorio")

    article_id = slugify(title)

    # No sobrescribir un artículo existente (Req 2.5).
    if _article_store.read(content_dir, article_id) is not None:
        raise ValueError(f"el artículo ya existe: '{article_id}'")

    # Fecha por defecto = hoy (Req 2.4).
    article_date = date if date else _today_iso()

    # Cuerpo: si el usuario lo aporta se conserva (Req 2.3); si no, lo genera el
    # LLM (Req 2.2). get_provider() se resuelve aquí para permitir monkeypatch.
    if body is None:
        provider = generate_content.get_provider()
        prompt = _body_prompt(
            title, tags=tags, category=category, summary=summary
        )
        article_body = provider.complete(prompt)
    else:
        article_body = body

    # Frontmatter: solo campos del esquema (additionalProperties: false).
    frontmatter: dict = {
        "id": article_id,
        "title": title,
        "date": article_date,
    }
    if tags is not None:
        frontmatter["tags"] = tags
    if category is not None:
        frontmatter["category"] = category
    if summary is not None:
        frontmatter["summary"] = summary

    article = {**frontmatter, _BODY_KEY: article_body}

    # Valida el frontmatter y persiste de forma atómica (DD-6).
    path = _article_store.write(content_dir, article)
    return {"id": article_id, "path": str(path)}


def list_articles(
    content_dir: Path | str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    tag: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Lista y filtra los artículos del Content_Store (Req 3).

    Deriva la colección escaneando ``/content`` (DD-1). Reglas:
    - Sin filtros → todos los artículos con su frontmatter (Req 3.1).
    - ``date_from``/``date_to`` → solo artículos cuyo ``date`` esté dentro del
      rango, **inclusive** los extremos (Req 3.2). Al ser fechas ISO
      ``YYYY-MM-DD``, la comparación lexicográfica de strings es equivalente a
      la comparación cronológica.
    - ``tag`` → solo artículos cuyo ``tags`` contenga esa etiqueta (Req 3.3).
    - ``category`` → solo artículos cuyo ``category`` sea igual (Req 3.4).
    - Varios filtros → conjunción: deben cumplirse todos (Req 3.5).
    - Sin coincidencias → lista vacía, sin error (Req 3.6).
    - El resultado se ordena por ``date`` de forma **descendente** (Req 3.7).
    """
    articles = _article_store.read_all(content_dir)

    def _matches(article: dict) -> bool:
        article_date = article.get("date")
        if date_from is not None:
            if not (isinstance(article_date, str) and article_date >= date_from):
                return False
        if date_to is not None:
            if not (isinstance(article_date, str) and article_date <= date_to):
                return False
        if tag is not None:
            article_tags = article.get("tags") or []
            if not (isinstance(article_tags, list) and tag in article_tags):
                return False
        if category is not None:
            if article.get("category") != category:
                return False
        return True

    filtered = [a for a in articles if _matches(a)]
    # Orden por fecha descendente (Req 3.7). Los artículos sin `date` van al final.
    filtered.sort(key=lambda a: a.get("date") or "", reverse=True)
    return filtered


def edit_article(content_dir: Path | str, *, id: str, **fields) -> dict:
    """Edita los campos indicados de un Article existente. Devuelve ``{"id"}``.

    Reglas (Req 4):
    - Merge a nivel de campo: solo se actualizan los campos indicados en
      ``fields`` y se preserva el resto (Req 4.1, DD-5). Editar ``title`` **no**
      regenera el ``id`` (Req 4.3): ``merge_fields`` conserva siempre el ``id``
      original.
    - Si el ``id`` no corresponde a ningún Article → error "no encontrado"
      (Req 4.2).
    - Se valida el frontmatter resultante contra ``article.schema.json`` antes
      de escribir (Req 4.4, vía ``ArticleStore.write``). Si una edición vacía un
      campo obligatorio, la validación lo rechaza nombrando el campo (Req 4.5).
    - En éxito, se escribe el archivo actualizado y se devuelve el ``id``
      (Req 4.6).

    Raises:
        ValueError: ``id`` inexistente (Req 4.2) o frontmatter resultante
            inválido (propagado desde ``ArticleStore.write``, Req 4.5).
    """
    existing = _article_store.read(content_dir, id)
    if existing is None:
        raise ValueError(f"artículo no encontrado: '{id}'")

    # Merge de solo los campos indicados; nunca regenera el id (DD-5).
    merged = merge_fields(existing, dict(fields))

    # Valida el frontmatter resultante y persiste de forma atómica (DD-6).
    _article_store.write(content_dir, merged)
    return {"id": id}


def delete_article(content_dir: Path | str, *, id: str) -> dict:
    """Elimina el Article con ese ``id`` del Content_Store. Devuelve ``{"id"}``.

    Reglas (Req 5):
    - Borra el ``.md`` correspondiente vía ``ArticleStore.delete`` (Req 5.1).
    - Si el ``id`` no corresponde a ningún Article → error "no encontrado"
      (Req 5.2). ``ArticleStore.delete`` lanza ``FileNotFoundError`` cuando no
      existe; aquí se traduce al error de dominio.
    - En éxito, devuelve el ``id`` eliminado (Req 5.3).

    Raises:
        ValueError: ``id`` inexistente (Req 5.2).
    """
    try:
        _article_store.delete(content_dir, id)
    except FileNotFoundError as exc:
        raise ValueError(f"artículo no encontrado: '{id}'") from exc
    return {"id": id}
