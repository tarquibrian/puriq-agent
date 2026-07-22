# Guion de arranque en Claude Code

Prompts en orden para no perder tiempo. Cada bloque tiene un **prompt listo para pegar** y su **criterio de aceptación** (cuándo darlo por hecho). Sigue el roadmap de 7 días de `PROYECTO-puriq.md`.

> Antes de empezar: abre Claude Code en la carpeta del repo. Ya existe `CLAUDE.md` con el contexto — Claude Code lo leerá solo.

---

## Paso 0 · Puesta en marcha (10 min)

```bash
# en la raíz del repo
git init && git add . && git commit -m "chore: scaffolding inicial de Puriq"
# crea el repo público en GitHub y:
git remote add origin git@github.com:<tu-usuario>/puriq.git
git push -u origin main

# instala dependencias
cd template && npm install && npm run dev   # abre http://localhost:4321 -> deberías ver Potosí
cd ../agent && python -m venv .venv && source .venv/bin/activate && pip install -e ".[local,mcp]"
```

**Aceptación:** el sitio de Potosí se ve en el navegador y `puriq --help` responde.

---

## Paso 1 · Día 3 — Ingesta de recursos (`scan_resources`)

**Prompt:**
> Implementa `agent/puriq/tools/scan_resources.py`. Debe leer una carpeta de proyecto que contenga `places.csv` y `events.csv` (define tú las columnas mínimas y documéntalas), copiar imágenes a `assets/`, generar ids en kebab-case y devolver un dict conforme a `schemas/tourism-data.schema.json`. Valida el resultado con `puriq/schemas.py`. Añade un CSV de ejemplo en `examples/potosi-bo/` y una prueba que verifique que el dict validado coincide con lo esperado. No toques los módulos de la plantilla.

**Aceptación:** `puriq build` puede partir de CSVs y produce un `tourism-data.json` válido.

---

## Paso 2 · Día 4 — LLM con Amazon Bedrock (`generate_content`)

**Prompt:**
> Implementa `generate_content.enrich()` usando Amazon Bedrock (boto3, modelo de `PURIQ_BEDROCK_MODEL`). Debe rellenar solo descripciones vacías, SEO y traducciones a los `locales` del sitio, respetando el `voice.tone` del tema. Añade `PURIQ_LLM_MODE=local` con Ollama como fallback. Maneja errores y cachea por id para no repetir llamadas. No inventes datos fuera de los que recibe.

**Aceptación:** con credenciales AWS, un lugar sin descripción queda con texto coherente en el tono configurado; sin credenciales, el modo local funciona.

---

## Paso 3 · Día 4 — Geocoding (`geocode`)

**Prompt:**
> Implementa `geocode.fill_missing_coords()`: para lugares con `address` pero sin `coords`, geocodifica con Nominatim (OSM) respetando su rate limit, con opción de Amazon Location Service si hay credenciales. Cachea resultados. Añade prueba con un caso conocido de Potosí.

**Aceptación:** un lugar con dirección y sin coords termina con lat/lng plausibles.

---

## Paso 4 · Día 4 — Chatbot RAG (`chatweb`)

**Prompt:**
> Implementa el RAG del módulo chatweb para el MVP: indexa los `.md` de `content/faq/` con embeddings de Amazon Titan (Bedrock) en un vector store local (FAISS o sqlite-vec). Expón un endpoint en el wizard (`/api/chat`) que recupere y responda con el LLM solo con ese contexto. Conecta el frontend `template/src/modules/chatweb/Chat.astro` a ese endpoint. Documenta cómo se migraría a Bedrock Knowledge Bases.

**Aceptación:** preguntar "¿cómo llego al Salar?" devuelve una respuesta basada en el FAQ de Potosí.

---

## Paso 5 · Día 5 — Build real y wizard (`build_site` + wizard)

**Prompt:**
> Completa `build_site.assemble()` para copiar `template/`, inyectar los 3 JSON en `template/src/data/`, ejecutar `npm ci && npm run build` y dejar la salida en `<project>/dist`. Implementa `preview`. Luego dale vida al wizard (`agent/puriq/wizard/`): endpoints para elegir módulos, subir recursos y marca, lanzar el build por WebSocket con progreso, y servir el preview. UI simple pero cuidada.

**Aceptación:** `puriq init` abre el wizard, se configura Potosí de punta a punta y se ve el preview construido.

---

## Paso 6 · Día 5–6 — Deploy (`deploy`) + MCP

**Prompt:**
> Implementa el adaptador `aws-amplify` (o `s3-cloudfront`) en `deploy.py` para publicar `dist/` y devolver la URL pública; deja `static-export` como fallback. Después implementa el servidor MCP `tourism-builder` en `agent/puriq/mcp/server.py` exponiendo las tools del core (scan_resources, generate_content, build_site, deploy) con el SDK de MCP.

**Aceptación:** el sitio de Potosí queda en una URL pública; el MCP lista y ejecuta las tools desde un cliente.

---

## Paso 7 · Día 6–7 — Pulido y entregables

- README final con capturas del sitio y GIF del wizard.
- Regenera Oaxaca y Jujuy para el video ("mismo agente, 3 países").
- Diagrama de arquitectura (opcional, suma puntos) — reusa el de `PROYECTO-puriq.md`.
- Graba el video ≤5 min: problema → agente en acción → 3 sitios → cierre de impacto.

**Recuerda:** cuando carguen los créditos de **Kiro**, haz al menos un paso real en Kiro y captúralo para el video (cubre el 10% AWS/Kiro).
