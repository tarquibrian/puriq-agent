# Conectar Puriq a un LLM por MCP

Puriq expone su núcleo como un **servidor MCP** (`tourism-builder`). Cualquier
cliente compatible —Claude Desktop, Kiro, Cline, Zed— puede conducir el registro
completo de un sitio **con su propio modelo**: Puriq no pone el LLM, pone las
herramientas y el guion.

Es la superficie A del registro conversacional. La superficie B es el chat web
del wizard (`POST /api/chat`), que sí trae su propio modelo. Ver
[registro-conversacional.md](registro-conversacional.md).

## Qué recibe el cliente

Al conectarse, el servidor anuncia:

- **25 tools.** Doce de *intake* (`set_site`, `configure_modules`,
  `configure_landing`, `add_place`, `add_event`, `edit_item`, `remove_item`,
  `set_brand`, `add_qa`, `attach_asset`, `get_state`, `build`), más
  `extract_pdf` y `get_guion`, y las once de pipeline y edición que ya existían
  (`scan_resources`, `import_open_data`, `generate_content`, `build_site`,
  `deploy`, `manage_articles`, `query_content`, `edit_content`,
  `delete_content`, `bulk_update`, `analyze_seo`).
- **Un recurso**, `intake://guion` (`text/markdown`): el guion conversacional por
  fases. El cliente puede cargarlo como contexto para conducir la charla en el
  orden correcto.

El guion se sirve **por partida doble**, como recurso y como la tool
`get_guion`, porque no todos los clientes leen recursos: Kiro, por ejemplo, sólo
consume tools. Si tu cliente no ve `intake://guion`, pedile que llame a
`get_guion` antes de la primera pregunta.

## Requisito previo

El servidor MCP vive detrás de un extra opcional. Instalalo una vez:

```bash
cd agent && .venv/bin/pip install -e ".[mcp]"
```

## Claude Desktop

El archivo de configuración en macOS es:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

Agregá la clave `mcpServers` (si el archivo ya existe, **fusioná** esta clave con
lo que tenga; no lo reemplaces entero):

```json
{
  "mcpServers": {
    "puriq": {
      "command": "/Users/tarquibrian/Code/Devanzire/Hackathon/agent/.venv/bin/python",
      "args": ["-m", "puriq.mcp.server"]
    }
  }
}
```

Reiniciá Claude Desktop. Puriq aparece en el ícono de herramientas.

## Kiro

Config por workspace en `.kiro/settings/mcp.json` (o global en
`~/.kiro/settings/mcp.json`), con el mismo par `command`/`args`:

```json
{
  "mcpServers": {
    "puriq": {
      "command": "/Users/tarquibrian/Code/Devanzire/Hackathon/agent/.venv/bin/python",
      "args": ["-m", "puriq.mcp.server"],
      "disabled": false,
      "autoApprove": ["get_state"]
    }
  }
}
```

`autoApprove` con `get_state` evita confirmar la lectura de estado en cada turno,
que el agente consulta constantemente. Las tools que **escriben** conviene
dejarlas pidiendo confirmación.

## Cualquier otro cliente

El transporte es **stdio**. Lo único que necesita cualquier cliente es el
comando:

```bash
/Users/tarquibrian/Code/Devanzire/Hackathon/agent/.venv/bin/python -m puriq.mcp.server
```

Dos detalles que evitan problemas:

- **Usá la ruta absoluta al Python del venv.** El paquete está instalado en modo
  editable ahí; con el `python` del sistema el módulo no resuelve.
- **No hace falta fijar el directorio de trabajo.** El servidor arranca desde
  cualquier cwd.

## Cómo se empieza la conversación

**Cada tool de intake exige el argumento `project`** con la ruta del proyecto.
A diferencia del wizard web, el servidor MCP no lee `PURIQ_PROJECT`: no hay
"proyecto actual" implícito, porque un mismo cliente puede trabajar sobre varios.

Así que el primer mensaje tiene que decir dónde está el proyecto:

> Trabajemos sobre el proyecto en `/Users/tarquibrian/Code/Devanzire/Hackathon/examples/potosi-bo`.
> Llamá a `get_guion` y ayudame a completar el registro del sitio.

Desde ahí el modelo conduce: consulta `get_state`, ve qué falta en `missing` y
pregunta por fases. Podés guiarlo en lenguaje natural ("quiero una paleta cálida
con tonos rojos") o dejar que él proponga.

Para un proyecto nuevo alcanza con un directorio vacío: las tools crean los tres
documentos del contrato en la primera escritura.

## Credenciales

No hacen falta para el registro: el LLM lo pone el cliente.

Sí las necesitan las tools que llaman a AWS —`generate_content`, `deploy`, y
`build` con `use_llm: true`—. Salen de `agent/.env`, que el servidor carga solo:
la ruta se resuelve desde la ubicación del módulo, no desde el cwd, así que
funciona aunque el cliente arranque en otro directorio. No hace falta declarar
`env` en la config del cliente.

## Si algo falla

| Síntoma | Causa habitual |
|---|---|
| El servidor no arranca | Falta el extra: `pip install -e ".[mcp]"` |
| `No module named 'puriq'` | Se usó el Python del sistema en vez del venv |
| El cliente no lista las tools | Falta reiniciar el cliente tras editar la config |
| `extract_pdf` falla | Falta el extra `pdf`: `pip install -e ".[pdf]"` |

Para verificar el servidor a mano, sin cliente:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | agent/.venv/bin/python -m puriq.mcp.server
```

Tiene que responder el `initialize` con `"name": "tourism-builder"` y después la
lista de las 25 tools.
