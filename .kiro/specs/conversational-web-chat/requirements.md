# Documento de Requisitos

## Introducción

Este spec cubre la **segunda fase (Hito 2)** de la capa conversacional descrita en `docs/registro-conversacional.md`: el **chat en la web de Puriq con previsualización en vivo**, en modo **text-only**. El encargado de turismo conversa con un panel de chat dentro del wizard y ve el esqueleto del sitio armándose a cada paso, mientras Puriq —que aquí **trae su propio LLM**— conduce la conversación por fases, interpreta el lenguaje natural y llena el contrato (los 3 documentos JSON + `assets/` + `content/`) invocando las intake tools.

Esta fase se apoya en el **Hito 1 ya implementado** (spec `conversational-intake-mcp`): el núcleo de intake (`agent/puriq/intake/tools.py`) ya expone las 12 funciones tipadas de registro, `INTAKE_TOOL_SPECS` (specs con `name`/`description`/`inputSchema`/`handler`), `INTAKE_TOOL_NAMES`, `INTAKE_GUION` (el guion por fases 1–9), `get_state` (estado + faltantes) y `run_intake_tool(name, arguments)` (despacho compartido con traducción de errores redactada). El MCP (`agent/puriq/mcp/server.py`) ya expone ese núcleo a clientes externos que traen su propio LLM (superficie A). **Esta fase construye la superficie B (web)** reutilizando exactamente ese mismo núcleo: el loop web despacha las tool-calls a través de `run_intake_tool` / `INTAKE_TOOL_SPECS`, sin reimplementar ninguna acción de registro.

Las piezas de esta fase (según §3 y §8 del documento de diseño) son:

- **Pieza 3 — Loop del agente + guion (superficie B):** `agent/puriq/intake/agent.py` (el bucle conversacional cuando Puriq trae el LLM) y `agent/puriq/intake/prompt.py` (el system prompt con las fases del §5, el catálogo de módulos/paletas y la regla de pedir archivos; el estado de `get_state` se inyecta cada turno).
- **Pieza 4 (text-only, SIN visión) — Provider LLM con tool-use:** extender el `LLMProvider` de `agent/puriq/tools/generate_content.py` (hoy text-only, `complete(prompt) -> str`) con `complete_chat(messages, tools=None)` para soportar tool-use, **sin romper** `complete(prompt)`. El proveedor es **configurable** y soporta **ambos** caminos: Bedrock Claude (tool-use nativo, camino del pitch AWS) y un proveedor compatible con OpenAI (para prototipar sin credenciales AWS, incluidos servidores locales), seleccionable por configuración con el mismo mecanismo existente (`get_provider()` / `PURIQ_LLM_MODE`).
- **Pieza 6 — Canal web:** `POST /api/chat` en `agent/puriq/wizard/server.py` (recibe `{mensaje, archivos[]}`, corre el loop, devuelve `{respuesta, estado}`) y un panel de chat en la UI del wizard **al lado del esqueleto en vivo**, que reutiliza el esqueleto/preview existente (`updateSkeleton`): cada tool-call refresca el preview.
- **Pieza 7 — Estado de sesión:** `content/.intake-session.json` (historial + fase actual) para continuidad. El contrato en disco sigue siendo la fuente de verdad; la sesión solo evita empezar la charla de cero.

### Alcance y exclusiones

Este spec abarca **exclusivamente** las Piezas 3, 4 (sin visión), 6 y 7 (Hito 2), en modo **text-only**.

Queda **explícitamente fuera de alcance** de este spec (Fase 3 / Hito 4 posterior):

- **Visión / multimodal:** describir imágenes con un LLM multimodal para autocompletar `alt` o enriquecer descripciones (parte de visión de la Pieza 4 y de la Pieza 5).
- **Extracción de texto de PDFs** y su destilado a contenido/Q&A (parte de la Pieza 5).
- **Transmisión de bytes de archivos al LLM** en el turno del chat. En esta fase el chat es text-only: el upload de imágenes sigue por el flujo de assets existente (`POST /api/assets`, drag & drop) y no por el LLM.
- **Re-implementación del núcleo de intake:** las acciones de registro ya existen (`intake/tools.py`); esta fase solo las conduce desde la web.

### Decisión de alcance sobre archivos (text-only)

El chat de esta fase es **text-only**. La carga real de imágenes (bytes) se mantiene en el flujo de assets ya existente del wizard (drag & drop → `POST /api/assets`), sin cambios. El campo `archivos[]` del `POST /api/chat` transporta **referencias a assets ya cargados** por ese flujo (rutas relativas bajo `assets/`), no binarios: el loop las expone al LLM como **contexto textual** para que el asistente las reconozca y razone sobre ellas, y —cuando la conversación identifique el destino— las asocie a un Place o Event mediante la intake tool `attach_asset` ya existente. La ingesta rica (visión de imágenes, extracción de PDFs) queda para la Fase 3.

Este documento define QUÉ debe hacer el chat web, no CÓMO implementarlo (eso corresponde al diseño).

## Glosario

- **Puriq**: El agente completo en Python (CLI + core + tools + wizard + MCP + intake).
- **Contrato**: El conjunto de documentos JSON validados contra `schemas/`: `tourism-data.json`, `site.config.json` y `theme.tokens.json`, más `assets/` y `content/`.
- **Intake_Core**: El núcleo de intake ya implementado en `agent/puriq/intake/tools.py`, que expone `INTAKE_TOOL_SPECS`, `INTAKE_TOOL_NAMES`, `INTAKE_GUION`, `get_state` y `run_intake_tool`.
- **Intake_Tool**: Una de las acciones de registro del Intake_Core (`set_site`, `configure_modules`, `add_place`, `add_event`, `edit_item`, `remove_item`, `set_brand`, `configure_landing`, `add_qa`, `attach_asset`, `get_state`, `build`).
- **Intake_Tool_Specs**: La lista `INTAKE_TOOL_SPECS` de specs de las Intake_Tools, cada una con `name`, `description`, `inputSchema` (JSON Schema) y `handler`.
- **Run_Intake_Tool**: La función `run_intake_tool(name, arguments)` del Intake_Core que despacha una Intake_Tool y traduce sus errores a una respuesta accionable y redactada.
- **Get_State**: La Intake_Tool de solo lectura que devuelve el estado de los tres documentos del Contrato y la lista de Faltantes.
- **Faltantes**: La lista `missing` que Get_State devuelve, indicando qué piezas del Contrato aún faltan o están incompletas.
- **Contract_State**: El resultado de Get_State (estado de los tres documentos + Faltantes), redactado.
- **Intake_Guion**: El texto del guion por fases (fases 1–9 del §5 del diseño) ya definido en el Intake_Core como `INTAKE_GUION`.
- **Chat_Agent**: El bucle conversacional de la superficie web, en `agent/puriq/intake/agent.py`, que procesa un turno de conversación.
- **Intake_Prompt**: El system prompt de la superficie web, en `agent/puriq/intake/prompt.py`, que instruye al LLM con las fases, el catálogo de módulos/paletas y la regla de pedir archivos.
- **Turno**: Un ciclo de conversación que parte de un mensaje del usuario y termina con una respuesta del asistente.
- **Chat_Request**: La entrada de un Turno del canal web: `{mensaje, archivos[]}`.
- **Chat_Response**: La salida de un Turno del canal web: `{respuesta, estado}`, donde `estado` es el Contract_State.
- **LLM_Provider**: La interfaz de proveedor de modelo de lenguaje `LLMProvider` de `agent/puriq/tools/generate_content.py`.
- **Complete_Chat**: El método `complete_chat(messages, tools=None)` que esta fase agrega al LLM_Provider para soportar conversación con tool-use.
- **Complete_Text**: El método existente `complete(prompt) -> str` del LLM_Provider, usado por el enriquecimiento de contenido.
- **Get_Provider**: La fábrica `get_provider()` que resuelve el LLM_Provider según la configuración `PURIQ_LLM_MODE`.
- **Tool_Call**: Una solicitud del LLM de invocar una Intake_Tool con ciertos argumentos, emitida durante Complete_Chat.
- **Tool_Result**: El resultado de ejecutar una Tool_Call, devuelto al LLM para continuar el Turno.
- **Chat_Endpoint**: El endpoint `POST /api/chat` de `agent/puriq/wizard/server.py`.
- **Chat_Panel**: El panel de chat de la UI del wizard, ubicado al lado del Live_Preview.
- **Live_Preview**: El esqueleto/previsualización en vivo del sitio ya existente en la UI del wizard, refrescado por la función `updateSkeleton`.
- **Session_Store**: El archivo `content/.intake-session.json` que guarda el Historial y la Fase actual de la conversación.
- **Historial**: La secuencia de mensajes de usuario y de asistente (y las Tool_Call/Tool_Result asociadas) de la conversación.
- **Fase**: La etapa del Intake_Guion en curso (fases 1–9 del §5 del diseño).
- **Project_Root**: La raíz del proyecto local sobre el que opera el wizard, resuelta por `project_root()` (variable `PURIQ_PROJECT` o el directorio de trabajo).
- **Redact**: La función `puriq.config.redact` / `puriq.config.redact_value` que enmascara valores de secretos en cualquier texto o estructura de salida.
- **Wizard_Error_Response**: La función `puriq.errors.wizard_error_response` que traduce una excepción a una respuesta accionable aplicando siempre Redact.

## Requisitos

### Requisito 1: Bucle conversacional por turno (Chat_Agent, Pieza 3)

**Historia de usuario:** Como encargado de turismo, quiero escribir en lenguaje natural y que Puriq entienda, ejecute las acciones y me responda, para registrar mi sitio conversando en vez de llenar formularios.

#### Criterios de aceptación

1. WHEN el Chat_Agent recibe un Chat_Request para un Project_Root, THE Chat_Agent SHALL construir el contexto del Turno con el Intake_Prompt, el Contract_State obtenido de Get_State y el Historial de la conversación.
2. WHEN el Chat_Agent tiene el contexto del Turno, THE Chat_Agent SHALL invocar Complete_Chat pasando las Intake_Tools disponibles como herramientas.
3. WHEN Complete_Chat emite una o más Tool_Call, THE Chat_Agent SHALL ejecutar cada Tool_Call mediante Run_Intake_Tool y devolver su Tool_Result al LLM para continuar el Turno.
4. WHEN Complete_Chat produce una respuesta de texto sin Tool_Call pendientes, THE Chat_Agent SHALL finalizar el Turno con esa respuesta como texto del asistente.
5. WHEN el Chat_Agent finaliza un Turno, THE Chat_Agent SHALL devolver una Chat_Response con la respuesta del asistente y el Contract_State resultante de Get_State tras las Tool_Call del Turno.
6. THE Chat_Agent SHALL limitar el número de rondas de ejecución de Tool_Call por Turno a un máximo finito configurado, con un valor por defecto de 8 rondas.
7. IF el Chat_Agent alcanza el máximo de rondas de Tool_Call en un Turno, THEN THE Chat_Agent SHALL finalizar el Turno con un mensaje que indique que se alcanzó el límite de acciones y devolver el Contract_State vigente.
8. WHEN el Chat_Agent ejecuta una Tool_Call, THE Chat_Agent SHALL inyectar el Project_Root en los argumentos de la Tool_Call de modo que el LLM no necesite proporcionarlo.

### Requisito 2: System prompt del intake web (Intake_Prompt, Pieza 3)

**Historia de usuario:** Como encargado de turismo, quiero que Puriq conduzca la conversación por fases y me pida lo que necesita, para no tener que saber qué información hace falta ni en qué orden.

#### Criterios de aceptación

1. THE Intake_Prompt SHALL incluir las fases del intake (fases 1–9) coherentes con el Intake_Guion del Intake_Core.
2. THE Intake_Prompt SHALL incluir el catálogo de módulos y el catálogo de paletas de marca disponibles para que el LLM proponga opciones válidas.
3. THE Intake_Prompt SHALL instruir al LLM para solicitar de forma proactiva los archivos que falten (fotos de lugares, logo de marca).
4. WHEN el Chat_Agent construye el contexto de un Turno, THE Chat_Agent SHALL inyectar el Contract_State vigente junto al Intake_Prompt para que el LLM sepa qué piezas faltan.
5. THE Intake_Prompt SHALL instruir al LLM para invocar las Intake_Tools al registrar datos, en lugar de describir los cambios sin ejecutarlos.

### Requisito 3: Extensión del proveedor con tool-use text-only (Complete_Chat, Pieza 4)

**Historia de usuario:** Como desarrollador de Puriq, quiero un método de conversación con tool-use en el proveedor de LLM, para que el loop web pueda pedirle al modelo que invoque las intake tools sin romper el enriquecimiento de contenido existente.

#### Criterios de aceptación

1. THE LLM_Provider SHALL exponer Complete_Chat, que recibe una secuencia de mensajes de conversación y una lista opcional de herramientas, y devuelve la respuesta del modelo indicando texto del asistente o Tool_Call solicitadas.
2. THE LLM_Provider SHALL conservar Complete_Text con su firma y comportamiento actuales, sin cambios para los consumidores existentes.
3. WHEN el Chat_Agent pasa las Intake_Tools a Complete_Chat, THE LLM_Provider SHALL traducir cada spec de Intake_Tool (nombre, descripción e `inputSchema`) al formato de herramientas nativo del proveedor seleccionado.
4. WHEN el modelo solicita ejecutar una herramienta durante Complete_Chat, THE LLM_Provider SHALL devolver el nombre de la herramienta y sus argumentos como una Tool_Call estructurada.
5. WHEN el Chat_Agent aporta los Tool_Result de las Tool_Call previas, THE LLM_Provider SHALL incorporarlos a la conversación antes de solicitar la siguiente respuesta del modelo.
6. THE Complete_Chat SHALL operar en modo text-only, sin recibir ni transmitir bytes de imágenes ni contenido multimodal al modelo.

### Requisito 4: Proveedor configurable (Bedrock y compatible con OpenAI/local)

**Historia de usuario:** Como desarrollador de Puriq, quiero elegir por configuración entre Bedrock Claude y un proveedor compatible con OpenAI, para demostrar el camino AWS-native del pitch y a la vez poder prototipar el chat sin credenciales de AWS.

#### Criterios de aceptación

1. THE Chat_Agent SHALL resolver el LLM_Provider mediante Get_Provider, usando el mismo mecanismo de configuración `PURIQ_LLM_MODE` que emplea el enriquecimiento de contenido.
2. WHERE la configuración selecciona el modo Bedrock, THE LLM_Provider SHALL implementar Complete_Chat con el tool-use nativo de Bedrock Claude.
3. WHERE la configuración selecciona el modo compatible con OpenAI, THE LLM_Provider SHALL implementar Complete_Chat con el tool-use del endpoint compatible con OpenAI, admitiendo un endpoint local para prototipar sin credenciales de AWS.
4. IF la configuración selecciona un modo de proveedor cuyo backend no soporta tool-use en Complete_Chat, THEN THE LLM_Provider SHALL rechazar la operación y devolver un mensaje que nombre la variable de configuración a ajustar.
5. IF una credencial requerida por el proveedor seleccionado no está definida, THEN THE LLM_Provider SHALL rechazar la operación y devolver un mensaje que nombre la variable de entorno faltante sin exponer valores de secretos.

### Requisito 5: Ejecución de tool-calls reutilizando el núcleo de intake (Pieza 3)

**Historia de usuario:** Como desarrollador de Puriq, quiero que el loop web ejecute las acciones a través del mismo núcleo de intake que usa el MCP, para no duplicar lógica ni divergir en comportamiento entre superficies.

#### Criterios de aceptación

1. WHEN el Chat_Agent ejecuta una Tool_Call, THE Chat_Agent SHALL despacharla mediante Run_Intake_Tool del Intake_Core, sin reimplementar la lógica de la Intake_Tool.
2. THE Chat_Agent SHALL exponer al LLM las herramientas declaradas en Intake_Tool_Specs, identificadas por los nombres de Intake_Tool_Names.
3. IF el LLM solicita una Tool_Call con un nombre que no pertenece a Intake_Tool_Names, THEN THE Chat_Agent SHALL devolver al LLM un Tool_Result de error que indique que la herramienta no existe, sin alterar el Contrato.
4. WHEN una Tool_Call ejecutada por Run_Intake_Tool devuelve un error accionable, THE Chat_Agent SHALL entregar ese error al LLM como Tool_Result para que informe al usuario y proponga una corrección.
5. WHEN una Tool_Call de escritura se ejecuta correctamente, THE Chat_Agent SHALL reflejar el estado resultante en el Contract_State que devuelve al final del Turno.

### Requisito 6: Canal web del chat (Chat_Endpoint, Pieza 6)

**Historia de usuario:** Como encargado de turismo, quiero enviar mis mensajes desde la web del wizard y recibir la respuesta con el estado actualizado, para conversar con Puriq desde el navegador.

#### Criterios de aceptación

1. WHEN el Chat_Endpoint recibe un Chat_Request, THE Chat_Endpoint SHALL ejecutar un Turno del Chat_Agent sobre el Project_Root y responder con la Chat_Response correspondiente.
2. THE Chat_Endpoint SHALL aceptar en el Chat_Request un mensaje de texto y una lista opcional de archivos.
3. WHEN el Chat_Endpoint responde un Turno, THE Chat_Endpoint SHALL incluir la respuesta del asistente y el Contract_State resultante.
4. IF el procesamiento del Turno falla, THEN THE Chat_Endpoint SHALL responder con un mensaje accionable traducido por Wizard_Error_Response, sin exponer valores de secretos ni trazas crudas.
5. IF el procesamiento del Turno falla, THEN THE Chat_Endpoint SHALL dejar el Contrato persistido sin cambios respecto a las Tool_Call que no se completaron.

### Requisito 7: Panel de chat y previsualización en vivo (Pieza 6)

**Historia de usuario:** Como encargado de turismo, quiero ver el sitio armándose a medida que converso, para tener retroalimentación inmediata de cada dato que registro.

#### Criterios de aceptación

1. THE Chat_Panel SHALL presentarse en la UI del wizard junto al Live_Preview.
2. WHEN el usuario envía un mensaje desde el Chat_Panel, THE Chat_Panel SHALL enviarlo al Chat_Endpoint y mostrar la respuesta del asistente en el Historial visible de la conversación.
3. WHEN el Chat_Panel recibe una Chat_Response, THE Chat_Panel SHALL refrescar el Live_Preview a partir del Contract_State recibido reutilizando la función `updateSkeleton` existente.
4. WHILE el Chat_Endpoint procesa un Turno, THE Chat_Panel SHALL indicar visualmente que la respuesta está en curso.
5. IF el Chat_Endpoint devuelve un error, THEN THE Chat_Panel SHALL mostrar el mensaje accionable recibido sin bloquear el envío de mensajes posteriores.

### Requisito 8: Alcance de archivos en el chat text-only (Pieza 6)

**Historia de usuario:** Como encargado de turismo, quiero mencionar en el chat las fotos que ya subí, para que Puriq las reconozca y pueda asociarlas al lugar correspondiente, aunque en esta fase el chat no interprete su contenido.

#### Criterios de aceptación

1. THE Chat_Endpoint SHALL interpretar la lista de archivos del Chat_Request como referencias a assets ya cargados mediante el flujo de assets existente, no como binarios.
2. WHEN el Chat_Request incluye referencias a archivos, THE Chat_Agent SHALL incluir esas referencias en el contexto textual del Turno para que el asistente pueda reconocerlas.
3. THE Chat_Agent SHALL operar sin transmitir bytes de imágenes al LLM y sin extraer texto de PDFs.
4. WHERE la conversación identifica un Place o Event de destino para una referencia de archivo, THE Chat_Agent SHALL asociarla mediante la Intake_Tool `attach_asset` existente.

### Requisito 9: Persistencia del estado de sesión (Session_Store, Pieza 7)

**Historia de usuario:** Como encargado de turismo, quiero que Puriq recuerde la conversación entre visitas, para retomar el registro donde lo dejé sin volver a explicar todo.

#### Criterios de aceptación

1. WHEN un Turno finaliza, THE Chat_Agent SHALL persistir el Historial actualizado y la Fase actual en el Session_Store del Project_Root.
2. THE Session_Store SHALL almacenarse en `content/.intake-session.json` dentro del Project_Root.
3. THE Chat_Agent SHALL aplicar Redact al Historial y a la Fase antes de escribirlos en el Session_Store, de modo que ningún valor de secreto quede persistido.
4. WHEN el Chat_Agent persiste el Session_Store, THE Chat_Agent SHALL tratar el Contrato en disco como la fuente de verdad y el Session_Store solo como continuidad de la conversación.

### Requisito 10: Continuidad de la conversación (Pieza 7)

**Historia de usuario:** Como encargado de turismo, quiero que al volver a abrir el wizard la conversación siga donde iba, para no empezar la charla de cero.

#### Criterios de aceptación

1. WHEN el Chat_Agent inicia un Turno y existe un Session_Store para el Project_Root, THE Chat_Agent SHALL cargar el Historial y la Fase previos como contexto del Turno.
2. IF el Session_Store no existe o su contenido no es legible, THEN THE Chat_Agent SHALL iniciar la conversación con un Historial vacío sin fallar.
3. WHEN el Chat_Agent carga un Historial previo, THE Chat_Agent SHALL derivar los Faltantes del Contract_State vigente y no del Historial, para que el estado refleje el Contrato en disco.

### Requisito 11: Seguridad, alcance local y protección de secretos (transversal)

**Historia de usuario:** Como usuario avanzado, quiero que el chat corra en local y no exponga credenciales, para conversar de forma segura sin filtrar secretos.

#### Criterios de aceptación

1. THE Chat_Endpoint SHALL atenderse únicamente en la interfaz local `127.0.0.1` del servidor del wizard.
2. THE Chat_Endpoint SHALL aplicar Redact a toda Chat_Response y a todo mensaje de error, de modo que ningún valor de secreto aparezca en la salida.
3. WHEN el LLM_Provider usa credenciales del proveedor seleccionado, THE LLM_Provider SHALL leerlas mediante el acceso a configuración que las registra como secretos para su enmascarado por Redact.
4. WHEN se produce un error durante un Turno, THE Chat_Endpoint SHALL traducirlo con Wizard_Error_Response sin incluir trazas crudas ni valores de secretos.
