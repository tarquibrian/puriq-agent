# Puriq

**Agente especializado que convierte los recursos turísticos dispersos de un gobierno local en un sitio web profesional y mantenible.**

> Hackathon IA Masivo Código Facilito × AWS (Kiro + AWS) · Reto 3: Agentes especializados
> CLI: `puriq` · Servidor MCP: `tourism-builder`

## El problema

Las provincias rurales y gobiernos locales con bajo presupuesto tienen un patrimonio turístico valioso (lugares, festivales, fotos, historias) pero **ninguna presencia web profesional**: contratar una agencia es caro y mantener un CMS exige un perfil técnico que no tienen. Resultado: pierden visibilidad y turismo frente a destinos mejor posicionados digitalmente.

## La solución

Puriq se instala y ejecuta localmente. A partir de los recursos que el encargado de turismo ya tiene —y opcionalmente enriqueciendo con datos abiertos (OpenStreetMap, Wikidata, Wikimedia Commons)— el agente:

1. **Recopila y estructura** los recursos (fotos, lugares, eventos, logo, Q&A) en un contrato validado.
2. **Redacta contenido** con un LLM configurable: descripciones, SEO y traducciones.
3. **Ensambla módulos** pre-construidos y probados (mapa, lugares, eventos, blog, chatweb).
4. **Aplica la identidad visual** de la provincia (colores, tipografías, tono de voz).
5. **Previsualiza y publica** el sitio (AWS Amplify / S3+CloudFront / export estático).

El agente **no escribe el código de los módulos**: compone y configura bloques probados. El LLM trabaja sobre contenido y configuración, nunca sobre infraestructura. Eso lo hace sólido, escalable y mantenible.

## Tres interfaces sobre un mismo core

Toda la lógica vive en `puriq.core`; hay tres formas de usarla, sin duplicar comportamiento:

- **CLI (`puriq`)** — flujo headless/técnico, ideal para automatización y para admins.
- **Wizard web local (`puriq init`)** — asistente de formularios por pasos para el encargado de turismo **no técnico**: nada de JSON ni de terminal.
- **Servidor MCP `tourism-builder`** — expone las tools a **cualquier cliente MCP** (Claude Desktop, Kiro, Cline...), de forma agnóstica al agente.

## Instalación

Requisitos: **Python >= 3.10** y **Node/npm** (el `build` ejecuta un `npm`/`astro build` real para generar el sitio estático).

```bash
cd agent
pip install -e .            # instalación base
# extras opcionales:
pip install -e ".[local]"   # modo LLM local con Ollama
pip install -e ".[mcp]"     # servidor MCP tourism-builder
pip install -e ".[local,mcp]"
```

El punto de entrada es el comando `puriq`.

## Quickstart

### 1) Local, sin nube (sin AWS ni LLM)

Genera un sitio completo a partir del ejemplo de Potosí, sin credenciales. Requiere Node/npm para el build.

```bash
cd agent && pip install -e .
puriq collect --project ../examples/potosi-bo --resources raw
puriq build   --project ../examples/potosi-bo --no-use-llm
puriq preview --project ../examples/potosi-bo
```

`--no-use-llm` ensambla el sitio con el contenido tal cual está en `tourism-data.json`, sin llamar a ningún modelo.

### 2) Con IA (ejemplo con Azure OpenAI)

Copia el archivo de ejemplo de variables de entorno y elige el motor de LLM:

```bash
cp agent/.env.example agent/.env
# edita agent/.env:
#   PURIQ_LLM_MODE=openai
#   PURIQ_OPENAI_API_KEY=...
#   PURIQ_OPENAI_BASE_URL=https://<tu-recurso>.openai.azure.com/
#   PURIQ_OPENAI_MODEL=<nombre-del-deployment>
#   PURIQ_OPENAI_API_VERSION=2024-10-21
puriq build --project ../examples/potosi-bo
```

Con el LLM activo, `build` rellena **solo** las descripciones y el SEO **vacíos** y los **escribe de vuelta** en `tourism-data.json`. Quedan revisables y no se regeneran en builds posteriores.

## Comandos del CLI

Pipeline principal:

| Comando | Descripción | Flags |
|---|---|---|
| `puriq init` | Abre el wizard web local | `--port` (default 4321) |
| `puriq collect` | Lee los recursos crudos (`site.json` + CSVs) y genera un `tourism-data.json` validado | `--project`, `--resources`, `--enrich` |
| `puriq build` | Valida el contrato, genera contenido y ensambla el sitio estático (requiere Node/npm) | `--project`, `--use-llm` / `--no-use-llm` |
| `puriq preview` | Sirve el sitio ya construido | `--project`, `--port` (default 4322) |
| `puriq deploy` | Publica el sitio construido | `--project`, `--target` |

Gestión de contenido (misma implementación que el MCP):

| Comando | Descripción |
|---|---|
| `puriq article-create` | Crea un artículo de blog en `/content` (`--body`, `--date`, `--tags`, `--category`, `--summary`) |
| `puriq article-list` | Lista/filtra artículos (`--date-from`, `--date-to`, `--tag`, `--category`) |
| `puriq article-edit` | Edita campos de un artículo (merge) |
| `puriq article-delete` | Elimina un artículo por id |
| `puriq query` | Consulta (solo lectura) Places o Events (`kind: places\|events`) |
| `puriq edit` | Edita un Place/Event por id con `--set clave=valor` (repetible; listas por comas) |
| `puriq delete` | Elimina un Place/Event por id (limpia referencias `placeId` colgantes) |
| `puriq bulk-update` | Fusiona un CSV de Places/Events por id (`--kind places\|events`) |
| `puriq seo` | Analiza el SEO del contenido/salida local (solo lectura) |

## LLM configurable (`PURIQ_LLM_MODE`)

El proveedor de LLM es enchufable. Se selecciona con la variable `PURIQ_LLM_MODE` (configurada en `agent/.env`, que está en `.gitignore`; usa `agent/.env.example` como referencia):

- **`bedrock`** (por defecto) — **Amazon Bedrock** (familia Claude). Necesita credenciales AWS, `PURIQ_BEDROCK_MODEL` y acceso al modelo aprobado en la consola de Bedrock.
- **`local`** — **Ollama** (sin nube). Modelo vía `PURIQ_OLLAMA_MODEL` (default `llama3.1`). Requiere el extra `[local]`.
- **`openai`** — **API compatible con OpenAI**, incluido **Azure OpenAI**. Variables:
  - `PURIQ_OPENAI_API_KEY` (requerida)
  - `PURIQ_OPENAI_BASE_URL` (default `https://api.openai.com/v1`)
  - `PURIQ_OPENAI_MODEL` (en Azure, el nombre del *deployment*)
  - `PURIQ_OPENAI_API_VERSION` (solo Azure; default `2024-10-21`)

  Azure se **detecta automáticamente** cuando la `base_url` contiene `azure.com` (cambia autenticación y forma de la URL). El mismo modo funciona con Groq, OpenRouter y servidores locales tipo vLLM/LM Studio.

## Contrato: 3 documentos JSON validados

El contrato entre el agente y el sitio son tres documentos JSON, validados contra `schemas/` en cada build:

- **`tourism-data.json`** — lugares (Places), eventos (Events), datos del sitio.
- **`site.config.json`** — módulos activos, orden, hero.
- **`theme.tokens.json`** — colores, tipografías, tono de voz.

Además:

- **`/content`** — artículos de blog en markdown + `qa.json` (FAQ del destino).
- **`/assets`** — imágenes y logo.

Módulos disponibles: `map`, `places`, `events`, `blog` (renderiza los artículos de `/content`) y `chatweb`.

## chatweb: Q&A sobre la información oficial

`chatweb` es un asistente **funcional** de preguntas y respuestas que corre **100% en el cliente**: recupera respuestas por solapamiento de tokens sobre la base de conocimiento pública del gobierno (`content/faq/*.md` + `content/qa.json`), sin red ni credenciales.

El widget deja una costura hacia el futuro: si se define `apiEndpoint` en la configuración de `chatweb`, el cliente hace `POST {question}` a ese endpoint. **Honestamente:** hoy funciona como recuperación client-side; el backend RAG gestionado (Amazon Bedrock Knowledge Bases) es trabajo futuro. El `apiEndpoint` es el único lugar donde vivirían credenciales; el cliente nunca las ve.

## Geocoding

El módulo de mapa completa las coordenadas faltantes de los Places:

- **Amazon Location Service** si `PURIQ_LOCATION_PLACE_INDEX` está definida.
- **Nominatim** (OpenStreetMap, gratuito) como opción por defecto.

## Deploy

Destinos soportados (`--target`):

- `aws-amplify` — AWS Amplify Hosting (por defecto). **Funcional.**
- `s3-cloudfront` — sube `dist/` a S3 e invalida CloudFront. **Funcional.**
- `static-export` — deja `dist/` listo para copiar a cualquier servidor. **Funcional.**
- `vercel` / `netlify` — **stubs documentados**: no implementados en este MVP (foco AWS); publica `dist/` con la CLI del proveedor.

## Arquitectura en capas (edición segura + actualizable)

```
CONTENIDO   tourism-data.json + /content + /assets   <- el usuario edita siempre
MARCA       theme.tokens.json                         <- el usuario edita siempre
ESTRUCTURA  site.config.json (módulos, orden, hero)   <- opciones acotadas
----------------------------------------------------
MÓDULOS     map, places, events, blog, chatweb        <- core, no se toca
```

Las ediciones del usuario viven en las capas de arriba; el core puede actualizarse **sin pisar** sus personalizaciones. El contrato son los tres JSON (ver `schemas/`, validados en cada build).

## Uso de AWS y Kiro

- **Amazon Bedrock** — uno de los motores de LLM soportados (redacción, SEO, traducción). Puriq también soporta **Azure/OpenAI** y **Ollama** local: no hay dependencia dura de un solo proveedor.
- **AWS Amplify Hosting / S3 + CloudFront** — publicación del sitio (adaptadores funcionales).
- **Amazon S3** — almacenamiento de assets del sitio publicado.
- **Amazon Location Service** — geocoding opcional (con fallback a Nominatim/OSM).
- **Amazon Bedrock Knowledge Bases** — backend RAG gestionado del chatweb: **trabajo futuro** (hoy la recuperación es client-side).
- **Kiro** — IDE spec-driven usado para construir el propio agente. Las specs viven en `.kiro/specs/` (`agent-tools`, `content-management`, `web-wizard`).

## Estructura del repo

```
agent/          Core del agente (Python): CLI, wizard, MCP, tools (puriq/)
schemas/        Contrato: JSON Schema de los documentos (tourism-data, site-config, theme-tokens, article)
examples/       Datasets de ejemplo multi-región: potosi-bo, oaxaca-mx, jujuy-ar (+ raw/ y content/faq)
template/       Plantilla Astro que consume el contrato y compone los módulos
docs/           Documentación adicional (SETUP, arranque)
.kiro/specs/    Specs spec-driven usadas para construir el agente
PROYECTO-puriq.md   Documento técnico completo (diseño y roadmap)
```

## Estado

Core + las **tres interfaces** (CLI, wizard web y MCP) **implementados**. Pipeline validado de punta a punta: `build` con IA real → sitio estático con mapa, lugares, eventos, blog y chatweb.

Pendientes honestos:

- Render i18n en la plantilla (las traducciones ya se generan y guardan bajo la clave companion `i18n`).
- Backend RAG gestionado del chatweb (Amazon Bedrock Knowledge Bases).
- Adaptadores de deploy para Vercel/Netlify (hoy stubs).
- Panel de administración.

Construido spec-driven en Kiro (ver `.kiro/specs/`).

## Licencia

MIT — ver [LICENSE](LICENSE).

Las fuentes tipográficas incluidas en `template/public/fonts/` están bajo la
SIL Open Font License 1.1 (ver `template/public/fonts/README.md`).
