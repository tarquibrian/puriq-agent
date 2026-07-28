# Puriq · Agente (Python)

Core del agente + CLI + wizard web local + servidor MCP.

Requisitos: Python >= 3.10 y Node/npm (el `build` ejecuta `astro build` real).

## Instalar (desarrollo)

```bash
cd agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,mcp]"   # extras: local (Ollama), mcp (servidor MCP)
```

## Uso

```bash
puriq init                                   # wizard web local (http://localhost:4321)
puriq collect --project ../examples/potosi-bo --resources raw
puriq build   --project ../examples/potosi-bo --no-use-llm   # o --use-llm
puriq preview --project ../examples/potosi-bo
puriq deploy  --project ../examples/potosi-bo --target aws-amplify
```

### Gestión de contenido (mismo core que el MCP)

```bash
puriq article-create "Título" --tags cultura,historia   # + article-list / article-edit / article-delete
puriq query places --category museo                      # consulta Places/Events (solo lectura)
puriq edit <id> --set name="Plaza Mayor" --set tags=a,b  # edita Place/Event por id
puriq delete <id>                                        # elimina y limpia placeId colgantes
puriq bulk-update lugares.csv --kind places              # fusiona un CSV por id
puriq seo                                                # análisis SEO local
```

## LLM configurable (`PURIQ_LLM_MODE`)

Se configura en `agent/.env` (gitignored; ver `agent/.env.example`):

- `bedrock` (default) — Amazon Bedrock (Claude). Necesita credenciales AWS + `PURIQ_BEDROCK_MODEL`.
- `local` — Ollama (`PURIQ_OLLAMA_MODEL`). Requiere el extra `[local]`.
- `openai` — API compatible con OpenAI, incluido **Azure OpenAI** (`PURIQ_OPENAI_API_KEY`,
  `PURIQ_OPENAI_BASE_URL`, `PURIQ_OPENAI_MODEL`, `PURIQ_OPENAI_API_VERSION`). Azure se
  detecta cuando `base_url` contiene `azure.com`. Compatible con Groq/OpenRouter/vLLM.

## Estructura

```
puriq/
  cli.py          Entrada del CLI (capa fina)
  core.py         Orquestacion del pipeline
  schemas.py      Carga + validacion del contrato (/schemas)
  tools/          scan_resources, import_open_data, generate_content, geocode,
                  build_site, deploy, y gestión de contenido (manage_articles,
                  query_content, edit_content, delete_content, bulk_update, analyze_seo)
  wizard/         FastAPI + UI local
  mcp/            Servidor MCP tourism-builder
```

El core es la unica fuente de logica; CLI, wizard y MCP son interfaces sobre el.
