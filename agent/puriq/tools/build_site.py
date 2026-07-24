"""build_site: ensambla el sitio estatico a partir del contrato.

No genera codigo: copia la plantilla /template, inyecta los 3 JSON como datos,
activa/desactiva modulos segun site.config y aplica theme.tokens como CSS vars,
luego corre el build de Astro.

El ensamblado se estructura en pasos para respetar las invariantes del diseno
(Req 5) y permitir que las tareas siguientes agreguen su parte sin romper la
firma publica de `assemble`:

  1. `_prepare_workdir`  -> copia la Template a un directorio de trabajo
     excluyendo `node_modules` y `dist` (Req 5.1).
  2. `_write_contract`   -> escribe los 3 documentos del contrato en
     `src/data/` y los valida contra sus esquemas ANTES del build (Req 5.2,
     5.10).
  3. `_resolve_modules` / `_materialize_brand` -> resuelve la activacion y el
     orden de los modulos (Req 5.3, 5.4, 5.5) y materializa el tema como
     variables CSS (Req 5.6). Ver notas de integracion con la Template abajo.
  4. `_run_astro_build` -> instala dependencias y ejecuta `npm run build`
     (Astro) en el directorio de trabajo (Req 5.7), luego promueve la salida a
     `project/dist` y devuelve esa ruta (Req 5.8); un build con error se
     reporta con la salida relevante del proceso (Req 5.9).

Notas de integracion del build (tarea 9.3):

- npm: como la copia de la Template excluye `node_modules` (paso 1), hay que
  instalar dependencias antes del build. Se elige `npm ci` cuando existe
  `package-lock.json` (instalacion reproducible y limpia a partir del lockfile)
  y `npm install` en su ausencia; si `npm ci` falla por desincronizacion del
  lockfile, se reintenta con `npm install` para no bloquear el build. Todos los
  comandos se ejecutan con `subprocess.run` pasando los argumentos como LISTA
  (nunca `shell=True` con interpolacion) y con `cwd=<directorio de trabajo>`.

- dist: `astro build` escribe su salida en `<workdir>/dist` por defecto
  (`astro.config.mjs` usa `output: "static"`). En vez de reconfigurar `outDir`
  (mas fragil ante rutas relativas del proyecto Astro), se adopta el enfoque
  mas robusto: construir en el working dir y luego MOVER `<workdir>/dist` a
  `project/dist`, eliminando antes cualquier `project/dist` previo para no
  mezclar builds (Req 5.8).

Notas de integracion con la Template (verificadas al implementar la tarea 9.2):

- Modulos (Req 5.3, 5.4, 5.5): la Template YA resuelve la activacion y el orden
  de forma data-driven. `template/src/lib/data.ts` expone `activeModules()`, que
  filtra los modulos con `enabled` y los ordena por `order`; `src/pages/
  index.astro` los renderiza en ese orden desde un registry. Por lo tanto, la
  activacion/orden se materializa simplemente escribiendo el `site.config.json`
  correcto en `src/data/` (lo hace el paso 9.1). El agente NO edita codigo de
  modulos (invariante de arquitectura). `_resolve_modules` replica esa logica en
  Python para garantizar/verificar el comportamiento (Property 17) sin duplicar
  ni sustituir la resolucion de la Template.

- Tema (Req 5.6): la Template YA traduce `colors`/`typography` a variables CSS.
  `template/src/layouts/Base.astro` aplica `themeTokens` con `define:vars` de
  Astro (colores, fuentes y radius) sobre `:root`, tomando los valores del
  `theme.tokens.json` que escribe el paso 9.1. Es decir, el theming efectivo es
  data-driven. Como complemento explicito y verificable (Property 18), la tarea
  9.2 genera ademas un archivo de tokens CSS (`_theme_to_css`) y lo escribe en
  `src/data/theme.css`: materializa el conjunto COMPLETO de variables del
  Design_System (`--color-*`, `--font-*`, `--space-*`, `--fs-*`/`--lh-*`,
  `--shadow-*`, `--radius-*`, `--bp-*`, `--motion-*`, `--container-*` y el
  `--radius` heredado), aplicando defaults para los tokens ausentes (Req 5.6,
  16.2, 16.5), sin reescribir componentes de la Template.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from puriq import schemas
from puriq.tools import _frontmatter, generate_content

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "template"

# Directorio de trabajo (copia parametrizable de la Template) dentro del proyecto.
WORK_DIRNAME = ".puriq-build"

# Subruta, relativa al directorio de trabajo, donde la Template carga el
# contrato (ver template/src/lib/data.ts, que importa los JSON desde src/data/).
DATA_SUBDIR = Path("src") / "data"

# Nombres EXACTOS que la Template espera en src/data/ (los importa data.ts).
DATA_FILENAME = "tourism-data.json"
CONFIG_FILENAME = "site.config.json"
THEME_FILENAME = "theme.tokens.json"

# Archivo de tokens CSS materializado desde Theme_Tokens (Req 5.6). Se escribe
# en src/data/ como artefacto explicito e importable; ver `_theme_to_css`.
THEME_CSS_FILENAME = "theme.css"

# Directorio del Content_Store dentro del proyecto: los Articles del blog son
# archivos markdown con frontmatter en `<project>/content/*.md` (los produce
# content-management; ver tools/manage_articles.py). El build los inyecta como
# una Content Collection de Astro (ver `_inject_articles`).
CONTENT_DIRNAME = "content"

# Subruta, relativa al directorio de trabajo, de la Content Collection `blog`
# de Astro. Astro descubre las colecciones bajo `src/content/<coleccion>/`.
BLOG_COLLECTION_SUBDIR = Path("src") / "content" / "blog"

# Config de las Content Collections que espera Astro 4 (schema con zod). Se
# escribe en `src/content/config.ts` del directorio de trabajo. El `date` del
# frontmatter es un string ISO `YYYY-MM-DD`; `z.coerce.date()` lo normaliza a
# Date para poder ordenar por fecha. El resto de campos del frontmatter de un
# Article (id/tags/category/summary) son opcionales salvo `title`.
CONTENT_CONFIG_SUBPATH = Path("src") / "content" / "config.ts"
_CONTENT_COLLECTION_CONFIG = """\
import { defineCollection, z } from "astro:content";

// Coleccion `blog`: Articles inyectados desde <project>/content/*.md por el
// build (build_site._inject_articles). El schema refleja el frontmatter de un
// Article; `date` se coacciona a Date para ordenar por fecha descendente.
const blog = defineCollection({
  type: "content",
  schema: z.object({
    id: z.string().optional(),
    title: z.string(),
    date: z.coerce.date(),
    tags: z.array(z.string()).optional(),
    category: z.string().optional(),
    summary: z.string().optional(),
  }),
});

export const collections = { blog };
"""

# Archivo companion para las traducciones (clave `i18n` que agrega
# generate_content.enrich). NO forma parte del contrato validado: se escribe
# aparte para que la Template pueda consumirlo sin invalidar el esquema del
# tourism-data (que fija `additionalProperties: false`).
I18N_FILENAME = "i18n.json"

# Base de conocimiento Q&A del modulo chatweb. Se materializa como una lista
# normalizada de {"question","answer"} en `src/data/faq.json`, que la Template
# importa para la recuperacion client-side del asistente (ver
# `_inject_faq`). No forma parte del contrato validado: es un artefacto
# derivado del `content/` del proyecto.
FAQ_FILENAME = "faq.json"

# Subdirectorio del Content_Store con el FAQ en markdown del proyecto:
# `<project>/content/faq/*.md`. Cada `## Heading` es una pregunta y el texto
# hasta el siguiente `##` (o EOF) es su respuesta (ver `_parse_faq_markdown`).
FAQ_DIRNAME = "faq"

# Archivo JSON opcional con Q&A ya estructurados (lo escribe el wizard):
# `<project>/content/qa.json`, una lista de {"question","answer"}.
QA_JSON_FILENAME = "qa.json"


def _prepare_workdir(project: Path) -> Path:
    """Copia la Template a un directorio de trabajo limpio y devuelve su ruta.

    Recrea `project/.puriq-build` desde cero (borra una copia previa si existe)
    y clona la Template excluyendo `node_modules` y `dist` (Req 5.1), que se
    regeneran localmente y no deben arrastrarse a la copia parametrizable.
    """
    work = Path(project) / WORK_DIRNAME
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(
        TEMPLATE_DIR, work, ignore=shutil.ignore_patterns("node_modules", "dist")
    )
    return work


def _write_contract(work: Path, data: dict, config: dict, theme: dict) -> None:
    """Escribe los 3 documentos del contrato en `src/data/` y los valida (Req 5.2, 5.10).

    Pasos:
      1. Obtiene la vista del `tourism-data` conforme al contrato con
         `generate_content.contract_view(data)`, que descarta la clave companion
         `i18n` (traducciones) porque el esquema fija `additionalProperties:
         false` y no la admite. Esa vista es la que se persiste y valida como
         `tourism-data.json`.
      2. Valida los 3 documentos contra sus esquemas ANTES de escribirlos, para
         que un contrato invalido impida el build (Req 5.10). Se usa
         `schemas.validate`, unica fuente de validacion del contrato (Req 9.4).
      3. Escribe los 3 JSON con `schemas.dumps` (ensure_ascii=False, indent=2)
         en `src/data/`, con los nombres EXACTOS que la Template importa.
      4. Si `data` trae traducciones bajo `i18n`, las escribe en un archivo
         companion aparte (`src/data/i18n.json`) para que la Template pueda
         consumirlas; NUNCA dentro de `tourism-data.json`.
    """
    data_dir = work / DATA_SUBDIR
    data_dir.mkdir(parents=True, exist_ok=True)

    # Vista conforme al contrato del tourism-data (sin la clave companion i18n).
    data_contract = generate_content.contract_view(data)

    # Validacion estricta de los 3 documentos ANTES de escribir (Req 5.10).
    schemas.validate(data_contract, "tourism-data")
    schemas.validate(config, "site-config")
    schemas.validate(theme, "theme-tokens")

    # Escritura de los 3 documentos del contrato con los nombres esperados.
    (data_dir / DATA_FILENAME).write_text(schemas.dumps(data_contract))
    (data_dir / CONFIG_FILENAME).write_text(schemas.dumps(config))
    (data_dir / THEME_FILENAME).write_text(schemas.dumps(theme))

    # Traducciones (companion fuera del contrato): archivo separado si existen.
    i18n = data.get(generate_content.I18N_KEY)
    if i18n:
        (data_dir / I18N_FILENAME).write_text(schemas.dumps(i18n))


def _resolve_modules(config: dict) -> list[dict]:
    """Resuelve los modulos activos, en orden, desde `Site_Config.modules` (Req 5.3-5.5).

    Devuelve la lista de modulos con `enabled=true`, cada uno como dict con su
    `key` mas sus propiedades (`order`, `label`, etc.), ordenados de forma
    ascendente por `order`. Los modulos con `enabled=false` se excluyen.

    Esta funcion NO edita ni genera codigo de modulos: replica en Python la
    misma resolucion data-driven que la Template ya hace en
    `template/src/lib/data.ts` (`activeModules()`), de modo que la activacion y
    el orden queden garantizados por los datos escritos (el `site.config.json`
    que persiste el paso 9.1). Sirve como punto unico y verificable de la
    Property 17 (subconjunto habilitado y ordenado) sin sustituir la logica de
    la Template.

    Args:
        config: documento Site_Config (ya validado contra su esquema).

    Returns:
        Lista de modulos activos ordenados por `order` ascendente; cada elemento
        es un dict que incluye la clave `key` del modulo.
    """
    modules = config.get("modules", {})
    activos = [
        {"key": key, **spec}
        for key, spec in modules.items()
        if isinstance(spec, dict) and spec.get("enabled")
    ]
    activos.sort(key=lambda m: m.get("order", 0))
    return activos


# Defaults del Design_System (DD-1). Es la MISMA tabla que vive en
# `template/src/design-system/defaults.ts` (`DESIGN_DEFAULTS`), replicada aqui
# para materializar `src/data/theme.css` con el conjunto completo de variables
# aunque el usuario defina pocos o ningun token ampliado (Req 1.4, 16.2, 16.5).
# Cualquier cambio en los valores por defecto debe reflejarse en ambos lugares
# (unica fuente conceptual de verdad, documentada en el diseno).
DESIGN_DEFAULTS: dict = {
    "spacing": {
        "xs": "0.25rem",
        "sm": "0.5rem",
        "md": "1rem",
        "lg": "1.5rem",
        "xl": "3rem",
        "2xl": "6rem",
        "3xl": "9rem",
    },
    "typeScale": {
        "h1": {"size": "clamp(2.75rem, 6vw, 4.5rem)", "lineHeight": "1.05"},
        "h2": {"size": "clamp(2rem, 4vw, 3rem)", "lineHeight": "1.1"},
        "h3": {"size": "clamp(1.35rem, 2vw, 1.75rem)", "lineHeight": "1.2"},
        "body": {"size": "1.0625rem", "lineHeight": "1.7"},
        "small": {"size": "0.9375rem", "lineHeight": "1.55"},
    },
    "shadows": {
        "sm": "0 1px 2px rgba(16,24,40,.04)",
        "md": "0 10px 30px -12px rgba(16,24,40,.14)",
        "lg": "0 30px 60px -20px rgba(16,24,40,.22)",
    },
    "radii": {"sm": "6px", "md": "14px", "lg": "28px", "pill": "999px"},
    "breakpoints": {"sm": "640px", "md": "768px", "lg": "1024px"},
    "motion": {
        "durationFast": "140ms",
        "durationBase": "260ms",
        "easing": "cubic-bezier(.22,1,.36,1)",
    },
    "container": {"sm": "640px", "md": "820px", "lg": "1160px", "xl": "1320px"},
}

# Nombres de las variables CSS que emite cada token de motion. El esquema fija
# las tres claves (`durationFast`/`durationBase`/`easing`), por lo que el mapeo
# es explicito en vez de derivarse de la clave.
_MOTION_VAR = {
    "durationFast": "--motion-duration-fast",
    "durationBase": "--motion-duration-base",
    "easing": "--motion-easing",
}


def _merge_defaults(default, user):
    """Fusiona `user` sobre `default` sin pisar lo que el usuario definio.

    Replica en Python la semantica pura de `resolveTokens` (DD-1): un valor del
    usuario ya presente nunca se sobreescribe y las claves ausentes se rellenan
    con el default correspondiente. La fusion es recursiva para los mapas
    anidados (p. ej. `typeScale.h1.{size,lineHeight}`), de modo que un usuario
    que define solo `size` conserva el `lineHeight` por defecto. Es idempotente:
    fusionar dos veces produce el mismo resultado.
    """
    if isinstance(default, dict) and isinstance(user, dict):
        fusionado = dict(default)
        for clave, valor in user.items():
            if clave in fusionado:
                fusionado[clave] = _merge_defaults(fusionado[clave], valor)
            else:
                fusionado[clave] = valor
        return fusionado
    # El valor del usuario (cuando existe) gana sobre el default.
    return user if user is not None else default


def _theme_to_css(theme: dict) -> str:
    """Materializa TODOS los tokens del Design_System a variables CSS (Req 5.6).

    Genera un bloque `:root { ... }` con el conjunto COMPLETO de variables CSS,
    aplicando la tabla de defaults `DESIGN_DEFAULTS` (misma que
    `resolveTokens`/`DESIGN_DEFAULTS` de la Template) para cada token ausente en
    `theme.tokens.json`. Asi el conjunto de variables emitidas es estable y
    completo sin importar cuantos tokens haya definido el usuario (Req 16.2,
    16.5), preservando la retrocompatibilidad.

    Variables emitidas:

      - colores    -> `--color-primary`, `--color-secondary`,
        `--color-background`, `--color-text`, `--color-accent` (los que esten
        definidos; los colores no tienen default de marca).
      - tipografia -> `--font-heading`, `--font-body`, `--font-base-size`.
      - spacing    -> `--space-<paso>` (xs, sm, md, lg, xl, 2xl, ...).
      - typeScale  -> `--fs-<nivel>` y `--lh-<nivel>` (size / lineHeight).
      - shadows    -> `--shadow-<nombre>`.
      - radii      -> `--radius-<nombre>`.
      - breakpoints-> `--bp-<nombre>`.
      - motion     -> `--motion-duration-fast`, `--motion-duration-base`,
        `--motion-easing`.
      - container  -> `--container-<nombre>`.
      - `--radius` cuando `theme.radius` esta presente (token heredado).

    Los colores y las fuentes se mantienen exactamente como antes (siguen siendo
    tokens de marca sin default), garantizando que la Property 18 y los tests
    existentes de color/tipografia sigan verdes. Es una funcion pura del dict
    `theme`: no muta la entrada ni depende de estado externo.

    Args:
        theme: documento Theme_Tokens (ya validado contra su esquema).

    Returns:
        El contenido del archivo CSS de tokens como string.
    """
    colors = theme.get("colors", {}) or {}
    typography = theme.get("typography", {}) or {}

    # Tokens ampliados resueltos: default + lo que definio el usuario. Cada mapa
    # se fusiona con su default para que las variables ausentes tomen su valor
    # del Design_System sin pisar lo definido por el usuario.
    spacing = _merge_defaults(DESIGN_DEFAULTS["spacing"], theme.get("spacing") or {})
    type_scale = _merge_defaults(
        DESIGN_DEFAULTS["typeScale"], theme.get("typeScale") or {}
    )
    shadows = _merge_defaults(DESIGN_DEFAULTS["shadows"], theme.get("shadows") or {})
    radii = _merge_defaults(DESIGN_DEFAULTS["radii"], theme.get("radii") or {})
    breakpoints = _merge_defaults(
        DESIGN_DEFAULTS["breakpoints"], theme.get("breakpoints") or {}
    )
    motion = _merge_defaults(DESIGN_DEFAULTS["motion"], theme.get("motion") or {})
    container = _merge_defaults(
        DESIGN_DEFAULTS["container"], theme.get("container") or {}
    )

    lineas: list[str] = [":root {"]

    # Colores: se emite una variable por cada color definido (sin default).
    for clave in ("primary", "secondary", "background", "text", "accent"):
        valor = colors.get(clave)
        if valor:
            lineas.append(f"  --color-{clave}: {valor};")

    # Tipografia: fuentes de titulos/cuerpo y tamano base (sin default).
    if typography.get("headingFont"):
        lineas.append(f"  --font-heading: {typography['headingFont']};")
    if typography.get("bodyFont"):
        lineas.append(f"  --font-body: {typography['bodyFont']};")
    if typography.get("baseSize"):
        lineas.append(f"  --font-base-size: {typography['baseSize']};")

    # Spacing_Scale -> --space-<paso>.
    for clave, valor in spacing.items():
        lineas.append(f"  --space-{clave}: {valor};")

    # Type_Scale -> --fs-<nivel> (size) y --lh-<nivel> (lineHeight, si existe).
    for clave, escala in type_scale.items():
        if not isinstance(escala, dict):
            continue
        if escala.get("size"):
            lineas.append(f"  --fs-{clave}: {escala['size']};")
        if escala.get("lineHeight"):
            lineas.append(f"  --lh-{clave}: {escala['lineHeight']};")

    # Sombras -> --shadow-<nombre>.
    for clave, valor in shadows.items():
        lineas.append(f"  --shadow-{clave}: {valor};")

    # Radios -> --radius-<nombre>.
    for clave, valor in radii.items():
        lineas.append(f"  --radius-{clave}: {valor};")

    # Breakpoints -> --bp-<nombre>.
    for clave, valor in breakpoints.items():
        lineas.append(f"  --bp-{clave}: {valor};")

    # Motion -> --motion-duration-fast/-base y --motion-easing.
    for clave, valor in motion.items():
        var = _MOTION_VAR.get(clave)
        if var and valor:
            lineas.append(f"  {var}: {valor};")

    # Anchos de contenedor -> --container-<nombre>.
    for clave, valor in container.items():
        lineas.append(f"  --container-{clave}: {valor};")

    # Radio de esquinas heredado (token opcional en el esquema).
    if theme.get("radius"):
        lineas.append(f"  --radius: {theme['radius']};")

    lineas.append("}")
    return "\n".join(lineas) + "\n"


def _materialize_brand(work: Path, config: dict, theme: dict) -> list[dict]:
    """Materializa modulos y marca en el directorio de trabajo (Req 5.3-5.6).

    - Modulos (Req 5.3, 5.4, 5.5): resuelve la activacion y el orden con
      `_resolve_modules`. La materializacion efectiva es el `site.config.json`
      ya escrito en `src/data/` (paso 9.1), que la Template lee via
      `activeModules()`; aqui la resolucion se computa para garantizar/verificar
      el contrato de datos, sin escribir una segunda fuente de verdad ni editar
      codigo de modulos.
    - Marca (Req 5.6): traduce el tema a variables CSS con `_theme_to_css` y lo
      escribe en `src/data/theme.css` como artefacto explicito e importable. El
      theming efectivo tambien lo aplica la Template en `Base.astro` via
      `define:vars` desde `theme.tokens.json`; este archivo es el complemento
      verificable de la materializacion de marca.

    Args:
        work: directorio de trabajo (copia parametrizable de la Template).
        config: documento Site_Config (ya validado).
        theme: documento Theme_Tokens (ya validado).

    Returns:
        La lista de modulos activos resueltos (por si el llamador la necesita).
    """
    # Resolucion de modulos (data-driven, garantizada por site.config.json).
    modulos_activos = _resolve_modules(config)

    # Materializacion de la marca como variables CSS.
    data_dir = work / DATA_SUBDIR
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / THEME_CSS_FILENAME).write_text(_theme_to_css(theme))

    return modulos_activos


def _inject_articles(work: Path, project: Path) -> int:
    """Inyecta los Articles del proyecto como Content Collection de Astro.

    Los Articles del blog se guardan como markdown con frontmatter en
    `<project>/content/*.md` (los administra content-management; el build NO
    reimplementa esa logica, solo lee lo ya producido). Astro los renderiza de
    forma nativa a traves de una Content Collection: se copian esos `.md` a
    `<work>/src/content/blog/` y se escribe el `src/content/config.ts` con el
    schema (zod) que Astro espera. `Blog.astro` los consume con
    `getCollection("blog")`, los ordena por fecha descendente y renderiza el
    cuerpo markdown real.

    Se elige Content Collections (frente a un JSON intermedio) porque la version
    de Astro instalada (^4.10) las soporta y renderiza el markdown a HTML sin
    dependencias extra (relevante con el disco casi lleno): el propio pipeline de
    Astro compila el cuerpo. Reutiliza `_frontmatter.parse` solo para descartar
    archivos que no sean Articles validos (sin `title`).

    El paso es tolerante a la ausencia de contenido (Req: el build no debe
    requerir articulos): siempre escribe el `config.ts` y crea el directorio de
    la coleccion (vacio si no hay articulos), de modo que `getCollection("blog")`
    devuelva `[]` y `Blog.astro` muestre su estado vacio sin romper el build.

    Args:
        work: directorio de trabajo (copia parametrizable de la Template).
        project: raiz del proyecto Puriq, donde vive `content/`.

    Returns:
        La cantidad de Articles inyectados (0 si no hay contenido).
    """
    # Config de la coleccion + directorio destino: se crean siempre para que la
    # coleccion exista aunque no haya articulos (estado vacio sin errores).
    config_path = work / CONTENT_CONFIG_SUBPATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_CONTENT_COLLECTION_CONFIG)

    blog_dir = work / BLOG_COLLECTION_SUBDIR
    blog_dir.mkdir(parents=True, exist_ok=True)

    content_dir = Path(project) / CONTENT_DIRNAME
    if not content_dir.is_dir():
        return 0

    inyectados = 0
    for md in sorted(content_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        frontmatter, _body = _frontmatter.parse(text)
        # Descarta markdown que no sea un Article valido (sin titulo).
        if not (isinstance(frontmatter.get("title"), str) and frontmatter["title"].strip()):
            continue
        shutil.copyfile(md, blog_dir / md.name)
        inyectados += 1
    return inyectados


def _parse_faq_markdown(text: str) -> list[dict]:
    """Extrae pares {question, answer} de un FAQ en markdown.

    Formato esperado (ver examples/potosi-bo/content/faq/faq.md):

        # Titulo (se ignora)
        Linea de intro (se ignora)

        ## Pregunta 1
        Respuesta en uno o mas parrafos hasta el siguiente `##`.

        ## Pregunta 2
        ...

    Cada encabezado de nivel 2 (`## ...`) es una pregunta; el texto que le sigue
    hasta el proximo encabezado `##`/`#` (o el fin del archivo) es su respuesta.
    El titulo `#` y cualquier texto antes del primer `##` se descartan. Los
    parrafos de la respuesta se unen con un espacio y se recortan; se omiten
    entradas con pregunta o respuesta vacia.

    Parser local minimo (sin dependencias nuevas): recorre las lineas y agrupa
    por encabezado de segundo nivel. Solo se tratan como encabezado las lineas
    que empiezan con `## ` (dos almohadillas); un `#` de primer nivel cierra la
    respuesta actual pero no abre pregunta.
    """
    pares: list[dict] = []
    pregunta: str | None = None
    lineas_respuesta: list[str] = []

    def _flush() -> None:
        nonlocal pregunta, lineas_respuesta
        if pregunta is not None:
            respuesta = " ".join(
                l.strip() for l in lineas_respuesta if l.strip()
            ).strip()
            q = pregunta.strip()
            if q and respuesta:
                pares.append({"question": q, "answer": respuesta})
        pregunta = None
        lineas_respuesta = []

    for linea in text.splitlines():
        stripped = linea.strip()
        if stripped.startswith("## "):
            # Nuevo encabezado de pregunta: cierra el anterior y abre este.
            _flush()
            pregunta = stripped[3:].strip()
        elif stripped.startswith("# "):
            # Titulo de primer nivel: cierra cualquier respuesta en curso y se
            # ignora (no abre pregunta).
            _flush()
        else:
            if pregunta is not None:
                lineas_respuesta.append(linea)
    _flush()
    return pares


def _load_qa_json(path: Path) -> list[dict]:
    """Lee `content/qa.json` (lista de {question, answer}) de forma tolerante.

    Devuelve la lista de entradas con `question`/`answer` string; ignora el
    archivo si no existe, no es JSON valido o no es una lista. Las entradas mal
    formadas (sin claves string) se descartan.
    """
    import json

    if not path.is_file():
        return []
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(datos, list):
        return []

    pares: list[dict] = []
    for item in datos:
        if not isinstance(item, dict):
            continue
        q = item.get("question")
        a = item.get("answer")
        if isinstance(q, str) and isinstance(a, str):
            pares.append({"question": q, "answer": a})
    return pares


def _inject_faq(work: Path, project: Path) -> int:
    """Materializa la base de conocimiento Q&A del chatweb en `src/data/faq.json`.

    Construye una lista normalizada de {"question","answer"} a partir del
    `content/` del proyecto:

      - `content/faq/*.md`: se parsean con `_parse_faq_markdown` (cada `##` es
        una pregunta y su texto hasta el siguiente encabezado, la respuesta).
      - `content/qa.json`: si existe y es una lista de {question, answer}, se
        agregan esas entradas.

    Las entradas con pregunta o respuesta vacia se descartan y se deduplica por
    pregunta (normalizada: recortada y en minusculas), conservando la primera
    aparicion. El resultado SIEMPRE se escribe en `<work>/src/data/faq.json`
    (aunque sea `[]`) para que el import de la Template resuelva sin fallar,
    incluso si el proyecto no tiene `content/` (tolerante a su ausencia).

    Args:
        work: directorio de trabajo (copia parametrizable de la Template).
        project: raiz del proyecto Puriq, donde vive `content/`.

    Returns:
        La cantidad de pares Q&A escritos (0 si no hay conocimiento).
    """
    data_dir = work / DATA_SUBDIR
    data_dir.mkdir(parents=True, exist_ok=True)

    pares: list[dict] = []
    content_dir = Path(project) / CONTENT_DIRNAME

    if content_dir.is_dir():
        # 1) FAQ en markdown bajo content/faq/*.md
        faq_dir = content_dir / FAQ_DIRNAME
        if faq_dir.is_dir():
            for md in sorted(faq_dir.glob("*.md")):
                try:
                    texto = md.read_text(encoding="utf-8")
                except OSError:
                    continue
                pares.extend(_parse_faq_markdown(texto))

        # 2) Q&A estructurados en content/qa.json (los escribe el wizard).
        pares.extend(_load_qa_json(content_dir / QA_JSON_FILENAME))

    # Normalizacion final: descarta vacios y deduplica por pregunta normalizada
    # (recortada + minusculas), conservando la primera aparicion.
    vistos: set[str] = set()
    normalizados: list[dict] = []
    for par in pares:
        q = (par.get("question") or "").strip()
        a = (par.get("answer") or "").strip()
        if not q or not a:
            continue
        clave = q.lower()
        if clave in vistos:
            continue
        vistos.add(clave)
        normalizados.append({"question": q, "answer": a})

    # Siempre se escribe el archivo (aunque sea []) para que el import resuelva.
    (data_dir / FAQ_FILENAME).write_text(schemas.dumps(normalizados))
    return len(normalizados)


# Subdirectorio del proyecto con los recursos estaticos del usuario (imagenes
# de places/events, logo, hero, ...). El contrato los referencia con rutas
# relativas tipo `assets/<archivo>` (ver tourism-data.json), por lo que deben
# quedar servidos en el mismo prefijo `/assets/` del sitio construido.
ASSETS_DIRNAME = "assets"

# Astro copia tal cual el contenido de `public/` a la raiz de `dist/`. Para que
# `assets/<archivo>` resuelva en la preview, los recursos del proyecto se copian
# a `<work>/public/assets/`.
PUBLIC_ASSETS_SUBDIR = Path("public") / "assets"

# Subdirectorio OPCIONAL del proyecto con las fuentes tipograficas de la marca
# (`.woff2`). Se auto-hospedan: la Template emite un `@font-face` por archivo
# presente y lo sirve desde el propio sitio, sin pedirle nada a un CDN de
# terceros (un sitio de gobierno no deberia filtrar la IP de sus visitantes, y
# asi el sitio tambien funciona sin salida a internet).
#
# Convencion de nombre: `<familia-en-slug>-<peso>.woff2`, p. ej.
# `playfair-display-700.woff2`. El catalogo de familias y sus pesos vive en
# `template/src/design-system/fonts.ts`. Si el directorio no existe, el sitio usa
# la pila de respaldo del catalogo (fuentes del sistema parecidas) y no se emite
# ningun `@font-face`.
FONTS_DIRNAME = "fonts"
PUBLIC_FONTS_SUBDIR = Path("public") / "fonts"


def _inject_assets(work: Path, project: Path) -> int:
    """Copia los recursos estaticos del proyecto para que queden servidos en `/assets/`.

    El contrato referencia imagenes con rutas relativas `assets/<archivo>` (p.
    ej. `places[].images`), pero la Template no incluye esos recursos. Astro
    publica el contenido de `public/` en la raiz de `dist/`, asi que se copia
    `<project>/assets/` a `<work>/public/assets/` para que esas rutas resuelvan
    en la preview y el sitio construido.

    Es tolerante a la ausencia de `assets/` (no todos los proyectos tienen
    recursos propios): en ese caso no hace nada y devuelve 0. No genera ni
    transforma imagenes, solo las copia (invariante de arquitectura).

    Args:
        work: directorio de trabajo (copia parametrizable de la Template).
        project: raiz del proyecto Puriq, donde vive `assets/`.

    Returns:
        La cantidad de archivos de recursos copiados (0 si no hay `assets/`).
    """
    origen = Path(project) / ASSETS_DIRNAME
    if not origen.is_dir():
        return 0

    destino = work / PUBLIC_ASSETS_SUBDIR
    destino.mkdir(parents=True, exist_ok=True)

    copiados = 0
    for archivo in sorted(origen.rglob("*")):
        if not archivo.is_file():
            continue
        rel = archivo.relative_to(origen)
        salida = destino / rel
        salida.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archivo, salida)
        copiados += 1
    return copiados


def find_dangling_assets(project: Path, data: dict, config: dict) -> list[str]:
    """Devuelve las rutas `assets/...` que el contrato referencia y no existen.

    El contrato apunta a las imagenes por ruta relativa, pero nada garantiza que
    el archivo este: el usuario puede declarar una foto en el wizard y no
    subirla, o borrarla despues. La Template degrada con elegancia ante una
    referencia colgante (el hero cae a degradado, la ficha muestra su marcador de
    posicion), pero en silencio: el usuario ve un sitio mas pobre sin entender por
    que. Esta comprobacion le pone nombre al problema.

    Se revisan las `images` de Places y Events, el `logo` de la marca y cualquier
    ruta `assets/...` que aparezca en el `site.config` (hero heredado y secciones
    de portada). Las URLs absolutas se ignoran: viven fuera del proyecto y no se
    pueden comprobar aca.

    Returns:
        Lista ordenada y sin repetidos de rutas referenciadas que no existen.
    """
    import re

    referencias: set[str] = set()
    for clave in ("places", "events"):
        for item in data.get(clave) or []:
            if isinstance(item, dict):
                for ruta in item.get("images") or []:
                    if isinstance(ruta, str):
                        referencias.add(ruta.strip())

    # Rutas embebidas en la estructura del sitio (hero heredado, landing, ...).
    referencias.update(re.findall(r"assets/[\w./-]+", json.dumps(config)))

    assets_dir = Path(project) / ASSETS_DIRNAME
    colgantes = []
    for ruta in referencias:
        if not ruta or ruta.startswith(("http://", "https://", "//", "data:")):
            continue
        relativa = ruta[len(ASSETS_DIRNAME) + 1:] if ruta.startswith(f"{ASSETS_DIRNAME}/") else ruta
        if not (assets_dir / relativa).is_file():
            colgantes.append(ruta)
    return sorted(colgantes)


def _font_slug(name: str) -> str:
    """Convierte un nombre de familia al prefijo de sus archivos.

    Espeja `fontSlug` de `template/src/design-system/fonts.ts`: minusculas, sin
    acentos y con los tramos no alfanumericos colapsados en `-`
    (``"Playfair Display"`` -> ``playfair-display``).
    """
    import re
    import unicodedata

    normalizado = unicodedata.normalize("NFD", name.strip().lower())
    sin_acentos = "".join(c for c in normalizado if unicodedata.category(c) != "Mn")
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", sin_acentos))


def _inject_fonts(work: Path, project: Path, theme: dict) -> int:
    """Deja en `/fonts/` SOLO las fuentes de las familias que usa la marca.

    Las fuentes son OPCIONALES: `theme.tokens.json` declara la marca tipografica
    por NOMBRE de familia (`"Playfair Display"`), y la Template resuelve ese
    nombre contra su catalogo. Si el archivo esta disponible, la Template emite un
    `@font-face` y lo sirve desde el propio sitio; si no, usa la pila de respaldo
    del catalogo (fuentes del sistema elegidas por parecido) sin emitir ninguna
    regla, de modo que no haya peticiones fallidas.

    Dos fuentes de archivos, en este orden:
      1. El catalogo que la Template trae en `public/fonts/` (ya presente en el
         directorio de trabajo por la copia de la Template).
      2. `<project>/fonts/*.woff2`, que PISA al catalogo: asi un gobierno puede
         aportar su tipografia institucional propia.

    Y una PODA: se borran del directorio de trabajo los `.woff2` que no
    pertenezcan a las familias del tema. Sin esto, cada sitio publicaba el
    catalogo entero —el sitio de Potosi (Playfair + Inter) se llevaba tambien los
    tres archivos de Poppins— y ese peso muerto crece con cada familia que se
    agregue al catalogo.

    Solo se manejan archivos `.woff2` (el unico formato que declara la Template);
    cualquier otro archivo del directorio del proyecto se ignora.

    Args:
        work: directorio de trabajo (copia parametrizable de la Template).
        project: raiz del proyecto Puriq, donde puede vivir `fonts/`.
        theme: documento Theme_Tokens, de donde salen las familias en uso.

    Returns:
        La cantidad de archivos de fuente que quedan publicados.
    """
    destino = work / PUBLIC_FONTS_SUBDIR

    # 1. Fuentes propias del proyecto: pisan al catalogo de la Template.
    origen = Path(project) / FONTS_DIRNAME
    if origen.is_dir():
        destino.mkdir(parents=True, exist_ok=True)
        for archivo in sorted(origen.glob("*.woff2")):
            if archivo.is_file():
                shutil.copyfile(archivo, destino / archivo.name)

    if not destino.is_dir():
        return 0

    # 2. Poda: se conservan solo los archivos de las familias que declara la marca.
    typography = theme.get("typography") or {}
    familias = [typography.get("headingFont"), typography.get("bodyFont")]
    prefijos = {_font_slug(f) for f in familias if isinstance(f, str) and f.strip()}

    conservados = 0
    for archivo in sorted(destino.iterdir()):
        if not archivo.is_file():
            continue
        # Todo lo que no sea una fuente (p. ej. el README del catalogo) es
        # documentacion para quien mantiene la Template, no algo que deba
        # terminar publicado en el sitio del gobierno.
        if archivo.suffix != ".woff2":
            archivo.unlink()
            continue
        # `playfair-display-var.woff2` / `poppins-600.woff2` -> prefijo de familia.
        nombre = archivo.stem
        if any(nombre == p or nombre.startswith(f"{p}-") for p in prefijos):
            conservados += 1
        else:
            archivo.unlink()
    return conservados


def _npm(args: list[str], work: Path, *, paso: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta `npm <args>` en el directorio de trabajo y verifica el resultado.

    Corre `npm` con `subprocess.run` pasando los argumentos como LISTA (nunca
    `shell=True` con interpolacion, para evitar inyeccion) y capturando salida
    de texto. Si `npm` no esta disponible en el PATH, o si el proceso termina
    con `returncode != 0`, lanza un `RuntimeError` con un mensaje claro que
    incluye la salida relevante (stdout/stderr) del proceso (Req 5.9).

    Args:
        args: argumentos de npm (p. ej. ``["run", "build"]``).
        work: directorio de trabajo donde se ejecuta el comando (`cwd`).
        paso: descripcion legible del paso, para los mensajes de error.

    Returns:
        El `CompletedProcess` cuando el comando termina con exito.

    Raises:
        RuntimeError: si `npm` no esta disponible o el comando falla.
    """
    cmd = ["npm", *args]
    try:
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "No se encontro 'npm' en el PATH. Instala Node.js y npm para poder "
            f"construir el sitio (paso: {paso})."
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"Fallo `{' '.join(cmd)}` al {paso} (codigo {proc.returncode}).\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def _install_dependencies(work: Path) -> None:
    """Instala las dependencias de la Template en el directorio de trabajo (Req 5.7).

    La copia de la Template excluye `node_modules` (paso 1), asi que hay que
    instalar antes del build. Se prefiere `npm ci` cuando existe
    `package-lock.json` (instalacion reproducible desde el lockfile); si `npm
    ci` falla (tipicamente por desincronizacion del lockfile), se reintenta con
    `npm install` para no bloquear el build. Sin lockfile, se usa directamente
    `npm install`.
    """
    if shutil.which("npm") is None:
        raise RuntimeError(
            "No se encontro 'npm' en el PATH. Instala Node.js y npm para poder "
            "construir el sitio."
        )

    if (work / "package-lock.json").exists():
        try:
            _npm(["ci"], work, paso="instalar dependencias (npm ci)")
            return
        except RuntimeError:
            # Lockfile desincronizado u otro problema de `npm ci`: se degrada a
            # `npm install`, que reconstruye node_modules de forma tolerante.
            pass
    _npm(["install"], work, paso="instalar dependencias (npm install)")


def _run_astro_build(work: Path, project: Path) -> Path:
    """Instala dependencias, corre el build de Astro y deja la salida en `project/dist`.

    Pasos:
      1. Instala dependencias en el directorio de trabajo (`_install_dependencies`).
      2. Ejecuta `npm run build` (= `astro build`) con `cwd=work` (Req 5.7). Un
         exit code != 0 lanza un error con la salida del proceso (Req 5.9).
      3. Astro escribe su salida en `<work>/dist`. Se promueve esa carpeta a
         `project/dist`, eliminando primero un `project/dist` previo para no
         mezclar builds, y se devuelve esa ruta (Req 5.8).

    Args:
        work: directorio de trabajo (copia parametrizable de la Template).
        project: raiz del proyecto Puriq, donde quedara `dist/`.

    Returns:
        La ruta `project/dist` con el sitio estatico construido.

    Raises:
        RuntimeError: si npm no esta disponible, si el build falla, o si el
            build termina sin generar `dist/`.
    """
    _install_dependencies(work)
    _npm(["run", "build"], work, paso="ejecutar el build de Astro")

    dist_origen = work / "dist"
    if not dist_origen.is_dir():
        raise RuntimeError(
            "El build de Astro finalizo sin errores pero no genero el directorio "
            f"'dist/' esperado en {dist_origen}."
        )

    dist_destino = project / "dist"
    if dist_destino.exists():
        shutil.rmtree(dist_destino)
    shutil.move(str(dist_origen), str(dist_destino))
    return dist_destino


def assemble(project: Path, data: dict, config: dict, theme: dict) -> Path:
    """Genera el sitio en project/dist y devuelve esa ruta.

    Esta funcion orquesta el ensamblado en pasos (ver docstring del modulo).
    Las tareas 9.1 (preparacion del directorio de trabajo y escritura/validacion
    del contrato) y 9.2 (resolucion de modulos y materializacion del tema como
    CSS vars) ya estan implementadas; la tarea 9.3 (`npm run build`) completa el
    paso restante sin cambiar la firma.

    Args:
        project: raiz del proyecto Puriq (donde vive el contrato y saldra dist/).
        data: documento Tourism_Data ya geocodificado/enriquecido; puede traer
            la clave companion `i18n` con traducciones (se escribe aparte).
        config: documento Site_Config.
        theme: documento Theme_Tokens.

    Returns:
        La ruta `project/dist` donde quedara el sitio estatico construido.
    """
    work = _prepare_workdir(project)
    _write_contract(work, data, config, theme)
    _materialize_brand(work, config, theme)
    _inject_articles(work, Path(project))
    _inject_faq(work, Path(project))
    _inject_assets(work, Path(project))
    _inject_fonts(work, Path(project), theme)
    return _run_astro_build(work, Path(project))


def serve(project: Path, port: int = 4322) -> None:
    """Sirve `project/dist` en local para previsualizar el sitio (Req 6).

    Previsualizacion local de un sitio estatico ya construido. Sirve el
    contenido de `project/dist` con la libreria estandar `http.server`
    (`ThreadingHTTPServer` + `SimpleHTTPRequestHandler` acotado a `dist/` via
    `directory`), sin frameworks pesados.

    Comportamiento:
      - `project/dist` existe -> sirve su contenido en el puerto indicado
        (Req 6.1).
      - `project/dist` ausente -> lanza `FileNotFoundError` indicando ejecutar
        `puriq build` primero (Req 6.2). Reutiliza el mismo estilo de mensaje que
        `deploy` para dist ausente, por consistencia.
      - Sin puerto indicado -> puerto por defecto 4322 (Req 6.3), fijado en la
        firma.

    Nota: servir es un proceso **bloqueante** (sirve hasta Ctrl-C). Esto es
    esperable: `serve` se invoca desde el comando `puriq preview`, que el usuario
    corre e interrumpe manualmente. Se maneja `KeyboardInterrupt` para un cierre
    limpio del servidor.

    Args:
        project: raiz del proyecto Puriq que contiene el directorio `dist/`.
        port: puerto TCP donde exponer la preview; por defecto 4322 (Req 6.3).

    Raises:
        FileNotFoundError: si no existe `project/dist`; el mensaje indica
            ejecutar `puriq build` primero (Req 6.2).
    """
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    dist = Path(project) / "dist"
    # Req 6.2: exigir un build previo, con el mismo estilo de mensaje que deploy.
    if not dist.is_dir():
        raise FileNotFoundError(
            "No existe el directorio 'dist/'. Ejecuta `puriq build` primero."
        )

    # Handler acotado al directorio dist/ (no expone el resto del proyecto).
    handler = partial(SimpleHTTPRequestHandler, directory=str(dist))
    # Req 6.1 / 6.3: servir el contenido de dist/ en el puerto indicado.
    servidor = ThreadingHTTPServer(("", port), handler)

    print(f"Sirviendo dist/ en http://localhost:{port} (Ctrl-C para detener)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        # Cierre limpio ante Ctrl-C: el usuario termina la preview.
        print("\nDeteniendo la previsualizacion...")
    finally:
        servidor.shutdown()
        servidor.server_close()
