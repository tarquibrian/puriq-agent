# Configuración

Qué necesitás según hasta dónde quieras llegar. **Nada de esto hace falta para
ver Puriq funcionando**: `./start.sh --demo` genera un sitio completo sin
credenciales de ningún tipo.

## 1. Requisitos

| Herramienta | Para qué | Obligatorio |
|---|---|---|
| **Python ≥ 3.10** | el agente | sí |
| **Node.js ≥ 18** + npm | el `build` corre un `astro build` real | sí, para generar el sitio |
| Git | clonar y versionar tu proyecto | sí |

`./start.sh` comprueba las dos primeras al arrancar y te dice cuál falta.

## 2. Sin credenciales

Estos caminos funcionan tal cual, sin configurar nada:

```bash
./start.sh --demo    # genera y sirve el sitio de ejemplo
./start.sh           # abre el asistente: formularios + vista previa
./start.sh --mcp     # conecta Puriq a Claude Desktop / Kiro (usan SU modelo)
```

La vía MCP es la más interesante para probar el registro conversacional: quien
conversa usa su propia suscripción y Puriq no ve ninguna credencial.

## 3. Con un LLM propio

Sólo hace falta si querés el **chat integrado del asistente web** o enriquecer
contenido con `puriq build` (sin `--no-use-llm`).

```bash
cp agent/.env.example agent/.env
```

Elegí el motor con `PURIQ_LLM_MODE`:

### `openai` — OpenAI, Azure, Groq, OpenRouter, vLLM…

```
PURIQ_LLM_MODE=openai
PURIQ_OPENAI_API_KEY=...
PURIQ_OPENAI_BASE_URL=https://api.openai.com/v1
PURIQ_OPENAI_MODEL=gpt-4o
```

Azure se detecta solo cuando la `base_url` contiene `azure.com`; ahí
`PURIQ_OPENAI_MODEL` es el nombre del **deployment** y hace falta
`PURIQ_OPENAI_API_VERSION`.

### `bedrock` — Amazon Bedrock

```
PURIQ_LLM_MODE=bedrock
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
PURIQ_BEDROCK_MODEL=anthropic.claude-3-5-sonnet-20240620-v1:0
```

Requiere acceso al modelo aprobado en la consola de Bedrock (Model access), que
puede tardar en habilitarse.

### `local` — Ollama

```
PURIQ_LLM_MODE=local
PURIQ_OLLAMA_MODEL=llama3.1
```

Requiere el extra `[local]`. **Es text-only**: sirve para enriquecer contenido,
pero no para el chat conversacional, que necesita tool-use.

> `agent/.env` está en `.gitignore` y nunca se versiona. `agent/.env.example`
> sólo tiene marcadores: cada usuario pone sus propias claves.

## 4. Publicar el sitio

`static-export` no necesita nada: deja `dist/` listo para copiar a cualquier
hosting.

Para AWS, credenciales más las variables del destino:

```bash
# aws-amplify
PURIQ_AMPLIFY_APP_ID=...
PURIQ_AMPLIFY_BRANCH=main

# s3-cloudfront
PURIQ_S3_BUCKET=...
PURIQ_CLOUDFRONT_DISTRIBUTION_ID=...
```

Los adaptadores de AWS están implementados y cubiertos por pruebas contra
dobles, pero **no se han ejercitado contra AWS real**. Probalos con tiempo antes
de depender de ellos.

## 5. Opcionales

- **Geocoding**: usa Amazon Location Service si definís
  `PURIQ_LOCATION_PLACE_INDEX`; si no, Nominatim (OpenStreetMap), que es gratuito.
- **Extras de Python**: `[mcp]` para el servidor MCP (lo instala `./start.sh`),
  `[pdf]` para leer PDFs de contexto, `[local]` para Ollama, `[test]` para la
  suite.

## 6. Desarrollo

```bash
cd agent && .venv/bin/pip install -e ".[test]" && .venv/bin/python -m pytest -q
cd template && npm install && npm run dev
```
