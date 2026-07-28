# CLAUDE.md — Contexto del proyecto Puriq

Guía para agentes de código (Claude Code / Kiro). Lee también `PROYECTO-puriq.md` (diseño completo) y `docs/SETUP.md` (configuración).

## Qué es Puriq

Agente especializado que convierte recursos turísticos dispersos (fotos, lugares, eventos, logo, Q&A) en un **sitio web profesional y mantenible**. Un solo agente reutilizable; cada sitio es solo datos. Hackathon Código Facilito × AWS, Reto 3 (Agentes especializados).

**Dos usuarios, un mismo problema:** el gobierno local que quiere mostrar su destino completo, y quien vive ahí y emprende en turismo (hospedería, tours, guía, gastronomía) y quiere promocionarse sin depender de nadie. Ambos tienen contenido y no tienen cómo publicarlo bien. El contrato no distingue entre los dos: para un destino los `places` son atractivos; para un emprendimiento, lo que ofrece (habitaciones, tours, platos). Las `categories` son libres, así que el mismo esquema cubre los dos casos sin ramificar el código.

## Invariantes de arquitectura (NO romper)

1. **El agente compone y configura módulos pre-construidos; NO genera el código de los módulos.** Los módulos viven en `template/src/modules/` y están probados.
2. **El LLM trabaja solo sobre contenido y configuración** (descripciones, SEO, traducción, qué módulos activar). Nunca escribe infraestructura ni framework.
3. **Contrato = 3 JSON**, validados contra `schemas/` en cada build: `tourism-data.json` (contenido), `site.config.json` (estructura/módulos), `theme.tokens.json` (marca). Si cambias un schema, actualiza los ejemplos y la validación.
4. **Capas de edición:** el usuario edita contenido/marca/estructura (opciones acotadas); nunca el core. Las actualizaciones del core no deben pisar los datos del usuario.
5. **El sitio generado es estático (Astro).** El LLM = Amazon Bedrock. Deploy por adaptadores (aws-amplify | s3-cloudfront | static-export).

## Estructura del repo

```
agent/            Core del agente (Python): CLI (Typer), core, tools, wizard (FastAPI), MCP
  puriq/tools/    scan_resources, import_open_data, generate_content(Bedrock), geocode, build_site, deploy
schemas/          JSON Schema de los 3 documentos del contrato
examples/         Datasets reales: potosi-bo (demo principal), oaxaca-mx, jujuy-ar
template/         Plantilla Astro; src/modules/ = catálogo de módulos; src/lib/data.ts = carga del contrato
docs/             SETUP.md y guías
```

## Comandos

```bash
# Plantilla
cd template && npm install && npm run dev        # dev server
npm run build                                     # build estático -> dist/

# Agente
cd agent && python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,mcp]"
puriq build --project ../examples/potosi-bo       # build headless
python -c "import json,jsonschema"                # validación disponible

# Validar contrato (ejemplo)
python - <<'PY'
import json, jsonschema
s=json.load(open('schemas/tourism-data.schema.json'))
d=json.load(open('examples/potosi-bo/tourism-data.json')); d.pop('$schema',None)
jsonschema.validate(d,s); print('OK')
PY
```

## Convenciones

- Python ≥3.10, tipos donde ayuden; tools con una responsabilidad clara y firma estable (se exponen también por MCP).
- IDs en kebab-case (`^[a-z0-9-]+$`). Locales ISO 639-1 (2 letras).
- Nunca commitear secretos: usar `agent/.env` (gitignored); ver `agent/.env.example`.
- La plantilla usa datos en `template/src/data/` durante el desarrollo (hoy = Potosí). En producción el agente inyecta los JSON antes de `astro build`.
- Estado actual: scaffolding + plantilla que compila y renderiza Potosí. Siguiente: implementar las tools del agente (Día 3+).
