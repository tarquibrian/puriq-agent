# Arquitectura de Puriq

Documento técnico: cómo está construido Puriq y por qué. Para empezar a usarlo,
ver el [README](../README.md); para conectarlo a un cliente MCP,
[mcp-clientes.md](mcp-clientes.md).

## La idea central

Puriq **no genera código**. Compone y configura módulos pre-construidos y
probados. El LLM trabaja sobre **contenido y configuración** —descripciones, SEO,
qué módulos activar, qué paleta— y nunca sobre infraestructura ni framework.

Esa restricción es lo que hace el resultado predecible: un modelo no puede
romper el layout, introducir una vulnerabilidad ni dejar el sitio sin compilar,
porque no escribe la parte que podría romperse.

## El contrato: tres documentos JSON

Todo gira alrededor de un contrato validado contra `schemas/` en cada build.

| Documento | Qué define |
|---|---|
| `tourism-data.json` | lugares, eventos, datos del sitio — **el contenido** |
| `site.config.json` | módulos activos y su orden, portada, contacto, deploy — **la estructura** |
| `theme.tokens.json` | colores, tipografías, tono de voz — **la marca** |

Más `content/` (artículos en markdown y `qa.json`) y `assets/` (imágenes, logo).

**El contrato es la única fuente de verdad.** Todo lo demás son superficies que
lo escriben o lo leen. Dos consecuencias prácticas:

- Cambiar la plantilla no toca al agente, y al revés.
- Varias superficies pueden escribir el mismo proyecto a la vez.

El mismo esquema cubre a un destino y a un emprendimiento sin ramificar el
código: para un municipio los `places` son atractivos; para una hospedería son
sus habitaciones. Las `categories` son libres.

## Capas de edición

```
┌──────────────────────────────────────────────────────────────┐
│ CONTENIDO   tourism-data.json + content/ + assets/            │ ← el usuario edita siempre
│ MARCA       theme.tokens.json                                 │ ← el usuario edita siempre
│ ESTRUCTURA  site.config.json (módulos, orden, portada)        │ ← edita, opciones acotadas
├──────────────────────────────────────────────────────────────┤
│ MÓDULOS     template/src/modules/                             │ ← core, no se toca
│ MOTOR       agente (Python) + plantilla (Astro)               │ ← core, no se toca
└──────────────────────────────────────────────────────────────┘
```

Las ediciones del usuario viven arriba; el core, abajo y versionado. Por eso
actualizar Puriq **no pisa** el contenido, los colores ni la estructura que el
usuario definió.

La libertad de estructura es **acotada, no infinita**: se ofrecen módulos y
secciones que se activan y ordenan, no un editor libre que pueda romper el
diseño. La escotilla de escape existe igual: el sitio generado es un proyecto
Astro normal que el usuario posee, y alguien técnico puede editarlo directamente.

## El flujo

```
  Recursos del usuario ─┐
  (fotos, lugares,      │      ┌──────────────────┐      ┌─────────────────┐
   eventos, logo, Q&A)  ├──►   │ Agente (Python)  │──►   │  CONTRATO       │──►  Plantilla Astro ──► dist/
                        │      │ · valida         │      │  3 JSON + assets│     · activa módulos
  Datos abiertos ───────┘      │ · redacta (LLM)  │      └─────────────────┘     · aplica tema
  (OSM, Wikidata)              │ · geocodifica    │              ▲
                               └──────────────────┘        schemas/ valida
```

El agente nunca produce HTML: produce **datos limpios y validados**.

## Tres interfaces sobre un mismo núcleo

Toda la lógica vive en `puriq.core` y en `puriq/tools/`; las interfaces son
envoltorios finos que no duplican comportamiento.

| Interfaz | Para quién | Quién pone el modelo |
|---|---|---|
| **CLI** `puriq` | técnico, automatización, CI | opcional |
| **Wizard web** `puriq init` | no técnico: formularios **o** chat | Puriq |
| **MCP** `tourism-builder` | Claude Desktop, Kiro, Cline, Zed | **el cliente** |

La superficie MCP es la que hace a Puriq agnóstico del modelo: expone 25 tools y
el guion del registro, y quien conversa usa su propia suscripción. Puriq no pide
ninguna credencial para eso.

### El núcleo conversacional

`puriq/intake/` implementa el registro por conversación **sin transporte**:
recibe argumentos y devuelve estado, sin saber de HTTP ni de stdio. Las dos
superficies —el chat del wizard y el servidor MCP— consumen las mismas
funciones, por eso el agente se comporta igual en las dos.

```
puriq/intake/
  tools.py     las intake tools + el guion por fases (fuente única)
  agent.py     el loop conversacional del wizard
  prompt.py    system prompt: embebe el guion, no lo reescribe
  ingest.py    router de imágenes y PDFs
  session.py   historial, para no empezar la charla de cero
```

### Coordinación entre superficies

El wizard y un cliente MCP pueden escribir el mismo proyecto simultáneamente. El
wizard consulta `GET /api/version` —un `stat` por documento, barato— y ante un
cambio ajeno refresca la vista previa solo, mientras ofrece recargar el
formulario en vez de repintarlo encima de lo que el usuario esté escribiendo.

## La plantilla

Astro, estático. Cinco módulos y siete secciones de portada que se activan y
ordenan desde `site.config.json`.

| Módulos | Secciones de portada |
|---|---|
| `map`, `places`, `events`, `blog`, `chatweb` | `hero`, `features`, `stats`, `gallery`, `testimonials`, `faq`, `cta` |

**Sistema de diseño por tokens.** `theme.tokens.json` se resuelve a variables CSS
en `Base.astro`; ningún componente fija un color. La regla de uso está escrita
ahí mismo: `--color-primary` para todo lo accionable, `--color-accent` para el
énfasis que no se toca. El modo oscuro se deriva de los mismos colores de marca.

**Todo se auto-hospeda**: tipografías y Leaflet viajan con el sitio. Sin CDNs de
terceros: privacidad para el visitante y un sitio que funciona sin depender de
que otro servicio siga en pie.

## El asistente del sitio (`chatweb`)

Dos modos, según haya o no un `apiEndpoint` configurado:

- **Sin endpoint** — recuperación client-side por solapamiento de tokens sobre
  el FAQ. No necesita red ni credenciales, pero no entiende reformulaciones.
- **Con endpoint** — un servicio redacta la respuesta con el FAQ completo como
  contexto (`puriq/faq_chat.py`).

**No hay vector store, y es deliberado.** La base de conocimiento de un destino
son decenas de pares Q&A; la de Potosí ocupa ~420 tokens y entra entera en el
contexto del modelo. El problema no era encontrar el fragmento sino redactarlo.
Un índice vectorial agregaría infraestructura con costo continuo para un problema
que a esta escala no existe. Si el corpus creciera, la costura para meter
recuperación previa es el argumento `knowledge`.

El prompt prohíbe completar con conocimiento propio y da una salida explícita
para decir que no sabe: el asistente habla en nombre de un municipio o de un
negocio, e inventar un horario desinforma a alguien que va a actuar en base a eso.

## LLM enchufable

El proveedor se elige con `PURIQ_LLM_MODE`:

- **`bedrock`** — Amazon Bedrock (familia Claude).
- **`openai`** — API compatible con OpenAI, incluido Azure (detectado por la URL),
  Groq, OpenRouter, vLLM o LM Studio.
- **`local`** — Ollama, sin nube. Text-only: no admite tool-use, así que sirve
  para enriquecer contenido pero no para conversar.

## Publicación

Adaptadores seleccionables con `--target`:

| Destino | Estado |
|---|---|
| `static-export` | funcional — deja `dist/` listo para cualquier hosting |
| `aws-amplify` | implementado; probado contra dobles, no contra AWS real |
| `s3-cloudfront` | implementado; probado contra dobles, no contra AWS real |
| `vercel` / `netlify` | stubs documentados |

## Enriquecimiento con datos abiertos

Opcional, y siempre como sugerencia revisable: OpenStreetMap y Wikidata para
completar lugares, Wikimedia Commons para imágenes con licencia. El geocoding usa
Amazon Location Service si está configurado, y Nominatim (OSM) si no.

## Estado

Las tres interfaces implementadas y validadas de punta a punta. Registro
conversacional completo en las dos superficies, con ingesta de imágenes y PDFs.

**Pendientes declarados:**

- Render i18n en la plantilla (las traducciones se generan y guardan, no se muestran).
- Endpoint del asistente desplegado (hoy corre local).
- Adaptadores Vercel/Netlify.
- Panel de administración.
