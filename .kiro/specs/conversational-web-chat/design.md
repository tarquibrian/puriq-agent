# Documento de Diseño

## Overview

Este diseño cubre el **Hito 2** de la capa conversacional de Puriq: el **chat en la web con
previsualización en vivo**, en modo **text-only**. Implementa las Piezas 3, 4 (sin visión), 6 y 7 de
`docs/registro-conversacional.md` construyendo la **superficie B** (web) sobre el mismo núcleo de
intake que el Hito 1 (spec `conversational-intake-mcp`) ya expone por MCP (superficie A).

El principio rector es el del Hito 1: **una sola implementación compartida**. El núcleo de intake
(`agent/puriq/intake/tools.py`) ya declara las 12 acciones tipadas, `INTAKE_TOOL_SPECS`,
`INTAKE_TOOL_NAMES`, `INTAKE_GUION`, `get_state` y `run_intake_tool` (despacho con traducción de
errores redactada). El chat web **no reimplementa ninguna acción**: es otra superficie que conduce la
conversación con su propio LLM y despacha las tool-calls por `run_intake_tool`, igual que el cliente
MCP externo despacha por `call_tool`.

Las piezas de esta fase:

- **Pieza 3 — Loop del agente + guion (superficie B):**
  - `agent/puriq/intake/agent.py` (`Chat_Agent`): el bucle por turno. Arma el contexto (system prompt
    + `get_state` + historial), invoca `complete_chat` con las intake tools, despacha las tool-calls
    por `run_intake_tool` inyectando `project`, respeta un límite finito de rondas (default 8) y
    devuelve `{respuesta, estado}`.
  - `agent/puriq/intake/prompt.py` (`Intake_Prompt`): el system prompt con las fases 1–9 (alineadas a
    `INTAKE_GUION`), el catálogo de módulos y el catálogo de paletas, la regla de pedir archivos, y la
    inyección del `Contract_State` vigente cada turno.
- **Pieza 4 (text-only, SIN visión) — Provider con tool-use:** se extiende el `LLMProvider` de
  `agent/puriq/tools/generate_content.py` con `complete_chat(messages, tools=None)`, **sin romper**
  `complete(prompt)`. Abstrae el tool-use nativo de Bedrock Claude (boto3) y el function calling
  compatible con OpenAI, seleccionable por `get_provider()` / `PURIQ_LLM_MODE`.
- **Pieza 6 — Canal web:** `POST /api/chat` en `agent/puriq/wizard/server.py` (recibe
  `{mensaje, archivos[]}`, corre un turno del `Chat_Agent`, devuelve `{respuesta, estado}`) y un
  `Chat_Panel` en la UI del wizard al lado del `Live_Preview`, que refresca el esqueleto con
  `updateSkeleton` a partir del `Contract_State`.
- **Pieza 7 — Estado de sesión:** `content/.intake-session.json` (`Session_Store`, en
  `agent/puriq/intake/session.py`) guarda el historial y la fase, redactado, para continuidad. El
  contrato en disco sigue siendo la fuente de verdad.

### Alcance

Dentro: `intake/agent.py`, `intake/prompt.py`, `intake/session.py`, el método `complete_chat` en
`generate_content.py`, el endpoint `POST /api/chat` y el `Chat_Panel` en `wizard/static/`. Fuera
(Fase 3 / Hito 4, declarado en los requisitos): visión/multimodal, extracción de texto de PDFs,
transmisión de bytes de archivos al LLM y reimplementación del núcleo de intake. En esta fase el chat
es **text-only**: `archivos[]` transporta **referencias** a assets ya subidos por el flujo existente
(`POST /api/assets`), no binarios.

## Investigación y hallazgos que informan el diseño

Antes de diseñar se leyó el código real que esta fase debe reutilizar y extender. Los hallazgos que
condicionan el diseño:

1. **El núcleo de intake ya es la única implementación compartida.** `intake/tools.py` expone
   `INTAKE_TOOL_SPECS` (cada spec con `name`, `description`, `inputSchema` JSON Schema **puro** y
   `handler` que adapta `arguments → función`), `INTAKE_TOOL_NAMES`, `INTAKE_GUION` y
   `run_intake_tool(name, arguments) -> dict | str`. `run_intake_tool` localiza el handler, lo
   ejecuta y **traduce cualquier excepción** con `wizard_error_response(exc, documento=...)` (ya
   redactado y accionable); una tool desconocida NO propaga: devuelve un `ValueError` accionable que
   lista las tools disponibles. **El Chat_Agent despacha exactamente por aquí** (Req 5.1), sin tocar
   la lógica de cada tool. `get_state(project)` es de solo lectura y devuelve los tres documentos +
   `missing`, todo redactado (la brújula del intake).

2. **Cada `inputSchema` ya incluye `project` (string, requerido) y `additionalProperties: false`.**
   Los handlers hacen `Path(arguments["project"])`. Esto es clave para la Pieza 3: el LLM no debería
   tener que proveer `project` (es una ruta local del servidor), por lo que el Chat_Agent **inyecta**
   `project` en los `arguments` de cada tool-call antes de despachar (Req 1.8). Como consecuencia, el
   `Chat_Agent` puede **omitir `project`** del esquema que expone al LLM (o dejarlo y sobreescribirlo);
   el diseño lo omite del esquema presentado y lo inyecta al despachar, para no confundir al modelo.

3. **`LLMProvider` es hoy un `Protocol` text-only.** Define `complete(prompt: str) -> str`.
   `BedrockProvider` usa `invoke_model` con el cuerpo Messages de Claude
   (`anthropic_version` + `messages`, `max_tokens`) y extrae el texto de los bloques `content` de tipo
   `text`. `OpenAICompatibleProvider` hace `POST <base>/chat/completions` (o el estilo Azure) con
   `httpx`, lee la clave con `get_env("PURIQ_OPENAI_API_KEY", required=True, secret=True)` y extrae
   `choices[0].message.content`. `OllamaProvider` usa `ollama.generate`. `get_provider()` resuelve por
   `PURIQ_LLM_MODE` (`local`/`openai`/`bedrock`). **`complete_chat` se agrega a este protocolo y a los
   tres providers sin tocar `complete`** (Req 3.2): reutiliza el mismo cliente/endpoint y el mismo
   acceso a credenciales, de modo que la redacción de secretos siga valiendo.

4. **El wizard REST ya tiene el patrón exacto del endpoint y la red de seguridad transversal.**
   `wizard/server.py` resuelve la raíz con `project_root()` (env `PURIQ_PROJECT` o cwd), define
   endpoints con modelos pydantic, aplica `redact_value` a toda respuesta, traduce errores con
   `wizard_error_response`, y registra manejadores transversales (`RequestValidationError` → 422
   redactado; `Exception` → 500 redactado sin traza). `serve()` escucha solo en `127.0.0.1`. El
   `POST /api/chat` se **añade con el mismo patrón** (Req 6, 11): modelo pydantic
   `{mensaje, archivos}`, delega en el `Chat_Agent`, responde `redact_value({respuesta, estado})` y
   deja que la red de seguridad capture cualquier error inesperado.

5. **El upload de assets ya existe y es independiente del chat.** `POST /api/assets` recibe multipart,
   valida tamaño/extensión, normaliza el nombre y lo asocia opcionalmente a un Place/Event o al logo.
   El chat text-only **no transporta binarios**: `archivos[]` son rutas relativas bajo `assets/` ya
   subidas por ese flujo (Req 8). El Chat_Agent las expone como **texto** en el contexto del turno; la
   asociación a un Place/Event se hace, cuando la charla lo identifica, con la intake tool
   `attach_asset` ya existente (que en el chat usará `source_path`/una referencia, sin bytes nuevos).

6. **La UI del wizard ya tiene el esqueleto en vivo reutilizable.** `app.js` mantiene
   `state.server["tourism-data"|"site-config"|"theme-tokens"]` y `updateSkeleton()` repinta el
   preview leyendo ese estado. `apiRequest(method, url, {json})` centraliza el fetch y normaliza
   errores `{causa, acción}` / `{documento, campo, sugerencia}`. **El Chat_Panel reutiliza ambos**:
   tras cada `Chat_Response`, vuelca `estado` (que trae los tres documentos) en `state.server` y llama
   `updateSkeleton()` (Req 7.3), sin duplicar el renderizado del preview.

7. **`config`/`errors` son la única fuente de verdad de secretos y mensajes.** `config.redact_value`
   enmascara recursivamente; `get_env(..., secret=True)` registra una variable como secreto;
   `wizard_error_response(exc, documento=None)` traduce y **siempre** redacta. El diseño no introduce
   mensajes de error ad hoc: reutiliza estas piezas en el endpoint y en el `Session_Store` (Req 9.3,
   11.2, 11.4).

## Architecture

El núcleo sigue siendo `intake/tools.py`. El Hito 2 agrega la **superficie B** (web): un loop propio
(`Chat_Agent`) que trae el LLM (`complete_chat`) y conduce la conversación por fases
(`Intake_Prompt`), persiste la continuidad (`Session_Store`) y se expone por `POST /api/chat` con un
panel de chat junto al preview en vivo.

```mermaid
graph TD
    subgraph Navegador
        CP[Chat_Panel]
        LP[Live_Preview + updateSkeleton]
        AS[Upload de assets\ndrag & drop -> POST /api/assets]
    end

    subgraph "wizard/server.py (Pieza 6)"
        EP[POST /api/chat\nproject_root + redact + wizard_error_response]
        UP[POST /api/assets\n(ya existe)]
    end

    subgraph "intake/agent.py (Pieza 3)"
        AG[Chat_Agent.run_turn\nbucle por turno + limite de rondas]
    end

    subgraph "intake/prompt.py (Pieza 3)"
        PR[Intake_Prompt\nfases 1-9 + catalogos + estado inyectado]
    end

    subgraph "intake/session.py (Pieza 7)"
        SS[Session_Store\ncontent/.intake-session.json redactado]
    end

    subgraph "tools/generate_content.py (Pieza 4)"
        CC[LLMProvider.complete_chat\nBedrock tool-use / OpenAI function calling]
        GP[get_provider / PURIQ_LLM_MODE]
    end

    subgraph "intake/tools.py (nucleo Hito 1, reutilizado)"
        RIT[run_intake_tool]
        GS[get_state]
        SPEC[INTAKE_TOOL_SPECS / INTAKE_TOOL_NAMES / INTAKE_GUION]
    end

    CP -->|fetch mensaje, archivos| EP
    EP --> AG
    AG --> PR
    AG --> SS
    AG --> GP --> CC
    AG -->|despacha tool-calls\ncon project inyectado| RIT
    AG -->|estado del turno| GS
    PR -.usa.-> SPEC
    PR -.usa.-> GS
    CC -.traduce.-> SPEC
    EP -->|respuesta, estado| CP
    CP -->|vuelca estado en state.server| LP
    AS --> UP
    RIT -->|read/write atomico| DOCS[(3 JSON del contrato)]
    SS -->|read/write atomico| SESS[(content/.intake-session.json)]
```

### Flujo de un turno del Chat_Agent

```mermaid
sequenceDiagram
    participant U as Chat_Panel
    participant E as POST /api/chat
    participant A as Chat_Agent
    participant S as Session_Store
    participant L as LLMProvider.complete_chat
    participant T as run_intake_tool
    participant G as get_state

    U->>E: {mensaje, archivos[]}
    E->>A: run_turn(ChatRequest)
    A->>S: load_session(project)  (tolerante: vacio si falta/corrupto)
    S-->>A: {history, phase}
    A->>G: get_state(project)
    G-->>A: Contract_State (redactado)
    A->>A: build messages = system(Intake_Prompt + estado) + history + user(mensaje + refs)
    loop hasta max_tool_rounds (default 8)
        A->>L: complete_chat(messages, tools=INTAKE_TOOL_SPECS)
        alt el modelo pide Tool_Calls
            L-->>A: [Tool_Call(name, args), ...]
            loop cada Tool_Call
                A->>A: args["project"] = project  (inyeccion, Req 1.8)
                A->>T: run_intake_tool(name, args)
                T-->>A: dict estado  |  error accionable redactado
                A->>A: append Tool_Result al historial de mensajes
            end
        else el modelo responde texto sin Tool_Calls
            L-->>A: texto del asistente
            Note over A: fin del bucle
        end
    end
    A->>G: get_state(project)  (estado final tras las Tool_Calls)
    G-->>A: Contract_State
    A->>S: save_session(project, history, phase)  (redactado, atomico)
    A-->>E: ChatResponse{respuesta, estado}
    E-->>U: redact_value({respuesta, estado})
    U->>U: state.server = estado; updateSkeleton()
```

### Decisiones de diseño

- **DD-1 (el Chat_Agent es una superficie, no una reimplementación).** El bucle despacha **toda**
  tool-call por `run_intake_tool` del núcleo (Req 5.1) y toma el estado de `get_state` (Req 1.5, 5.5).
  No conoce la lógica de ninguna intake tool ni valida el contrato: hereda validación, atomicidad,
  integridad referencial y traducción de errores del Hito 1. *Alternativa descartada:* que el agente
  llame a las funciones tipadas (`add_place`, etc.) directamente — perdería la traducción de errores
  redactada y accionable de `run_intake_tool`, divergiendo del comportamiento del MCP.

- **DD-2 (inyección de `project`, el LLM no lo ve).** Cada `inputSchema` requiere `project`, pero es
  una ruta local que el modelo no debe inventar. El Chat_Agent **elimina `project`** del esquema que
  presenta al LLM y lo **inyecta** en `arguments` antes de despachar (Req 1.8). Así el modelo razona
  solo sobre datos de negocio y la ruta siempre es el `Project_Root` correcto, sea cual sea lo que el
  modelo intente pasar. *Alternativa descartada:* dejar que el LLM provea `project` — expone rutas del
  servidor al modelo y habilita que apunte a un proyecto ajeno.

- **DD-3 (`complete_chat` con un modelo de mensajes neutral, traducido por proveedor).** Se define un
  modelo de datos común (`Message`, `ToolCall`, `ToolResult`, `ChatResult`) independiente del
  proveedor, en `generate_content.py` (junto al protocolo). Cada proveedor **traduce**: Bedrock al
  tool-use nativo de Claude (bloques `tool_use`/`tool_result`, `stop_reason == "tool_use"`) y el
  compatible con OpenAI al function calling (`tools=[{type:function,...}]`,
  `message.tool_calls[].function.{name,arguments}`). El `inputSchema` puro de cada spec se mapea a
  `input_schema` (Claude) o `parameters` (OpenAI), descartando `handler`. `complete(prompt)` queda
  intacto (Req 3.2). *Alternativa descartada:* acoplar el modelo de mensajes al formato de un proveedor
  — obligaría a traducir en el agente y rompería la simetría entre backends.

- **DD-4 (proveedor configurable, degradación accionable).** `get_provider()`/`PURIQ_LLM_MODE` ya
  eligen el backend (Req 4.1). `complete_chat` se implementa para Bedrock (Req 4.2) y OpenAI-compatible
  (Req 4.3, admite endpoint local). Un backend sin tool-use (Ollama) implementa `complete_chat`
  lanzando un error accionable que **nombra `PURIQ_LLM_MODE`** y los modos con tool-use (Req 4.4). Una
  credencial faltante propaga `MissingEnvVarError` que nombra la variable sin exponer su valor
  (Req 4.5), reutilizando `get_env(..., required=True, secret=True)`.

- **DD-5 (redacción y traducción en el borde web, reutilizando el Hito 1).** El endpoint aplica
  `redact_value` a la `Chat_Response` (Req 11.2) y traduce cualquier error con
  `wizard_error_response` (Req 6.4, 11.4). Las tool-calls ya vuelven redactadas de `run_intake_tool`.
  El `Session_Store` aplica `redact_value` al historial y a la fase antes de escribir (Req 9.3). Se
  apoya además en la red de seguridad transversal ya registrada en `server.py`.

- **DD-6 (sesión como continuidad, contrato como verdad).** El `Session_Store` guarda solo el
  historial y la fase para no empezar la charla de cero (Req 9, 10). Los `Faltantes` **siempre** se
  derivan de `get_state` sobre el contrato en disco, nunca del historial (Req 10.3). Una sesión
  ausente o corrupta se trata como historial vacío sin fallar (Req 10.2): la conversación arranca
  limpia pero el contrato (y por ende el preview) refleja lo ya cargado.

- **DD-7 (archivos como referencias textuales).** `archivos[]` son rutas relativas bajo `assets/`
  (Req 8.1). El Chat_Agent las inserta como **texto** en el mensaje de usuario del turno (p. ej. "El
  usuario adjuntó estas imágenes ya subidas: assets/cerro-rico.jpg"), sin leer bytes ni extraer PDFs
  (Req 8.2, 8.3). Cuando la charla identifica el destino, el modelo llama `attach_asset` (Req 8.4),
  que ya existe. *Alternativa descartada:* enviar bytes al LLM — es la Pieza 5 (visión), fuera de
  alcance.

## Components and Interfaces

### 1. `agent/puriq/intake/session.py` (Pieza 7): `Session_Store`

Módulo de E/S sin FastAPI (misma frontera que `asset_store`/`qa_store` del Hito 1). Persiste el
historial y la fase en `content/.intake-session.json`, redactado y de forma atómica.

```python
@dataclass
class Session:
    history: list[dict]      # mensajes serializables (ver Data Models)
    phase: str | None        # fase del Intake_Guion en curso (1..9), o None

_SESSION_RELPATH = "content/.intake-session.json"

def load_session(project: Path) -> Session:
    """Carga la sesión previa; tolerante a ausencia/corrupción (Req 10.1, 10.2).

    Si el archivo no existe o su JSON no es legible/estructurado, devuelve
    Session(history=[], phase=None) SIN fallar. Nunca deriva `missing` de aquí.
    """

def save_session(project: Path, history: list[dict], phase: str | None) -> None:
    """Persiste historial + fase redactados y de forma atómica (Req 9.1, 9.3).

    Aplica config.redact_value al historial y a la fase antes de escribir, crea
    content/ si falta y escribe con temp + os.replace (mismo patrón atómico que
    contracts.save_contract).
    """
```

### 2. `agent/puriq/tools/generate_content.py` (Pieza 4): modelo de mensajes + `complete_chat`

Se agrega, junto al `Protocol` `LLMProvider`, el modelo de datos neutral y el método `complete_chat`
en el protocolo y en los tres providers. `complete(prompt)` **no cambia**.

```python
@dataclass
class ToolCall:
    id: str                  # id opaco del proveedor (para casar el resultado)
    name: str                # nombre de la intake tool solicitada
    arguments: dict          # argumentos ya deserializados a dict

@dataclass
class ToolResult:
    tool_call_id: str        # id de la ToolCall que responde
    content: str             # resultado serializado a JSON (texto para el modelo)

@dataclass
class Message:
    role: str                # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None   # solo en mensajes "assistant"
    tool_result: ToolResult | None = None       # solo en mensajes "tool"

@dataclass
class ChatResult:
    text: str | None                 # texto del asistente (si no pide tools)
    tool_calls: list[ToolCall]       # tool-calls solicitadas (vacío si texto final)
    assistant_message: Message       # el turno del asistente, para anexar al historial
```

Extensión del protocolo:

```python
class LLMProvider(Protocol):
    def complete(self, prompt: str) -> str: ...
    def complete_chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> ChatResult: ...
```

- **`BedrockProvider.complete_chat`** (Req 3.1, 3.3, 3.4, 4.2): traduce `messages` al cuerpo Messages
  de Claude (system aparte, `user`/`assistant`, bloques `tool_use`/`tool_result`), mapea cada spec de
  `tools` a `{"name", "description", "input_schema": <inputSchema sin project ni handler>}`, e invoca
  `invoke_model` con `tools` + `tool_choice: {"type":"auto"}`. Si `stop_reason == "tool_use"`, extrae
  los bloques `tool_use` como `ToolCall(id, name, input)`; si no, concatena los bloques `text` como
  `ChatResult.text`.
- **`OpenAICompatibleProvider.complete_chat`** (Req 3.1, 3.3, 3.4, 4.3): traduce `messages` a la forma
  OpenAI (`role`/`content`, `assistant.tool_calls`, `role:"tool"` con `tool_call_id`), mapea cada spec
  a `{"type":"function","function":{"name","description","parameters": <inputSchema sin project>}}`, y
  hace `POST .../chat/completions` con `tools`. Si la respuesta trae `message.tool_calls`, parsea cada
  una a `ToolCall(id, name, json.loads(arguments))`; si no, usa `message.content` como texto. Reusa el
  endpoint local (base_url) para prototipar sin AWS.
- **`OllamaProvider.complete_chat`** (Req 4.4): lanza un error accionable que nombra `PURIQ_LLM_MODE`
  y los modos con tool-use (`bedrock`, `openai`). No intenta emular tool-use.
- **Traducción de tools (helper compartido)**: `_tools_to_bedrock(specs)` y `_tools_to_openai(specs)`
  parten de `INTAKE_TOOL_SPECS`, descartan `handler`, y **quitan `project`** de `properties` y de
  `required` del `inputSchema` (DD-2), de modo que el LLM no lo vea.
- **Credenciales (Req 4.5)**: la clave del proveedor se sigue leyendo con `get_env(..., required=True,
  secret=True)`; ausente → `MissingEnvVarError` que la nombra sin exponer su valor; registrada como
  secreto para `redact`. Text-only (Req 3.6): `complete_chat` solo maneja texto en `content`; no acepta
  ni transmite bytes de imágenes.

### 3. `agent/puriq/intake/prompt.py` (Pieza 3): `Intake_Prompt`

```python
# Catálogo de paletas de marca disponibles (espejo de las 6 paletas de la UI),
# para que el LLM proponga opciones válidas (Req 2.2).
INTAKE_PALETTES: list[dict] = [ {"name": "Oceano", ...}, ... ]

def build_system_prompt(contract_state: dict) -> str:
    """Construye el system prompt del intake web (Req 2).

    Compone, en un único texto de system:
      - Las fases 1–9 del intake, coherentes con INTAKE_GUION del núcleo (Req 2.1).
        Se referencia/embebe INTAKE_GUION para no duplicar el guion.
      - El catálogo de módulos (MODULE_CATALOG) y el de paletas (INTAKE_PALETTES),
        para que el LLM proponga opciones válidas (Req 2.2).
      - La instrucción de pedir archivos de forma proactiva (fotos, logo) (Req 2.3).
      - La instrucción de INVOCAR las intake tools al registrar datos, en vez de
        describir los cambios sin ejecutarlos (Req 2.5).
      - Un bloque con el Contract_State vigente (qué hay y qué falta: `missing`),
        inyectado cada turno para orientar la siguiente pregunta (Req 2.4).
    """
```

`MODULE_CATALOG` se importa de `puriq.wizard.modules` (fuente única). `INTAKE_PALETTES` se define aquí
por ser guía de presentación para el LLM (las paletas de la UI viven en `app.js`; se replican los
nombres/colores como catálogo textual, documentado como espejo). El guion por fases se toma de
`INTAKE_GUION` para no divergir del texto del Hito 1.

### 4. `agent/puriq/intake/agent.py` (Pieza 3): `Chat_Agent`

```python
@dataclass
class ChatRequest:
    mensaje: str
    archivos: list[str]      # referencias a assets/ ya subidos (text-only, Req 8.1)

@dataclass
class ChatResponse:
    respuesta: str           # texto del asistente
    estado: dict             # Contract_State (get_state, redactado)

DEFAULT_MAX_TOOL_ROUNDS = 8   # Req 1.6

class ChatAgent:
    def __init__(
        self,
        project: Path,
        *,
        provider: LLMProvider | None = None,      # default: get_provider()
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ): ...

    def run_turn(self, request: ChatRequest) -> ChatResponse:
        """Procesa un turno completo de conversación (Req 1)."""
```

Comportamiento de `run_turn` (honra Req 1, 5, 8, 9, 10):

1. **Cargar sesión** con `load_session(project)` → historial + fase previos (Req 10.1); ausente o
   corrupta → historial vacío sin fallar (Req 10.2).
2. **Estado inicial** con `get_state(project)` para inyectar en el prompt (Req 1.1, 2.4).
3. **Construir mensajes**: `system = build_system_prompt(estado)` + historial + mensaje de usuario. El
   mensaje de usuario concatena `request.mensaje` con las **referencias** de `request.archivos` como
   texto (Req 8.2); nunca bytes (Req 8.3).
4. **Bucle de tool-use** (hasta `max_tool_rounds`, Req 1.6):
   - `result = provider.complete_chat(messages, tools=INTAKE_TOOL_SPECS)` (Req 1.2); las tools se
     exponen por `INTAKE_TOOL_NAMES`/`INTAKE_TOOL_SPECS` (Req 5.2).
   - Si `result.tool_calls`: por cada una, **inyectar** `project` en `arguments` (Req 1.8), despachar
     con `run_intake_tool(name, args)` (Req 1.3, 5.1) —incluida una tool desconocida, que devuelve un
     error accionable sin tocar el contrato (Req 5.3)—, anexar el `ToolResult` (dict o error
     accionable, Req 5.4) al historial de mensajes y continuar el bucle.
   - Si `result.text` sin tool-calls: fin del bucle con ese texto (Req 1.4).
5. **Límite de rondas** (Req 1.7): si se alcanza `max_tool_rounds` con tool-calls aún pendientes, se
   termina el turno con un mensaje que indica que se alcanzó el límite de acciones.
6. **Estado final** con `get_state(project)` tras las tool-calls (Req 1.5, 5.5); los `Faltantes` salen
   del contrato en disco, no del historial (Req 10.3).
7. **Persistir sesión** con `save_session(project, history, phase)` redactado (Req 9.1, 9.3).
8. **Devolver** `ChatResponse(respuesta, estado)`.

El agente resuelve el proveedor con `get_provider()` cuando no se le inyecta uno (Req 4.1); la
inyección por constructor habilita mocks deterministas para PBT.

### 5. `agent/puriq/wizard/server.py` (Pieza 6): `POST /api/chat`

```python
class ChatBody(BaseModel):
    mensaje: str
    archivos: list[str] = []      # referencias a assets/ (Req 6.2, 8.1)

@app.post("/api/chat")
def chat(body: ChatBody):
    """Corre un turno del Chat_Agent sobre el Project_Root (Req 6, 11).

    project = project_root(); construye ChatAgent(project), corre run_turn con
    {mensaje, archivos}, y responde redact_value({respuesta, estado}) (Req 6.1,
    6.3, 11.2). Ante error, wizard_error_response (redactado, sin trazas) (Req 6.4,
    11.4). La red de seguridad transversal ya cubre lo inesperado.
    """
```

Se sirve solo en `127.0.0.1` por `serve()` (Req 11.1). La **atomicidad** ante fallo (Req 6.5) es
heredada: cada tool-call que se completa persiste con `save_contract` (validate-before-write +
`os.replace`); una tool-call que falla no deja escritura parcial, y un fallo del turno posterior a
tool-calls exitosas no revierte lo ya confirmado (el contrato refleja exactamente las tool-calls
completadas). El endpoint no reimplementa nada del núcleo.

### 6. `agent/puriq/wizard/static/` (Pieza 6): `Chat_Panel`

Se agrega un panel de chat en la UI (JS vanilla, sin toolchain, coherente con `app.js`) ubicado **al
lado del `Live_Preview`** (Req 7.1). Reutiliza `apiRequest` y `updateSkeleton`:

- **Envío** (Req 7.2): `apiRequest("POST", "/api/chat", {json: {mensaje, archivos}})`; muestra el
  mensaje del usuario y, al responder, la `respuesta` del asistente en el historial visible.
- **Refresco del preview** (Req 7.3): al recibir `Chat_Response`, vuelca `estado` (los tres
  documentos) en `state.server["tourism-data"|"site-config"|"theme-tokens"]` y llama
  `updateSkeleton()` — la misma función que ya repinta el esqueleto, sin duplicarla.
- **Indicador en curso** (Req 7.4): mientras el fetch está pendiente, muestra un indicador (p. ej.
  "Puriq está escribiendo…") y deshabilita el envío.
- **Errores** (Req 7.5): usa la normalización de errores existente (`{causa, acción}` /
  `{documento, campo, sugerencia}`) para mostrar el mensaje accionable sin bloquear envíos
  posteriores.

`archivos` se toma de las referencias de assets ya subidos por el flujo existente (drag & drop →
`POST /api/assets`); el Chat_Panel no sube binarios por `/api/chat` (Req 8.1, 8.3).

## Data Models

Esta fase **no introduce modelos persistidos nuevos en el contrato**: sigue operando sobre los tres
documentos JSON del Hito 1 vía `run_intake_tool`/`get_state`. Los modelos nuevos son de **conversación**
(mensajes/tool-use), de **entrada/salida del turno** y del **Session_Store**.

### Chat_Request / Chat_Response (canal web)

- **Chat_Request** (`POST /api/chat`): `{ "mensaje": string, "archivos": string[] }`. `archivos` son
  rutas relativas bajo `assets/` ya subidas (Req 6.2, 8.1); opcional (default `[]`).
- **Chat_Response**: `{ "respuesta": string, "estado": Contract_State }`, redactada (Req 6.3, 11.2).
  `estado` es exactamente la salida de `get_state` (los tres documentos + `missing`).

### Modelo de mensajes / tool-use (`complete_chat`)

`Message`, `ToolCall`, `ToolResult`, `ChatResult` (ver Components). Forma **serializable** del
historial (lo que guarda el `Session_Store`), independiente del proveedor:

```jsonc
// content/.intake-session.json
{
  "phase": "3",                       // fase del Intake_Guion en curso, o null
  "history": [
    { "role": "user", "content": "Quiero mostrar el Cerro Rico" },
    { "role": "assistant", "content": null,
      "tool_calls": [ { "id": "tc_1", "name": "add_place",
                        "arguments": { "name": "Cerro Rico", "category": "cerro" } } ] },
    { "role": "tool",
      "tool_result": { "tool_call_id": "tc_1", "content": "{\"document\": {...}}" } },
    { "role": "assistant", "content": "Agregué el Cerro Rico. ¿Tenés una foto?" }
  ]
}
```

Notas:
- El `arguments` **persistido/historial no contiene `project`** en las tool-calls tal como las emite el
  LLM; `project` se inyecta al despachar y no se guarda en el historial (evita filtrar rutas locales).
- Todo el archivo pasa por `config.redact_value` antes de escribirse (Req 9.3): ningún valor de
  secreto queda persistido.
- El `Session_Store` es **continuidad**, no verdad: `missing` y el `estado` devueltos siempre salen de
  `get_state` sobre el contrato en disco (Req 9.4, 10.3).

### Traducción de tools a formato nativo (entrada de `complete_chat`)

Entrada: `INTAKE_TOOL_SPECS` (lista de `{name, description, inputSchema, handler}`). Salida por
proveedor (se descarta `handler` y se elimina `project` del schema, DD-2):

- **Bedrock/Claude**: `{ "name", "description", "input_schema": <inputSchema sin project> }`.
- **OpenAI-compatible**: `{ "type": "function", "function": { "name", "description",
  "parameters": <inputSchema sin project> } }`.

## Correctness Properties

*Una propiedad es una característica o comportamiento que debe cumplirse en todas las ejecuciones
válidas del sistema: esencialmente, un enunciado formal de lo que el sistema debe hacer. Las
propiedades son el puente entre las especificaciones legibles por humanos y las garantías de
corrección verificables por máquina.*

Estas propiedades se ejercitan sobre un **proyecto temporal** (directorio con los 3 JSON del
contrato) y un **proveedor de LLM mock** inyectado en el `Chat_Agent` (el constructor admite
`provider=`), de modo que el bucle es determinista y sin coste externo: el mock emite las tool-calls o
el texto que la propiedad necesita. El comportamiento de cada intake tool (validación, atomicidad,
integridad referencial, cómputo de `missing`) ya está cubierto por las propiedades del Hito 1; aquí se
prueban las invariantes del **nivel de la superficie web**: el bucle por turno, la inyección de
`project`, el límite de rondas, la traducción de tools, el round-trip de sesión y la redacción.

Tras el análisis de prework se realizó una reflexión para eliminar redundancias: los criterios 1.3,
1.4, 1.5, 3.5, 5.1 y 5.5 describen el mismo bucle de despacho y se consolidan en la Property 1; 8.1,
8.2 y 8.3 se consolidan en la Property 10; 9.1 y 10.1 en la Property 11 (round-trip); 6.4 y 11.4 en la
Property 5; y 2.2 con 2.4 en la Property 7.

### Property 1: El turno despacha por el núcleo y devuelve el estado de get_state

*Para toda* secuencia de Tool_Calls válidas emitida por el modelo seguida de una respuesta de texto,
el Chat_Agent despacha cada Tool_Call por `run_intake_tool`, anexa su Tool_Result al historial de
mensajes antes de la siguiente llamada al modelo, finaliza el turno con el texto del asistente y
devuelve una Chat_Response cuyo `estado` es igual al de `get_state(project)` tras esas Tool_Calls.

**Validates: Requirements 1.3, 1.4, 1.5, 3.5, 5.1, 5.5**

### Property 2: Toda Tool_Call se despacha con el Project_Root inyectado

*Para toda* Tool_Call emitida por el modelo con argumentos arbitrarios (incluso sin `project` o con un
`project` distinto), los argumentos que el Chat_Agent entrega a `run_intake_tool` contienen
`project` igual al Project_Root del turno.

**Validates: Requirements 1.8**

### Property 3: El número de rondas de Tool_Call por turno está acotado

*Para todo* proveedor que siempre emite al menos una Tool_Call, el Chat_Agent ejecuta a lo sumo
`max_tool_rounds` rondas de Tool_Call y finaliza el turno con un mensaje que indica que se alcanzó el
límite de acciones, devolviendo el Contract_State vigente.

**Validates: Requirements 1.6, 1.7**

### Property 4: Una Tool_Call con nombre inexistente no altera el contrato

*Para todo* nombre de herramienta que no pertenece a `INTAKE_TOOL_NAMES`, despacharla por
`run_intake_tool` produce un Tool_Result de error accionable y deja los tres archivos del contrato
byte a byte idénticos a como estaban antes de la invocación.

**Validates: Requirements 5.3**

### Property 5: Todo error de Tool_Call o de turno se entrega traducido y redactado

*Para toda* Tool_Call que `run_intake_tool` rechaza, el Chat_Agent entrega el error accionable
(`{causa, acción}` o `{documento, campo, sugerencia}`) como Tool_Result al modelo; y *para toda*
excepción que aborta el procesamiento del turno, el Chat_Endpoint responde ese error traducido por
`wizard_error_response`, sin trazas crudas ni valores de secretos.

**Validates: Requirements 5.4, 6.4, 11.4**

### Property 6: El contexto del turno contiene prompt, estado e historial

*Para todo* historial previo y todo Contract_State, los mensajes que el Chat_Agent pasa a
`complete_chat` incluyen el Intake_Prompt (system), el Contract_State vigente y el historial previo de
la conversación.

**Validates: Requirements 1.1**

### Property 7: El Intake_Prompt refleja los catálogos y los faltantes vigentes

*Para todo* Contract_State, el system prompt construido contiene todas las claves de `MODULE_CATALOG`,
todos los nombres del catálogo de paletas, y refleja los `Faltantes` (`missing`) del Contract_State
inyectado.

**Validates: Requirements 2.2, 2.4**

### Property 8: La traducción de tools preserva identidad y esquema

*Para todo* subconjunto de `INTAKE_TOOL_SPECS`, su traducción al formato nativo (Bedrock y
OpenAI-compatible) produce exactamente una herramienta por spec, preserva `name` y `description` de
cada una, conserva su esquema de parámetros y no incluye la propiedad `project`.

**Validates: Requirements 3.3**

### Property 9: La respuesta del proveedor se parsea a Tool_Calls estructuradas

*Para toda* respuesta del proveedor que representa solicitudes de herramienta, `complete_chat` la
parsea a una lista de Tool_Call cuyos `name` y `arguments` coinciden con los de la respuesta.

**Validates: Requirements 3.4**

### Property 10: Los archivos se tratan como referencias textuales, sin binarios

*Para toda* lista de referencias de archivos del Chat_Request, el mensaje de usuario del turno incluye
cada referencia como texto y el turno no lee ni transmite bytes de imágenes ni extrae texto de PDFs.

**Validates: Requirements 8.1, 8.2, 8.3**

### Property 11: El Session_Store hace round-trip del historial y la fase

*Para todo* historial y toda fase (sin secretos), tras `save_session` un `load_session` posterior
devuelve un historial y una fase equivalentes; y tras finalizar un turno, el Session_Store del
Project_Root contiene el historial y la fase resultantes.

**Validates: Requirements 9.1, 10.1**

### Property 12: La carga de sesión es tolerante a ausencia o corrupción

*Para todo* contenido de `content/.intake-session.json` ausente o no legible como sesión válida,
`load_session` devuelve un historial vacío sin lanzar excepción.

**Validates: Requirements 10.2**

### Property 13: Ningún secreto queda persistido en el Session_Store

*Para todo* historial o fase que contendría un valor registrado como secreto, el contenido escrito en
`content/.intake-session.json` no contiene el valor crudo del secreto.

**Validates: Requirements 9.3**

### Property 14: Los Faltantes se derivan del contrato, no del historial

*Para todo* historial previo y todo contrato en disco, el `missing` del `estado` devuelto por el turno
es igual al `missing` de `get_state(project)`, independientemente del contenido del historial.

**Validates: Requirements 10.3**

### Property 15: Toda Chat_Response se devuelve redactada

*Para todo* turno cuyo `estado` o `respuesta` contendría un valor registrado como secreto, la
Chat_Response devuelta por el Chat_Endpoint no contiene el valor crudo del secreto.

**Validates: Requirements 11.2**

## Error Handling

El manejo de errores reutiliza la única fuente de verdad del proyecto (`errors.wizard_error_response`
+ `config.redact`/`redact_value`) y el despacho ya endurecido del Hito 1 (`run_intake_tool`). No se
introducen mensajes ad hoc.

- **Errores de una Tool_Call.** `run_intake_tool` ya captura cualquier excepción de la intake tool y
  devuelve `{causa, acción}` (o `{documento, campo, sugerencia}` para errores de esquema), redactado.
  El Chat_Agent entrega ese resultado al modelo como Tool_Result (Req 5.4) para que informe al usuario
  y proponga una corrección; no aborta el turno por un error de una tool. Una tool desconocida no
  propaga: `run_intake_tool` devuelve un error accionable que lista las tools disponibles (Req 5.3).

- **Atomicidad ante fallo (Req 6.5).** Cada intake tool de escritura persiste con `save_contract`
  (validate-before-write + `os.replace`): una Tool_Call que falla no deja escritura parcial, y las
  Tool_Calls previas ya confirmadas quedan íntegras. Un fallo posterior del turno no corrompe el
  contrato; el estado en disco refleja exactamente las Tool_Calls completadas.

- **Error del proveedor de LLM.** Un backend sin tool-use lanza un error que nombra `PURIQ_LLM_MODE`
  y los modos válidos (Req 4.4). Una credencial faltante propaga `MissingEnvVarError` que nombra la
  variable sin exponer su valor (Req 4.5); errores de red/servicio (boto3/httpx) los traduce
  `describir_error` a "Fallo de red o servicio externo" con acción sugerida. Todos llegan al
  Chat_Endpoint y se traducen con `wizard_error_response` (Req 6.4, 11.4).

- **Borde del endpoint (Req 6.4, 11.2, 11.4).** `POST /api/chat` envuelve el turno en un `try/except`
  que traduce con `wizard_error_response` (redactado) y aplica `redact_value` a la respuesta de éxito.
  La red de seguridad transversal de `server.py` (`@app.exception_handler(Exception)` → 500 redactado
  sin traza; `RequestValidationError` → 422 redactado) cubre lo inesperado.

- **Session_Store (Req 10.2).** `load_session` es tolerante: ante archivo ausente, JSON inválido o
  estructura inesperada, devuelve `Session(history=[], phase=None)` sin fallar. `save_session` escribe
  de forma atómica y redactada; un fallo de escritura de sesión no debe tumbar el turno (la sesión es
  continuidad, no verdad) y se registra como advertencia.

## Testing Strategy

Enfoque dual: pruebas de propiedad para las invariantes universales de la superficie web y pruebas de
ejemplo/integración para el cableado, los backends externos y la UI. Coherente con el estilo del Hito
1.

### Property-based testing

- **Librería.** Se usa **Hypothesis** (Python), ya presente en el proyecto (`agent/.hypothesis/`). No
  se implementa PBT desde cero.
- **Configuración.** Cada prueba de propiedad corre un **mínimo de 100 iteraciones**
  (`@settings(max_examples=100)` o superior).
- **Aislamiento.** Cada propiedad opera sobre un **directorio de proyecto temporal** (`tmp_path`) con
  los 3 JSON del contrato, y sobre un **proveedor mock** inyectado en `ChatAgent(provider=...)` que
  emite tool-calls o texto según la propiedad. Así el bucle es determinista y sin llamadas externas.
- **Generadores.** Estrategias para: historiales de conversación (secuencias user/assistant/tool),
  fases 1–9, listas de referencias de archivos (rutas `assets/...`), argumentos arbitrarios de
  tool-call (con y sin `project`, con `project` falso), nombres de tool dentro y fuera de
  `INTAKE_TOOL_NAMES`, subconjuntos de `INTAKE_TOOL_SPECS`, payloads de proveedor (Bedrock/OpenAI) con
  y sin tool-calls, contratos parciales/completos (para variar `missing`), y valores marcados como
  secreto insertados en estado/historial/respuesta.
- **Mapa propiedad → prueba.** Cada una de las 15 propiedades se implementa con **una sola** prueba de
  propiedad.
- **Etiquetado.** Cada prueba lleva un comentario con el formato:
  `# Feature: conversational-web-chat, Property {N}: {texto de la propiedad}`.

### Pruebas de ejemplo y de integración

- **Ejemplos (unit):**
  - El prompt contiene las fases del `INTAKE_GUION` (2.1), instruye a pedir archivos (2.3) e instruye a
    invocar las tools (2.5).
  - `complete_chat` expone la interfaz de texto-o-tool-calls (3.1); `complete(prompt)` se conserva sin
    cambios (regresión, 3.2); `complete_chat` es text-only (3.6).
  - `get_provider()` resuelve el backend por `PURIQ_LLM_MODE` (4.1); Ollama rechaza `complete_chat`
    nombrando `PURIQ_LLM_MODE` (4.4); sin `PURIQ_OPENAI_API_KEY` se lanza `MissingEnvVarError` que la
    nombra sin valor (4.5); la credencial se lee con `get_env(secret=True)` (11.3).
  - El Chat_Agent expone las tools por `INTAKE_TOOL_NAMES` (5.2); un mock que emite `attach_asset`
    despacha por `run_intake_tool` (8.4).
- **Integración (mock del backend, 1–3 ejemplos):**
  - **Bedrock (4.2):** mock de `invoke_model` devolviendo `stop_reason="tool_use"` con un bloque
    `tool_use`; verificar el parseo a `ToolCall` y el segundo turno con `tool_result`. El E2E real
    contra Bedrock queda fuera de PBT (coste/servicio externo).
  - **OpenAI-compatible (4.3):** mock de `httpx.post` devolviendo `message.tool_calls`; verificar el
    parseo y el endpoint local. E2E real fuera de PBT.
  - **Endpoint (6.1, 6.2, 6.3):** `TestClient` de FastAPI con un `ChatAgent` mock; `POST /api/chat`
    con y sin `archivos` responde 200 con `{respuesta, estado}`.
- **Smoke (11.1):** `serve()` liga el servidor a `127.0.0.1`.
- **UI del Chat_Panel (7.1–7.5):** el chat web es JS vanilla sin toolchain; su comportamiento se
  cubre con pruebas de ejemplo/DOM ligeras o revisión: el panel se monta junto al `#skeleton` (7.1),
  el envío llega al endpoint y renderiza la respuesta (7.2), tras la respuesta se vuelca `estado` en
  `state.server` y se llama `updateSkeleton` (7.3), hay indicador en curso (7.4) y el error accionable
  se muestra sin bloquear envíos (7.5). No se aplica PBT a la capa de rendering (se usa
  snapshot/DOM), coherente con la guía de cuándo NO usar property-based testing.

### Balance

Las pruebas de propiedad cubren las invariantes universales de la superficie web (bucle de despacho,
inyección de `project`, cota de rondas, traducción de tools, parseo de respuestas, round-trip y
robustez de sesión, redacción y derivación de `missing` del contrato). Las pruebas de ejemplo se
reservan para el cableado, los backends externos (con mocks), la configuración y la UI; se evita
multiplicar ejemplos donde una propiedad ya cubre el espacio de entradas. El comportamiento del núcleo
de intake no se re-testea aquí: ya lo cubren las propiedades del Hito 1.
