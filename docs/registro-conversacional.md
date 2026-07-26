# Registro conversacional de Puriq — diseño para construir

> Documento de continuación. Objetivo: un agente con el que el encargado de
> turismo **conversa** (por la web propia de Puriq y por cualquier cliente MCP
> como Claude Desktop o Kiro), el agente pregunta, interpreta lenguaje natural,
> **pide y recibe imágenes/PDFs**, y llena el contrato (3 JSON + `assets/` +
> `content/`) hasta poder construir el sitio.

Estado al escribir este doc: el registro existe como **formulario** (wizard web)
y como **tools sueltas** (MCP + CLI). Falta la capa conversacional y exponer el
intake por MCP.

---

## 1. Arquitectura: un núcleo, dos superficies

La decisión clave (acordada): el registro debe funcionar tanto en la **web de
Puriq** como en **Claude Desktop / Kiro / cualquier cliente MCP**. Eso obliga a
separar el núcleo (las acciones) de quién conduce la conversación.

```
                 ┌─────────────────────────────┐
                 │   INTAKE TOOLS (núcleo)      │   ← una sola implementación
                 │   set_site, add_place,       │     envuelve los constructores
                 │   configure_modules,         │     puros que YA existen
                 │   set_brand, add_qa,         │
                 │   attach_asset, get_state,   │
                 │   build ...                  │
                 └──────────┬───────────┬───────┘
                            │           │
              ┌─────────────┘           └──────────────┐
              ▼                                         ▼
   SUPERFICIE A: MCP                        SUPERFICIE B: Web propia
   (cliente externo trae SU LLM)            (Puriq trae Bedrock + loop)
   - Claude Desktop, Kiro, etc.             - endpoint /api/chat
   - Puriq expone las tools;                - Puriq conduce la conversación
     el cliente hace la charla              - preview del esqueleto EN VIVO
   - visión/PDF los procesa el              - upload de archivos nativo
     LLM del cliente                          (drag & drop ya existe)
```

**Por qué así:** las mismas tools sirven a los dos. En MCP, el cliente ya es un
agente (tiene LLM, hace tool-use, procesa imágenes) — Puriq solo aporta las
acciones y sus descripciones. En la web, Puriq **es** el agente: trae el LLM
(Bedrock), el guion, y muestra el sitio armándose.

---

## 2. Cimientos que YA existen (reutilizar, no reconstruir)

| Pieza | Qué da | Ubicación |
|---|---|---|
| Constructores puros | `build_place`, `build_event`, `make_coords` | `wizard/intake.py` |
| | `build_modules` + `DEFAULT_MODULE_LABELS` | `wizard/modules.py` |
| | `build_landing` | `wizard/landing.py` |
| Validadores | `validate_domain`, `validate_qa_entry`, `validate_deploy_target` | `wizard/validation.py` |
| Persistencia atómica | `_load_contract`, `merge_document`, `save_contract` | `wizard/contracts.py` |
| Assets seguros | `normalize_asset_name`, `resolve_within_assets`, `IMAGE_EXTS`, `MAX_ASSET_BYTES`, `POST /api/assets` | `wizard/assets.py`, `wizard/server.py` |
| Motor LLM | `LLMProvider` (Bedrock/OpenAI/Ollama), `get_provider()` | `tools/generate_content.py` |
| Pipeline | `build_site`, `deploy`, geocode, `find_dangling_assets` | `tools/` |
| MCP server | `list_tools` / `call_tool`, 11 tools de pipeline/edición | `mcp/server.py` |
| Catálogos | módulos, landing, fuentes, paletas, deploy targets | varios |

**Lo difícil ya está resuelto.** Cada acción tiene su función pura + validación +
persistencia. Falta envolverlas como tools de intake y conducir la charla.

Nota importante: el MCP actual expone **edición** de contenido existente
(`edit_content`, `delete_content`, `query_content`, `bulk_update`,
`manage_articles`) y el **pipeline** (`scan_resources`, `import_open_data`,
`generate_content`, `build_site`, `deploy`, `analyze_seo`). **No** expone el
intake inicial (crear sitio, activar módulos, agregar un lugar, marca, portada,
Q&A). Eso vive solo en el wizard como REST. Esta pieza es el hueco a llenar.

---

## 3. Las 7 piezas a construir

### Pieza 1 — Intake tools (el núcleo compartido)

Un módulo nuevo `agent/puriq/intake/tools.py` que declara cada acción como tool
con su JSON Schema (mismo patrón que `mcp/server.py`). Cada una **envuelve** un
constructor puro; no reimplementa lógica.

| Tool | Envuelve | Escribe en |
|---|---|---|
| `set_site` | site doc + `validate_domain` | `tourism-data.site`, `site.config.deploy/contact` |
| `configure_modules` | `build_modules` | `site.config.modules` |
| `add_place` | `build_place` | `tourism-data.places` |
| `add_event` | `build_event` | `tourism-data.events` |
| `edit_item` / `remove_item` | `edit_content` / `delete_content` | places/events |
| `set_brand` | validación hex + fuentes | `theme.tokens` |
| `configure_landing` | `build_landing` | `site.config.landing` |
| `add_qa` | `validate_qa_entry` | `content/qa.json` |
| `attach_asset` | `normalize_asset_name` + `resolve_within_assets` | copia a `assets/`, asocia |
| `get_state` | `_load_contract` ×3 | (lectura) devuelve **qué hay y qué falta** |
| `build` | `build_site` | genera `dist/` |

`get_state` es central: devuelve el estado + una lista de "faltantes" (sin
nombre de sitio, sin lugares, sin marca…) que el agente usa para saber qué
preguntar.

Cada tool: valida → persiste con `save_contract` (draft cuando aplica) →
devuelve el estado nuevo. Errores → mensaje accionable redactado (reutiliza
`wizard_error_response` + `config.redact`).

### Pieza 2 — Exponer las intake tools por MCP (superficie A)

Extender `mcp/server.py`: agregar las intake tools al `list_tools` y al
`call_tool`, delegando en `intake/tools.py`. Con esto, Claude Desktop / Kiro
descubren y llaman las tools; el LLM del cliente conduce la charla.

El **guion** (qué preguntar y en qué orden) en MCP vive en las **descripciones**
de las tools y, opcionalmente, en un recurso MCP (`resources/`) con las
instrucciones del intake, para que el cliente lo cargue como contexto.

### Pieza 3 — Loop del agente + guion (superficie B, web)

`agent/puriq/intake/agent.py`: el bucle que conduce la conversación cuando Puriq
trae el LLM. Por turno:
1. Recibe `{mensaje, archivos[]}`.
2. Arma contexto: system prompt + `get_state()` + historial.
3. Llama al LLM con las intake tools (tool-use).
4. Ejecuta las tool-calls, valida, persiste.
5. Devuelve `{respuesta, estado}`.

`agent/puriq/intake/prompt.py`: el system prompt con las **fases** (§5), el
catálogo de módulos/paletas, y la regla de **pedir archivos activamente**. El
estado (qué falta) se inyecta cada turno.

### Pieza 4 — Provider LLM con tool-use y visión (extensión)

`LLMProvider` hoy es text-only (`complete(prompt) -> str`). Se agrega
`complete_chat(messages, tools=None, images=None)` a los providers:
- **Bedrock Claude**: soporta tool-use + imágenes nativamente (mismo cliente
  boto3). Es el camino del pitch (AWS-native).
- **OpenAI-compatible**: soporta ambos también.
- Se agrega **sin romper** el `complete(prompt)` que usa el enriquecimiento.

En la superficie MCP esta pieza no hace falta (el LLM lo trae el cliente); solo
la necesita la web.

### Pieza 5 — Ingesta de archivos (imágenes + PDFs)

`agent/puriq/intake/ingest.py`: router por tipo.

**Imágenes** →
- Se guardan como asset (reutiliza `attach_asset`; límites y validación ya
  existen: `IMAGE_EXTS`, `MAX_ASSET_BYTES`).
- Con visión (Bedrock multimodal): el agente las **describe** para autocompletar
  `alt` y ayudar la descripción del lugar. Confirma con el usuario.
- Se asocian al place/event en contexto.

**PDFs** (folletos, historia, ficha del municipio) →
- Extraer texto (`pypdf` / `pdfplumber`, dependencia nueva).
- El texto entra como **contexto** para que el LLM pueble descripciones, Q&A,
  datos históricos. **No se publica el PDF**: se destila en contenido del
  contrato.

Cómo entran los archivos por superficie:
- **Web**: multipart (drag & drop ya existe en el paso Recursos); el endpoint
  `/api/chat` acepta adjuntos.
- **MCP**: los clientes pasan imágenes al **su** LLM para visión; para
  guardarlas como asset, `attach_asset` recibe la imagen como base64 o una ruta
  accesible al server. (Matiz: MCP transporta JSON; el binario va base64 o por
  una ruta local — a definir según el cliente.)

### Pieza 6 — Canal web (endpoint + UI de chat)

- `POST /api/chat` en `wizard/server.py`: recibe `{mensaje, archivos[]}`, corre
  el loop (pieza 3), devuelve `{respuesta, estado}`.
- UI: un panel de chat en el wizard, **al lado del esqueleto en vivo**, para que
  el usuario converse y vea el sitio armándose. Reutiliza el esqueleto ya hecho
  (`updateSkeleton`) — cada tool-call refresca el preview.

### Pieza 7 — Estado de sesión

El contrato en disco ya es el estado persistente. Falta guardar el **historial**
para continuidad: `content/.intake-session.json` (historial + fase actual). El
contrato es la fuente de verdad; la sesión solo evita empezar la charla de cero.

---

## 4. Estructura de archivos nueva

```
agent/puriq/intake/
  __init__.py
  tools.py        # Pieza 1: intake tools (núcleo compartido)
  agent.py        # Pieza 3: loop conversacional (web)
  prompt.py       # Pieza 3: system prompt + fases
  ingest.py       # Pieza 5: router de imágenes/PDF
  session.py      # Pieza 7: historial de sesión

agent/puriq/mcp/server.py     # Pieza 2: + intake tools en list/call
agent/puriq/tools/generate_content.py  # Pieza 4: + complete_chat (tool-use, visión)
agent/puriq/wizard/server.py  # Pieza 6: + POST /api/chat
agent/puriq/wizard/static/    # Pieza 6: panel de chat en la UI
docs/registro-conversacional.md   # este documento
```

---

## 5. Guion del intake (fases)

El agente sigue fases, marcando completo/falta con `get_state`:

```
Fase 1  Sitio       nombre, región, centro del mapa, idioma, (dominio/contacto)
Fase 2  Módulos     "quiero lugares y eventos" → activa places+events
Fase 3  Lugares     uno por uno: nombre, categoría, ubicación, y PIDE fotos
Fase 4  Eventos     fechas, lugar asociado
Fase 5  Marca       propone una de las 6 paletas; PIDE el logo
Fase 6  Portada     arma landing según lo cargado
Fase 7  Q&A         para el asistente; puede EXTRAER de un PDF
Fase 8  Recursos    solicita imágenes faltantes y PDFs de contexto
Fase 9  Generar     build + preview
```

Regla transversal: **el agente pide los archivos, no espera** ("¿Tenés una foto
del Cerro Rico? Mandámela y la asocio"). El estado (qué falta) guía cada
pregunta.

---

## 6. Ejemplo de un turno

```
Usuario: "Quiero mostrar el Cerro Rico y la Casa de la Moneda"
   ├─ get_state → falta sitio y lugares
   ├─ LLM → add_place(Cerro Rico), add_place(Casa de la Moneda)
   ├─ tools.py valida (build_place) + save_contract
   └─ Agente: "Agregué los dos. ¿Tenés fotos? Mandámelas.
              ¿El Cerro Rico tiene horario de visita?"

Usuario: [adjunta cerro-rico.jpg] "Sí, de 9 a 17"
   ├─ ingest.py: imagen → attach_asset(place=cerro-rico) + visión → alt
   ├─ LLM → edit_item(cerro-rico, hours="9-17")
   └─ Agente: "Foto asociada y horario cargado. ¿Seguimos con eventos?"
```

En la web, cada tool-call refresca el esqueleto lateral → el usuario ve el sitio
tomando forma mientras conversa.

---

## 7. Decisiones

**Tomadas:**
- Dos superficies: web propia **y** MCP (Claude Desktop/Kiro/etc.).
- El núcleo son intake tools compartidas; el MCP las expone para clientes
  externos.

**Pendientes (definir antes o durante):**
- LLM de la web: Bedrock (pitch AWS) vs modo local/OpenAI para prototipar sin
  credenciales.
- Alcance de visión: describir imágenes (requiere Bedrock multimodal) vs solo
  guardarlas.
- PDFs: destilar a contenido (descripciones/Q&A) vs solo contexto de la charla.
- Transporte de binarios por MCP: base64 en el argumento vs ruta local.

---

## 8. Orden de construcción sugerido

1. **Intake tools** (Pieza 1) — envolver constructores. Base de todo, bajo riesgo.
2. **MCP expone intake** (Pieza 2) — ya funciona en Claude Desktop/Kiro con el
   LLM del cliente. **Hito 1: registro conversacional por MCP, sin tocar la web.**
3. **Provider tool-use** (Pieza 4, sin visión) — para el loop propio.
4. **Loop + prompt** (Pieza 3) + **canal web** (Pieza 6) — **Hito 2: chat en la
   web con preview en vivo, text-only.**
5. **Ingesta de imágenes** (Pieza 5, reutiliza upload) — **Hito 3: recibe fotos.**
6. **Visión + PDFs** (Pieza 4 completa + Pieza 5) — **Hito 4: multimodal completo.**

Con los pasos 1-2 ya hay registro conversacional funcionando por MCP. Con 3-4,
en la web. Los archivos (5-6) son la segunda mitad.
