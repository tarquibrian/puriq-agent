# Puriq · Agente (Python)

Core del agente + CLI + wizard web local + servidor MCP.

## Instalar (desarrollo)

```bash
cd agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,mcp]"
```

## Uso

```bash
puriq init                                  # wizard web local (http://localhost:4321)
puriq build --project ../examples/potosi-bo  # build headless (o oaxaca-mx, jujuy-ar)
puriq preview --project ../examples/potosi-bo
puriq deploy --project ../examples/potosi-bo --target aws-amplify
```

## Estructura

```
puriq/
  cli.py          Entrada del CLI (capa fina)
  core.py         Orquestacion del pipeline
  schemas.py      Carga + validacion del contrato (/schemas)
  tools/          scan_resources, import_open_data, generate_content,
                  geocode, build_site, deploy
  wizard/         FastAPI + UI local
  mcp/            Servidor MCP tourism-builder
```

El core es la unica fuente de logica; CLI, wizard y MCP son interfaces sobre el.
