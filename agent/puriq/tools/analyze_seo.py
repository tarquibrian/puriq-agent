"""analyze_seo: análisis SEO de solo lectura sobre el contenido/salida local (Req 10).

Analiza **únicamente** los artefactos locales del proyecto —``tourism-data.json``,
los artículos markdown del Content_Store (``content/``) y, si existe, el sitio
generado en ``dist/``— y devuelve sugerencias accionables. Es una operación de
**solo lectura**: nunca consulta una URL publicada en vivo (Req 10.1) y nunca
modifica el contenido del proyecto (Req 10.8, invariante 8).

Diseño (para poder property-testear la detección, Propiedades 17/18/19):
  - Cada regla es una función pequeña y **pura** que recibe la entrada ya cargada
    (una lista de Places/Events/Articles, o el texto de una página HTML) y devuelve
    la lista de issues que esa regla detecta. `analyze_seo` solo se encarga de la
    frontera de E/S (leer archivos) y de agregar los resultados de cada regla.
  - La detección es determinista respecto de la entrada: mismas entradas → mismas
    sugerencias, en un orden estable.

Reglas verificables (cada issue nombra el elemento y el campo/problema):
  - Falta de meta descripción o resumen en un Place/Event/Article (Req 10.2).
  - Contenido sin un título adecuado (Req 10.3).
  - Una imagen sin texto alternativo (Req 10.4).
  - Jerarquía de encabezados incorrecta en una página generada: salta un nivel
    (p. ej. h1→h3) o no comienza en h1 (Req 10.5).
  - Un Slug que no cumple ``^[a-z0-9-]+$`` o excede la longitud recomendada (Req 10.6).

Si no se detecta ningún problema, el resultado indica "sin problemas"
(``ok=True``, Req 10.7).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from puriq.tools import _frontmatter

# --- Convenciones de rutas del proyecto (ver puriq.core) --------------------

DATA_FILE = "tourism-data.json"
CONTENT_DIR = "content"
DIST_DIR = "dist"

# --- Parámetros de las reglas -----------------------------------------------

# Patrón de Slug bien formado, consistente con `schemas/` y `_slug.slugify`.
_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")

# Longitud máxima recomendada para un Slug antes de sugerir acortarlo (Req 10.6).
# 60 caracteres es un límite habitual para mantener URLs legibles y amigables.
RECOMMENDED_SLUG_MAX_LENGTH = 60

# --- Detección de imágenes / encabezados en texto ---------------------------

# Imagen markdown: ![alt](url). El grupo 1 es el texto alternativo (alt).
_MD_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]*)\)")

# Etiqueta <img ...> de HTML generado.
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
# Atributo alt="..." | alt='...' dentro de una etiqueta <img>.
_HTML_IMG_ALT = re.compile(r"""\balt\s*=\s*(?P<q>["'])(?P<alt>.*?)(?P=q)""", re.IGNORECASE)
# Atributo src="..." para nombrar la imagen en el issue.
_HTML_IMG_SRC = re.compile(r"""\bsrc\s*=\s*(?P<q>["'])(?P<src>.*?)(?P=q)""", re.IGNORECASE)

# Encabezados <h1>..<h6> del HTML generado (solo se necesita el nivel).
_HTML_HEADING = re.compile(r"<h(?P<level>[1-6])\b", re.IGNORECASE)


def _issue(element: str, rule: str, message: str, *, field: str | None = None) -> dict:
    """Construye un issue con una forma estable (nombra siempre el elemento).

    Args:
        element: identificador legible del elemento afectado (id de Place/Event/
            Article, nombre de página, o referencia de imagen).
        rule: etiqueta de la regla que se incumple (p. ej. ``"missing-description"``).
        message: sugerencia accionable en lenguaje natural.
        field: campo concreto afectado, cuando aplica (p. ej. ``"description"``).
    """
    issue = {"element": element, "rule": rule, "message": message}
    if field is not None:
        issue["field"] = field
    return issue


def _is_blank(value: object) -> bool:
    """True si `value` es None, no-string, o una cadena vacía/solo espacios."""
    return not (isinstance(value, str) and value.strip())


def _element_label(item: dict, fallback: str) -> str:
    """Etiqueta para nombrar un elemento en un issue: prefiere `id`, luego `name`."""
    for key in ("id", "name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


# --- Reglas puras -----------------------------------------------------------

def check_slug(element_kind: str, element_id: object) -> list[dict]:
    """Regla de Slug (Req 10.6): patrón `^[a-z0-9-]+$` y longitud recomendada.

    Devuelve un issue por cada problema detectado (puede haber más de uno). Un
    `id` ausente o no-string se reporta como Slug inválido.
    """
    label = element_id if isinstance(element_id, str) and element_id else element_kind
    issues: list[dict] = []
    if not isinstance(element_id, str) or not _SLUG_PATTERN.match(element_id):
        issues.append(
            _issue(
                str(label),
                "slug",
                f"El slug de {element_kind} '{label}' no cumple el patrón "
                f"^[a-z0-9-]+$.",
                field="id",
            )
        )
        return issues
    if len(element_id) > RECOMMENDED_SLUG_MAX_LENGTH:
        issues.append(
            _issue(
                element_id,
                "slug",
                f"El slug de {element_kind} '{element_id}' excede la longitud "
                f"recomendada de {RECOMMENDED_SLUG_MAX_LENGTH} caracteres.",
                field="id",
            )
        )
    return issues


def check_places(places: list[dict]) -> list[dict]:
    """Reglas de contenido sobre Places (Req 10.2, 10.3, 10.4, 10.6).

    - Título adecuado: `name` no vacío.
    - Meta descripción: `description` no vacía.
    - Resumen: `shortDescription` no vacío.
    - Slug: `id` cumple patrón y longitud recomendada.
    """
    issues: list[dict] = []
    for index, place in enumerate(places):
        label = _element_label(place, f"place[{index}]")
        if _is_blank(place.get("name")):
            issues.append(
                _issue(label, "missing-title",
                       f"El Place '{label}' carece de un título adecuado (name).",
                       field="name")
            )
        if _is_blank(place.get("description")):
            issues.append(
                _issue(label, "missing-description",
                       f"El Place '{label}' carece de meta descripción (description).",
                       field="description")
            )
        if _is_blank(place.get("shortDescription")):
            issues.append(
                _issue(label, "missing-summary",
                       f"El Place '{label}' carece de resumen (shortDescription).",
                       field="shortDescription")
            )
        issues.extend(check_slug("Place", place.get("id")))
    return issues


def check_events(events: list[dict]) -> list[dict]:
    """Reglas de contenido sobre Events (Req 10.2, 10.3, 10.6).

    - Título adecuado: `name` no vacío.
    - Meta descripción: `description` no vacía (los Events no tienen resumen aparte).
    - Slug: `id` cumple patrón y longitud recomendada.
    """
    issues: list[dict] = []
    for index, event in enumerate(events):
        label = _element_label(event, f"event[{index}]")
        if _is_blank(event.get("name")):
            issues.append(
                _issue(label, "missing-title",
                       f"El Event '{label}' carece de un título adecuado (name).",
                       field="name")
            )
        if _is_blank(event.get("description")):
            issues.append(
                _issue(label, "missing-description",
                       f"El Event '{label}' carece de meta descripción (description).",
                       field="description")
            )
        issues.extend(check_slug("Event", event.get("id")))
    return issues


def check_articles(articles: list[dict]) -> list[dict]:
    """Reglas de contenido sobre Articles (Req 10.2, 10.3, 10.4, 10.6).

    Cada `article` es un dict con el frontmatter parseado más una clave privada
    ``_body`` con el cuerpo markdown (para detectar imágenes sin alt).

    - Título adecuado: `title` no vacío.
    - Resumen: `summary` no vacío.
    - Imágenes markdown sin texto alternativo en el cuerpo.
    - Slug: `id` cumple patrón y longitud recomendada.
    """
    issues: list[dict] = []
    for index, article in enumerate(articles):
        label = _element_label(article, f"article[{index}]")
        if _is_blank(article.get("title")):
            issues.append(
                _issue(label, "missing-title",
                       f"El Article '{label}' carece de un título adecuado (title).",
                       field="title")
            )
        if _is_blank(article.get("summary")):
            issues.append(
                _issue(label, "missing-summary",
                       f"El Article '{label}' carece de resumen (summary).",
                       field="summary")
            )
        issues.extend(check_markdown_images(article.get("_body", ""), label))
        issues.extend(check_slug("Article", article.get("id")))
    return issues


def check_markdown_images(body: str, element_label: str) -> list[dict]:
    """Detecta imágenes markdown ``![](url)`` sin texto alternativo (Req 10.4)."""
    issues: list[dict] = []
    if not isinstance(body, str):
        return issues
    for match in _MD_IMAGE.finditer(body):
        if not match.group("alt").strip():
            url = match.group("url").strip() or "(sin url)"
            issues.append(
                _issue(
                    element_label,
                    "missing-alt",
                    f"La imagen '{url}' en '{element_label}' carece de texto "
                    f"alternativo (alt).",
                    field="alt",
                )
            )
    return issues


def check_html_images(html: str, page: str) -> list[dict]:
    """Detecta ``<img>`` sin atributo ``alt`` o con ``alt`` vacío (Req 10.4)."""
    issues: list[dict] = []
    if not isinstance(html, str):
        return issues
    for tag_match in _HTML_IMG.finditer(html):
        tag = tag_match.group(0)
        alt_match = _HTML_IMG_ALT.search(tag)
        if alt_match is not None and alt_match.group("alt").strip():
            continue
        src_match = _HTML_IMG_SRC.search(tag)
        src = src_match.group("src").strip() if src_match else "(sin src)"
        issues.append(
            _issue(
                page,
                "missing-alt",
                f"La imagen '{src}' en la página '{page}' carece de texto "
                f"alternativo (alt).",
                field="alt",
            )
        )
    return issues


def check_heading_hierarchy(html: str, page: str) -> list[dict]:
    """Detecta jerarquía de encabezados incorrecta en una página (Req 10.5).

    Marca la página como problemática si y solo si la secuencia de niveles `hN`:
      - no comienza en `h1`, o
      - salta un nivel al descender (p. ej. de `h1` a `h3` sin pasar por `h2`).

    Una página sin encabezados no se marca (no hay jerarquía que evaluar).
    """
    if not isinstance(html, str):
        return []
    levels = [int(m.group("level")) for m in _HTML_HEADING.finditer(html)]
    if not levels:
        return []

    if levels[0] != 1:
        return [
            _issue(
                page,
                "heading-hierarchy",
                f"La página '{page}' no comienza su jerarquía de encabezados en "
                f"h1 (comienza en h{levels[0]}).",
            )
        ]

    previous = levels[0]
    for level in levels[1:]:
        if level - previous > 1:
            return [
                _issue(
                    page,
                    "heading-hierarchy",
                    f"La página '{page}' salta un nivel de encabezado "
                    f"(de h{previous} a h{level}).",
                )
            ]
        previous = level
    return []


# --- Frontera de E/S (carga de artefactos locales, solo lectura) ------------

def _load_tourism_data(project: Path) -> dict:
    """Lee ``tourism-data.json`` de forma tolerante (sin validar contra el esquema).

    Devuelve un dict vacío si el archivo no existe o no es JSON parseable: el
    análisis SEO no debe fallar por un contrato ausente o malformado.
    """
    path = project / DATA_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_articles(project: Path) -> list[dict]:
    """Escanea ``content/*.md`` y parsea el frontmatter de cada artículo.

    Devuelve una lista de dicts con el frontmatter más la clave privada
    ``_body`` (cuerpo markdown), ordenada por nombre de archivo para determinismo.
    """
    content_dir = project / CONTENT_DIR
    if not content_dir.is_dir():
        return []
    articles: list[dict] = []
    for md_path in sorted(content_dir.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, body = _frontmatter.parse(text)
        article = dict(frontmatter)
        article["_body"] = body
        # Si el frontmatter no trae `id`, usar el nombre del archivo como fallback.
        article.setdefault("id", md_path.stem)
        articles.append(article)
    return articles


def _load_pages(project: Path) -> list[tuple[str, str]]:
    """Escanea ``dist/**/*.html`` y devuelve (nombre_relativo, contenido).

    Si ``dist/`` no existe, devuelve una lista vacía y las comprobaciones de
    nivel de página (imágenes/encabezados en HTML) se omiten con elegancia.
    Ordenado por ruta relativa para determinismo.
    """
    dist_dir = project / DIST_DIR
    if not dist_dir.is_dir():
        return []
    pages: list[tuple[str, str]] = []
    for html_path in sorted(dist_dir.rglob("*.html")):
        try:
            html = html_path.read_text(encoding="utf-8")
        except OSError:
            continue
        pages.append((str(html_path.relative_to(dist_dir)), html))
    return pages


def analyze_seo(project: Path) -> dict:
    """Analiza el contenido/salida local del proyecto y devuelve sugerencias SEO.

    Solo lectura: lee ``tourism-data.json``, los artículos de ``content/`` y, si
    existe, las páginas de ``dist/``; nunca consulta una URL en vivo (Req 10.1) ni
    modifica el contenido del proyecto (Req 10.8).

    Args:
        project: ruta del proyecto Puriq.

    Returns:
        ``{"issues": [...], "ok": bool}`` donde ``issues`` es la lista de
        sugerencias (cada una nombra el elemento y el campo/problema) y ``ok`` es
        ``True`` si no se detectó ningún problema (Req 10.7).
    """
    project = Path(project)

    data = _load_tourism_data(project)
    places = data.get("places") if isinstance(data.get("places"), list) else []
    events = data.get("events") if isinstance(data.get("events"), list) else []
    articles = _load_articles(project)
    pages = _load_pages(project)

    issues: list[dict] = []
    issues.extend(check_places(places))
    issues.extend(check_events(events))
    issues.extend(check_articles(articles))
    for page_name, html in pages:
        issues.extend(check_heading_hierarchy(html, page_name))
        issues.extend(check_html_images(html, page_name))

    return {"issues": issues, "ok": len(issues) == 0}
