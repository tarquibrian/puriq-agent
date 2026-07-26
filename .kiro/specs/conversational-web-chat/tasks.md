# Plan de Implementación: Chat Web Conversacional con Preview en Vivo (Hito 2)

## Overview

El plan implementa las **Piezas 7, 4, 3 y 6** del diseño en Python, sobre `agent/puriq/`, de forma
incremental y de bajo riesgo, siguiendo el orden sugerido en §8 del diseño: primero los cimientos que
no dependen del bucle (**Session_Store** y el **provider con tool-use**), luego el **Intake_Prompt** y
el **Chat_Agent**, y por último el **canal web** (`POST /api/chat` + `Chat_Panel`). El chat es
**text-only**: no reimplementa el núcleo de intake del Hito 1, sino que despacha las tool-calls por
`run_intake_tool` inyectando `project`, y refresca el preview reutilizando `updateSkeleton`.

La estrategia de pruebas es **dual**: pruebas de propiedad con **Hypothesis** (mínimo 100 iteraciones,
sobre un directorio de proyecto temporal `tmp_path` y un proveedor mock inyectado en
`ChatAgent(provider=...)`) para las 15 propiedades de correctitud, y pruebas de ejemplo/integración
(mocks de `invoke_model` de Bedrock y de `httpx.post` de OpenAI, `TestClient` para el endpoint, smoke
de `127.0.0.1`, y ejemplos/DOM ligeros para el `Chat_Panel`). Cada prueba de propiedad se etiqueta con
`# Feature: conversational-web-chat, Property {N}: ...`.

## Tasks

- [x] 1. Estado de sesión (Pieza 7): `Session_Store` en `intake/session.py`
  - [x] 1.1 Implementar `load_session`/`save_session`
    - Crear `agent/puriq/intake/session.py` con el dataclass `Session {history, phase}` y `_SESSION_RELPATH = "content/.intake-session.json"`
    - `save_session(project, history, phase)`: aplicar `config.redact_value` al historial y a la fase, crear `content/` si falta y escribir con temp + `os.replace` (mismo patrón atómico que `contracts.save_contract`)
    - `load_session(project)`: leer y parsear el JSON; ante ausencia, JSON inválido o estructura inesperada devolver `Session(history=[], phase=None)` sin fallar; nunca derivar `missing` de aquí
    - Módulo de E/S sin FastAPI (misma frontera que `asset_store`/`qa_store` del Hito 1)
    - _Requirements: 9.1, 9.2, 9.3, 10.2, 9.4_

  - [x]* 1.2 Escribir prueba de propiedad para el round-trip de sesión
    - **Property 11: El Session_Store hace round-trip del historial y la fase**
    - **Validates: Requirements 9.1, 10.1**

  - [x]* 1.3 Escribir prueba de propiedad para la tolerancia a ausencia/corrupción
    - **Property 12: La carga de sesión es tolerante a ausencia o corrupción**
    - **Validates: Requirements 10.2**

  - [x]* 1.4 Escribir prueba de propiedad para la no persistencia de secretos
    - **Property 13: Ningún secreto queda persistido en el Session_Store**
    - **Validates: Requirements 9.3**

- [x] 2. Provider con tool-use text-only (Pieza 4): `complete_chat` en `tools/generate_content.py`
  - [x] 2.1 Definir el modelo de mensajes neutral, extender el protocolo y los helpers de traducción
    - Agregar los dataclasses `ToolCall`, `ToolResult`, `Message` y `ChatResult` junto al `Protocol` `LLMProvider`, y declarar `complete_chat(messages, tools=None) -> ChatResult` en el protocolo (sin tocar `complete(prompt)`)
    - Implementar los helpers compartidos `_tools_to_bedrock(specs)` y `_tools_to_openai(specs)` que parten de `INTAKE_TOOL_SPECS`, descartan `handler` y **quitan `project`** de `properties` y `required` del `inputSchema` (DD-2)
    - _Requirements: 3.1, 3.2, 3.6_

  - [x]* 2.2 Escribir prueba de propiedad para la traducción de tools
    - **Property 8: La traducción de tools preserva identidad y esquema**
    - **Validates: Requirements 3.3**

  - [x] 2.3 Implementar `BedrockProvider.complete_chat` (tool-use nativo de Claude)
    - Traducir `messages` al cuerpo Messages de Claude (system aparte, `user`/`assistant`, bloques `tool_use`/`tool_result`), mapear tools con `_tools_to_bedrock` e invocar `invoke_model` con `tools` + `tool_choice: {"type":"auto"}`
    - Si `stop_reason == "tool_use"`, extraer los bloques `tool_use` como `ToolCall(id, name, input)`; si no, concatenar los bloques `text` como `ChatResult.text`
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 4.2_

  - [x] 2.4 Implementar `OpenAICompatibleProvider.complete_chat` (function calling)
    - Traducir `messages` a la forma OpenAI (`role`/`content`, `assistant.tool_calls`, `role:"tool"` con `tool_call_id`), mapear tools con `_tools_to_openai` y hacer `POST .../chat/completions` con `tools`; reusar el endpoint local (base_url) para prototipar sin AWS
    - Si la respuesta trae `message.tool_calls`, parsear cada una a `ToolCall(id, name, json.loads(arguments))`; si no, usar `message.content` como texto; leer la clave con `get_env(..., required=True, secret=True)`
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 4.3, 4.5, 11.3_

  - [x] 2.5 Implementar `OllamaProvider.complete_chat` (rechazo accionable)
    - Lanzar un error accionable que nombra `PURIQ_LLM_MODE` y los modos con tool-use (`bedrock`, `openai`); no emular tool-use
    - _Requirements: 4.4_

  - [x]* 2.6 Escribir prueba de propiedad para el parseo de la respuesta del proveedor
    - **Property 9: La respuesta del proveedor se parsea a Tool_Calls estructuradas**
    - **Validates: Requirements 3.4**

  - [x]* 2.7 Escribir pruebas de ejemplo e integración de los providers
    - Regresión de `complete(prompt)` sin cambios (3.2); `complete_chat` es text-only (3.6); `get_provider()` resuelve por `PURIQ_LLM_MODE` (4.1); Ollama rechaza nombrando `PURIQ_LLM_MODE` (4.4); sin `PURIQ_OPENAI_API_KEY` se lanza `MissingEnvVarError` que la nombra sin valor (4.5); credencial leída con `get_env(secret=True)` (11.3)
    - Integración con mock de `invoke_model` (Bedrock, `stop_reason="tool_use"`) y de `httpx.post` (OpenAI, `message.tool_calls`): verificar parseo a `ToolCall` y el segundo turno con `tool_result`
    - _Requirements: 3.2, 3.6, 4.1, 4.2, 4.3, 4.4, 4.5, 11.3_

- [x] 3. Checkpoint - Asegurar que las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. System prompt del intake web (Pieza 3): `Intake_Prompt` en `intake/prompt.py`
  - [x] 4.1 Implementar `INTAKE_PALETTES` y `build_system_prompt(contract_state)`
    - Crear `agent/puriq/intake/prompt.py`; definir `INTAKE_PALETTES` (espejo textual de las 6 paletas de la UI) e importar `MODULE_CATALOG` de `puriq.wizard.modules`
    - Componer el system prompt: fases 1–9 referenciando/embebiendo `INTAKE_GUION` (2.1), el catálogo de módulos y de paletas (2.2), la regla de pedir archivos proactivamente (2.3), la instrucción de invocar las intake tools al registrar datos (2.5) y un bloque con el `Contract_State` vigente y sus `missing` (2.4)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x]* 4.2 Escribir prueba de propiedad para los catálogos y faltantes del prompt
    - **Property 7: El Intake_Prompt refleja los catálogos y los faltantes vigentes**
    - **Validates: Requirements 2.2, 2.4**

  - [x]* 4.3 Escribir pruebas de ejemplo del prompt
    - El prompt contiene las fases del `INTAKE_GUION` (2.1), instruye a pedir archivos (2.3) e instruye a invocar las tools (2.5)
    - _Requirements: 2.1, 2.3, 2.5_

- [x] 5. Bucle conversacional por turno (Pieza 3): `Chat_Agent` en `intake/agent.py`
  - [x] 5.1 Implementar `ChatAgent.run_turn` (bucle de tool-use)
    - Crear `agent/puriq/intake/agent.py` con los dataclasses `ChatRequest {mensaje, archivos}`, `ChatResponse {respuesta, estado}`, `DEFAULT_MAX_TOOL_ROUNDS = 8` y `ChatAgent(project, *, provider=None, max_tool_rounds=...)` que resuelve el proveedor con `get_provider()` si no se inyecta uno (habilita mocks para PBT)
    - `run_turn`: cargar sesión (`load_session`), obtener estado inicial (`get_state`), construir mensajes (`build_system_prompt(estado)` + historial + mensaje de usuario con las **referencias** de `archivos` como texto), correr el bucle de tool-use hasta `max_tool_rounds` invocando `complete_chat(messages, tools=INTAKE_TOOL_SPECS)`
    - Por cada tool-call: **inyectar** `project` en `arguments`, despachar con `run_intake_tool(name, args)`, anexar el `ToolResult` al historial y continuar; texto sin tool-calls finaliza el turno; alcanzar el límite finaliza con un mensaje de "límite de acciones alcanzado"
    - Estado final con `get_state` tras las tool-calls, persistir sesión con `save_session` redactado y devolver `ChatResponse(respuesta, estado)`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 5.1, 5.2, 5.3, 5.4, 5.5, 8.2, 8.3, 8.4, 9.1, 10.1, 10.3_

  - [x]* 5.2 Escribir prueba de propiedad para el despacho por el núcleo y el estado final
    - **Property 1: El turno despacha por el núcleo y devuelve el estado de get_state**
    - **Validates: Requirements 1.3, 1.4, 1.5, 3.5, 5.1, 5.5**

  - [x]* 5.3 Escribir prueba de propiedad para la inyección del Project_Root
    - **Property 2: Toda Tool_Call se despacha con el Project_Root inyectado**
    - **Validates: Requirements 1.8**

  - [x]* 5.4 Escribir prueba de propiedad para la cota de rondas de tool-call
    - **Property 3: El número de rondas de Tool_Call por turno está acotado**
    - **Validates: Requirements 1.6, 1.7**

  - [x]* 5.5 Escribir prueba de propiedad para la tool inexistente
    - **Property 4: Una Tool_Call con nombre inexistente no altera el contrato**
    - **Validates: Requirements 5.3**

  - [x]* 5.6 Escribir prueba de propiedad para el contexto del turno
    - **Property 6: El contexto del turno contiene prompt, estado e historial**
    - **Validates: Requirements 1.1**

  - [x]* 5.7 Escribir prueba de propiedad para el tratamiento de archivos como texto
    - **Property 10: Los archivos se tratan como referencias textuales, sin binarios**
    - **Validates: Requirements 8.1, 8.2, 8.3**

  - [x]* 5.8 Escribir prueba de propiedad para la derivación de faltantes del contrato
    - **Property 14: Los Faltantes se derivan del contrato, no del historial**
    - **Validates: Requirements 10.3**

  - [x]* 5.9 Escribir pruebas de ejemplo del agente
    - El Chat_Agent expone las tools por `INTAKE_TOOL_NAMES` (5.2); un mock que emite `attach_asset` despacha por `run_intake_tool` (8.4)
    - _Requirements: 5.2, 8.4_

- [x] 6. Checkpoint - Asegurar que las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Canal web del chat (Pieza 6): `POST /api/chat` en `wizard/server.py`
  - [x] 7.1 Implementar el `Chat_Endpoint`
    - Definir el modelo pydantic `ChatBody {mensaje: str, archivos: list[str] = []}` y el endpoint `POST /api/chat` que resuelve `project = project_root()`, construye `ChatAgent(project)` y corre `run_turn`
    - Responder `redact_value({respuesta, estado})` en éxito; envolver en `try/except` que traduce con `wizard_error_response` (redactado, sin trazas); la atomicidad ante fallo (6.5) es heredada de `save_contract`; se sirve solo en `127.0.0.1` por `serve()`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 8.1, 11.1, 11.2, 11.4_

  - [x]* 7.2 Escribir prueba de propiedad para la traducción y redacción de errores
    - **Property 5: Todo error de Tool_Call o de turno se entrega traducido y redactado**
    - **Validates: Requirements 5.4, 6.4, 11.4**

  - [x]* 7.3 Escribir prueba de propiedad para la redacción de la Chat_Response
    - **Property 15: Toda Chat_Response se devuelve redactada**
    - **Validates: Requirements 11.2**

  - [x]* 7.4 Escribir pruebas de integración y smoke del endpoint
    - Con `TestClient` y un `ChatAgent` mock: `POST /api/chat` con y sin `archivos` responde 200 con `{respuesta, estado}` (6.1, 6.2, 6.3); smoke de que `serve()` liga el servidor a `127.0.0.1` (11.1)
    - _Requirements: 6.1, 6.2, 6.3, 11.1_

- [x] 8. Panel de chat y preview en vivo (Pieza 6): `Chat_Panel` en `wizard/static/`
  - [x] 8.1 Implementar el `Chat_Panel`
    - Agregar el panel de chat (JS vanilla, coherente con `app.js`) montado junto al `Live_Preview`/`#skeleton` (7.1); enviar con `apiRequest("POST", "/api/chat", {json: {mensaje, archivos}})` y renderizar el mensaje del usuario y la `respuesta` del asistente en el historial visible (7.2)
    - Al recibir `Chat_Response`, volcar `estado` en `state.server["tourism-data"|"site-config"|"theme-tokens"]` y llamar `updateSkeleton()` (7.3); mostrar indicador "en curso" y deshabilitar envío mientras el fetch está pendiente (7.4); mostrar el error accionable normalizado sin bloquear envíos posteriores (7.5)
    - Tomar `archivos` de las referencias de assets ya subidos por el flujo existente (drag & drop → `POST /api/assets`); no subir binarios por `/api/chat`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.1_

  - [ ]* 8.2 Escribir pruebas de ejemplo/DOM ligeras del Chat_Panel (no implementada: el proyecto no tiene arnés de testing JS)
    - El panel se monta junto al `#skeleton` (7.1); el envío llega al endpoint y renderiza la respuesta (7.2); tras la respuesta se vuelca `estado` en `state.server` y se llama `updateSkeleton` (7.3); hay indicador en curso (7.4); el error accionable se muestra sin bloquear envíos (7.5)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
    - (No implementada: el proyecto no tiene arnés de testing JS)

- [x] 9. Checkpoint final - Asegurar que todas las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Las tareas marcadas con `*` son opcionales (pruebas) y pueden omitirse para un MVP más rápido, igual que en el spec del Hito 1.
- Cada tarea referencia requisitos específicos para trazabilidad; cada prueba de propiedad referencia su `Property N` del diseño con su `Validates`.
- Las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones, sobre un proyecto temporal (`tmp_path`) y un proveedor mock inyectado en `ChatAgent(provider=...)`, y cada una lleva el comentario `# Feature: conversational-web-chat, Property {N}: ...`.
- El chat es **text-only**: el núcleo de intake del Hito 1 no se re-testea aquí; el agente solo lo conduce despachando por `run_intake_tool` con `project` inyectado.
- Los checkpoints aseguran validación incremental en cortes razonables (cimientos → prompt/agente → canal web).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "4.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "2.2", "2.3", "4.2", "4.3"] },
    { "id": 2, "tasks": ["2.4"] },
    { "id": 3, "tasks": ["2.5"] },
    { "id": 4, "tasks": ["2.6", "2.7", "5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9"] },
    { "id": 6, "tasks": ["7.1"] },
    { "id": 7, "tasks": ["7.2", "7.3", "7.4", "8.1"] },
    { "id": 8, "tasks": ["8.2"] }
  ]
}
```
