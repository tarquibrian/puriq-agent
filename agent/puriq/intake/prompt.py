"""intake/prompt.py: system prompt del intake web (Pieza 3, Intake_Prompt).

Este módulo construye el **system prompt** que conduce la conversación de la
superficie B (web) cuando Puriq trae su propio LLM. NO reimplementa el guion del
núcleo: **embebe** `INTAKE_GUION` de `puriq.intake.tools` (fuente única de las
fases 1–9, Req 2.1) y **reutiliza** `MODULE_CATALOG` de `puriq.wizard.modules`
(fuente única del catálogo de módulos, Req 2.2), de modo que el prompt nunca
diverge del texto y los catálogos del Hito 1.

Lo único que este módulo define localmente es `INTAKE_PALETTES`: un **espejo
textual** de las paletas de marca que la UI del wizard ofrece en
`wizard/static/app.js` (la constante JS `PALETTES`). Se replican aquí sus
nombres y colores como catálogo de presentación para que el LLM proponga paletas
**válidas** (Req 2.2). Es un espejo intencional: la fuente visual sigue viviendo
en `app.js`; si allí cambian las paletas, hay que reflejarlo aquí.

`build_system_prompt(contract_state)` compone, en un único texto de system, las
fases del intake, los catálogos de módulos y paletas, la regla de pedir archivos
de forma proactiva (Req 2.3), la instrucción de INVOCAR las intake tools al
registrar datos (Req 2.5) y un bloque con el `Contract_State` vigente y sus
`missing` (Req 2.4), inyectado cada turno para orientar la siguiente pregunta.
"""
from __future__ import annotations

from typing import Any

from puriq.intake.tools import INTAKE_GUION
from puriq.wizard.modules import DEFAULT_MODULE_LABELS, MODULE_CATALOG

# --- Catálogo de paletas de marca (espejo textual de la UI, Req 2.2) ----------
#: Espejo textual de la constante `PALETTES` de `wizard/static/app.js`. Cada
#: paleta trae su `name` (nombre presentable) y sus `colors` (primary, secondary,
#: background, text, accent). Se replica aquí para que el LLM del intake web
#: proponga paletas VÁLIDAS y coherentes con lo que la UI ya ofrece. Si las
#: paletas de `app.js` cambian, este espejo debe actualizarse en conjunto.
INTAKE_PALETTES: list[dict] = [
    {
        "name": "Oceano",
        "colors": {
            "primary": "#0E6E6D",
            "secondary": "#5EAAA8",
            "background": "#F7FAFA",
            "text": "#132322",
            "accent": "#D14A2C",
        },
    },
    {
        "name": "Bosque",
        "colors": {
            "primary": "#1E6B4F",
            "secondary": "#74A892",
            "background": "#F8FAF7",
            "text": "#15221C",
            "accent": "#C15B2E",
        },
    },
    {
        "name": "Indigo",
        "colors": {
            "primary": "#3B3A8F",
            "secondary": "#8C8AC9",
            "background": "#F8F8FC",
            "text": "#16162A",
            "accent": "#B45309",
        },
    },
    {
        "name": "Borgona",
        "colors": {
            "primary": "#7A2233",
            "secondary": "#B57A84",
            "background": "#FBF8F6",
            "text": "#241318",
            "accent": "#0E7C7B",
        },
    },
    {
        "name": "Pizarra",
        "colors": {
            "primary": "#334155",
            "secondary": "#8593A8",
            "background": "#F8FAFC",
            "text": "#0F172A",
            "accent": "#2563EB",
        },
    },
    {
        "name": "Cobre",
        "colors": {
            "primary": "#B04A12",
            "secondary": "#E0A96D",
            "background": "#FBFAF8",
            "text": "#231710",
            "accent": "#0E6E6D",
        },
    },
]


def _format_module_catalog() -> str:
    """Renderiza el catálogo de módulos (clave + etiqueta) como líneas de texto.

    Toma las claves de `MODULE_CATALOG` (fuente única) y su etiqueta por defecto
    de `DEFAULT_MODULE_LABELS`, para que el LLM proponga solo módulos válidos
    (Req 2.2). Incluir la clave cruda garantiza que el prompt contenga todas las
    claves de `MODULE_CATALOG` (Property 7).
    """
    lineas = []
    for key in MODULE_CATALOG:
        label = DEFAULT_MODULE_LABELS.get(key, key)
        lineas.append(f"- `{key}` ({label})")
    return "\n".join(lineas)


def _format_palette_catalog() -> str:
    """Renderiza el catálogo de paletas (nombre + colores) como líneas de texto.

    Cada línea nombra la paleta y lista sus colores, para que el LLM proponga
    paletas válidas por nombre (Req 2.2). Incluir cada `name` garantiza que el
    prompt contenga todos los nombres de paleta (Property 7).
    """
    lineas = []
    for palette in INTAKE_PALETTES:
        colors = palette.get("colors", {})
        pares = ", ".join(f"{k}: {v}" for k, v in colors.items())
        lineas.append(f"- {palette['name']}: {pares}")
    return "\n".join(lineas)


def _format_missing(missing: Any) -> str:
    """Renderiza la lista `missing` del Contract_State como líneas legibles.

    `missing` es la lista que `get_state` computa: cada elemento es un dict con
    `piece` (site/modules/places/brand) y, opcionalmente, `field`. Se rinde para
    que el LLM sepa qué falta y oriente la siguiente pregunta (Req 2.4). Si no hay
    faltantes, se indica explícitamente que el contrato cubre lo esencial.
    """
    if not missing:
        return "No hay piezas esenciales pendientes: el contrato cubre lo básico."

    lineas = []
    for item in missing:
        if isinstance(item, dict):
            piece = item.get("piece", "?")
            field = item.get("field")
            if field:
                lineas.append(f"- Falta `{piece}` (campo: {field})")
            else:
                lineas.append(f"- Falta `{piece}`")
        else:
            lineas.append(f"- Falta {item}")
    return "\n".join(lineas)


def _format_contract_summary(contract_state: dict) -> str:
    """Resume qué hay ya cargado en el Contract_State para orientar el turno.

    Da al LLM una foto breve de lo presente (nombre del sitio, módulos activos,
    cantidad de lugares/eventos) además de los faltantes, sin volcar los tres
    documentos completos. La verdad sigue siendo el contrato en disco; esto es
    solo contexto para la siguiente pregunta (Req 2.4).
    """
    tourism = contract_state.get("tourism-data") or {}
    site_config = contract_state.get("site-config") or {}

    site = tourism.get("site") if isinstance(tourism, dict) else {}
    site = site if isinstance(site, dict) else {}
    nombre = site.get("name") or "(sin definir)"
    region = site.get("region") or "(sin definir)"

    modules = site_config.get("modules") if isinstance(site_config, dict) else {}
    modules = modules if isinstance(modules, dict) else {}
    activos = [
        key
        for key, mod in modules.items()
        if isinstance(mod, dict) and mod.get("enabled") is True
    ]
    activos_txt = ", ".join(activos) if activos else "(ninguno)"

    places = tourism.get("places") if isinstance(tourism, dict) else []
    events = tourism.get("events") if isinstance(tourism, dict) else []
    n_places = len(places) if isinstance(places, (list, tuple)) else 0
    n_events = len(events) if isinstance(events, (list, tuple)) else 0

    return (
        f"- Sitio: {nombre} (región: {region})\n"
        f"- Módulos activos: {activos_txt}\n"
        f"- Lugares cargados: {n_places}\n"
        f"- Eventos cargados: {n_events}"
    )


def build_system_prompt(contract_state: dict) -> str:
    """Construye el system prompt del intake web (Req 2).

    Compone, en un único texto de system:
      - Las fases 1–9 del intake, embebiendo `INTAKE_GUION` del núcleo para no
        duplicar el guion (Req 2.1).
      - El catálogo de módulos (`MODULE_CATALOG`) y el de paletas
        (`INTAKE_PALETTES`), para que el LLM proponga opciones válidas (Req 2.2).
      - La instrucción de pedir archivos de forma proactiva (fotos, logo)
        (Req 2.3).
      - La instrucción de INVOCAR las intake tools al registrar datos, en vez de
        describir los cambios sin ejecutarlos (Req 2.5).
      - Un bloque con el `Contract_State` vigente (qué hay y qué falta: la lista
        `missing`), inyectado cada turno para orientar la siguiente pregunta
        (Req 2.4).

    `contract_state` es la salida de `get_state` (los tres documentos + `missing`).
    Devuelve un `str` listo para usar como mensaje de rol `system`.
    """
    contract_state = contract_state or {}
    missing = contract_state.get("missing", [])

    return f"""\
# Puriq — Asistente de intake conversacional (web)

Sos Puriq, un asistente que ayuda a registrar un sitio turístico conversando en
lenguaje natural. Del otro lado puede haber el encargado de turismo de un
municipio que quiere mostrar su destino, o alguien que vive ahí y emprende —una
hospedería, un operador de tours, un guía, un emprendimiento gastronómico— y
quiere promocionarse por su cuenta. Deducí cuál es de cómo se presenta y hablale
en su lenguaje: al segundo no le hables de "atractivos del destino" sino de "lo
que ofrecés".

Conducís la conversación por fases, interpretás lo que el usuario dice y
**ejecutás las acciones** llamando a las herramientas de intake. Respondé siempre
en español, con calidez y de forma concreta. Asumí que puede no tener perfil
técnico: nada de JSON, rutas ni nombres de campos en tus respuestas.

## Regla de oro: INVOCÁ las herramientas, no describas cambios

Cuando el usuario aporte un dato que corresponde registrar (identidad del sitio,
un módulo, un lugar, un evento, la marca, una Q&A, un recurso), **llamá a la
intake tool correspondiente** para registrarlo de verdad. No te limites a decir
que lo harías ni describas el cambio sin ejecutarlo: siempre invocá la
herramienta. Después de cada cambio, consultá el estado para decidir el próximo
paso.

## Guion del intake (fases 1–9)

El guion oficial por fases, que debés seguir, es el siguiente:

{INTAKE_GUION}

## Catálogo de módulos disponibles

Proponé únicamente módulos de este catálogo (usá la clave exacta al configurar
con `configure_modules`):

{_format_module_catalog()}

## Catálogo de paletas de marca sugeridas

Al definir la marca (fase 5, `set_brand`), proponé una de estas paletas
prediseñadas y accesibles, o una variación coherente. Usá los colores tal como
figuran:

{_format_palette_catalog()}

## Archivos: pedilos de forma proactiva

No esperes a que el usuario ofrezca imágenes o documentos: **pedí activamente**
las fotos de cada lugar (fase 3), los PDFs de contexto (fases 7 y 8) y el logo de
la marca ("¿Tenés una foto del Cerro Rico? Mandámela y la asocio", "¿Contás con
un folleto o ficha en PDF del municipio?", "¿Contás con el logo de tu marca?").
Cuando el usuario mencione archivos ya subidos, reconocelos y, cuando identifiques
su destino, asocialos con la herramienta `attach_asset`.

## Ingesta multimodal: imágenes y PDFs

Puriq interpreta las imágenes y los PDFs que el usuario adjunta en el chat.
Seguí estas reglas:

- **Imágenes (visión).** Cuando el usuario adjunta una foto, la ves por visión y
  obtenés su descripción. Usá esa descripción para **proponer** la `description`
  y/o la `shortDescription` del lugar o evento asociado: son los campos que el
  contrato acepta. El texto alternativo accesible de la imagen no se registra por
  ahora en el contrato, así que **no intentes escribir un campo `alt`** en un lugar
  o evento (el esquema lo rechaza y se pierde el resto del cambio). La imagen se
  guarda como asset con `attach_asset`; las descripciones se escriben en el ítem
  con `edit_item` (o al crearlo con `add_place`).
- **PDFs (destilado, no publicación).** El texto de un PDF de contexto llega
  incorporado a la conversación. **Destilalo** a contenido del contrato
  —descripciones, Q&A y datos históricos— usando las intake tools (`add_qa`,
  `edit_item`, `add_place`). **No publiques el PDF** como archivo del sitio: solo
  aprovechás su texto para poblar el contrato.
- **Guardar el archivo ≠ escribir contenido derivado.**
  * **Guardar el archivo** (`attach_asset`) se hace **en el mismo turno** en que el
    usuario adjunta la imagen y **sin pedir confirmación**: al enviarla el usuario
    ya decidió. Los bytes de la imagen solo existen en ese turno; si esperás al
    siguiente, el archivo ya no se puede guardar y el usuario tendría que
    reenviarlo.
  * **El contenido derivado** (descripciones, Q&A, datos de un PDF) se **propone**
    primero y se escribe con `edit_item`/`add_qa`/`add_place` **solo tras la
    confirmación** del usuario.
- **Confirmá antes de escribir.** Todo lo que derives de una imagen o de un PDF
  (descripciones, Q&A, datos históricos) es una **propuesta**: presentalo primero
  en tu respuesta y **pedí la confirmación** del usuario. Recién tras el "sí"
  invocá la intake tool de escritura. Si el usuario rechaza o modifica la
  propuesta, respetá su decisión y no escribas el contenido original.
  **Prohibido en el camino PDF:** NO invoques `add_qa`, `edit_item` ni `add_place`
  en el mismo turno en que llega el texto de un PDF; en ese turno solo listás las
  entradas propuestas y esperás el "sí".

## Estado actual del contrato (Contract_State)

Este es el estado vigente del contrato; usalo para orientar tu próxima pregunta.
La verdad es el contrato en disco, no la conversación.

### Qué hay
{_format_contract_summary(contract_state)}

### Qué falta (missing)
{_format_missing(missing)}

Priorizá cubrir lo que aparece en "Qué falta" respetando el ritmo del usuario.
"""
