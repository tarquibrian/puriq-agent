# Documento de Requisitos

## Introducción

Este spec cubre la **tercera fase (Hitos 3 y 4)** de la capa conversacional descrita en `docs/registro-conversacional.md`: la **ingesta multimodal**, que completa la **Pieza 5** (ingesta e interpretación de archivos) y la **parte de visión de la Pieza 4** (extensión multimodal del proveedor de LLM). El encargado de turismo ya puede **conversar** con Puriq por dos superficies (MCP y web); esta fase le permite además **aportar imágenes y PDFs** y que Puriq los **interprete**: describir una foto para ayudar a completar su texto alternativo y la descripción del lugar/evento, y **destilar** el texto de un PDF a contenido del contrato (descripciones, Q&A, datos históricos), siempre **confirmando con el usuario antes de escribir**.

Esta fase se apoya en las dos fases anteriores **ya implementadas**:

- **Hito 1 (spec `conversational-intake-mcp`)**: el núcleo de intake `agent/puriq/intake/tools.py`, con las 12 intake tools tipadas (`set_site`, `configure_modules`, `configure_landing`, `add_place`, `add_event`, `edit_item`, `remove_item`, `set_brand`, `add_qa`, `attach_asset`, `get_state`, `build`), `INTAKE_TOOL_SPECS`, `INTAKE_GUION`, `run_intake_tool` y `get_state`. `attach_asset` **ya** acepta el binario por `content_base64` o `source_path` y lo guarda/asocia **sin interpretarlo** (sin visión); los límites y la validación (`IMAGE_EXTS`, `MAX_ASSET_BYTES`) ya existen. El MCP (`agent/puriq/mcp/server.py`) expone el núcleo y el recurso `intake://guion`.
- **Hito 2 (spec `conversational-web-chat`)**: el loop web `ChatAgent` (`agent/puriq/intake/agent.py`), `Intake_Prompt` (`agent/puriq/intake/prompt.py`), `Session_Store` (`agent/puriq/intake/session.py`) y el proveedor con tool-use en `agent/puriq/tools/generate_content.py`, con `complete_chat(messages, tools=None)` **text-only** para Bedrock (tool-use nativo de Claude vía `invoke_model`), compatible con OpenAI (function calling) y Ollama (que lo rechaza). El modelo de mensajes neutral (`Message`/`ToolCall`/`ToolResult`/`ChatResult`) y el canal web `POST /api/chat` + `Chat_Panel` operan en modo **text-only** (los archivos se tratan como **referencias** textuales, sin binarios al LLM).

Esta fase construye sobre esos cimientos **sin romperlos**: reutiliza `attach_asset`, `run_intake_tool`, `INTAKE_TOOL_SPECS`, el `ChatAgent`, el modelo de mensajes neutral y el proveedor configurable (`get_provider()` / `PURIQ_LLM_MODE`).

### Decisiones del usuario que este spec cierra (§7 del documento)

El documento dejaba pendientes decisiones que esta fase **cierra**:

- **Alcance de visión**: se **describe** la imagen (requiere un proveedor multimodal) para autocompletar el `alt` y ayudar la descripción, además de guardarla como asset.
- **PDFs**: se **destilan a contenido del contrato** (descripciones, Q&A, datos históricos) vía las intake tools existentes; **el PDF no se publica**.
- **Confirmación**: ningún contenido derivado por visión o por destilado de PDF se escribe en el contrato **sin la confirmación del usuario**.
- **Superficies**: la ingesta multimodal funciona en **ambas**:
  - **Web (Superficie B)**: Puriq trae su LLM (Bedrock multimodal) y hace la visión y la extracción/destilado de PDF directamente en el loop del chat; el canal web acepta los **binarios reales**.
  - **MCP (Superficie A)**: el LLM del **cliente externo** (Claude Desktop/Kiro) hace la visión sobre las imágenes que el cliente le pasa; **Puriq aporta la extracción de PDF y el guardado** (`attach_asset` ya existe). El binario viaja por MCP como base64 o por ruta local.

### Alcance y exclusiones

Este spec abarca **exclusivamente**:

- **Pieza 5 — Ingesta e interpretación de archivos** (`agent/puriq/intake/ingest.py`): un router por tipo (imagen vs PDF) y su comportamiento en cada superficie.
- **Pieza 4 (parte de visión)** — extensión multimodal de `complete_chat` para transportar imágenes al modelo, en Bedrock Claude multimodal (nativo, camino AWS del pitch) y en el compatible con OpenAI (visión), conviviendo con el `complete_chat` text-only del Hito 2.
- La aceptación de **binarios reales** por el canal web `POST /api/chat` y el transporte de binarios por MCP (base64 o ruta) para la extracción de PDF.
- La **dependencia nueva** de extracción de texto de PDF, con versión fijada.

Queda **explícitamente fuera de alcance**:

- Reimplementar el núcleo de intake, el loop del chat, la sesión o el proveedor base (existen del Hito 1 y 2). Esta fase los **extiende**.
- La visión del lado de Puriq en la superficie **MCP**: en MCP la visión la ejecuta el LLM del cliente externo.
- Otros formatos de archivo distintos de imágenes soportadas y PDF (p. ej. video, audio, hojas de cálculo).
- Publicar el PDF crudo o cualquier documento fuente en el sitio construido.

Este documento define QUÉ debe hacer la ingesta multimodal, no CÓMO implementarla (eso corresponde al diseño).

## Glosario

- **Puriq**: El agente completo en Python (CLI + core + tools + wizard + MCP + intake).
- **Contrato**: El conjunto de documentos JSON validados contra `schemas/` (`tourism-data.json`, `site.config.json`, `theme.tokens.json`), más `assets/` y `content/`.
- **Intake_Core**: El núcleo de intake ya implementado en `agent/puriq/intake/tools.py`.
- **Intake_Tool**: Una de las acciones de registro del Intake_Core (incluye `attach_asset`, `add_qa`, `edit_item`, `add_place`).
- **Run_Intake_Tool**: La función `run_intake_tool(name, arguments)` del Intake_Core que despacha una Intake_Tool y traduce sus errores a una respuesta accionable y redactada.
- **Attach_Asset**: La Intake_Tool existente que guarda una imagen en el Assets_Dir (por `content_base64` o `source_path`) y la asocia a un Place o Event, sin interpretarla.
- **Assets_Dir**: El directorio `<proyecto>/assets` donde se guardan las imágenes del sitio.
- **Ingest_Router**: El módulo nuevo `agent/puriq/intake/ingest.py` que enruta un Archivo_Entrante según su tipo (imagen o PDF) al tratamiento correspondiente.
- **Archivo_Entrante**: Un archivo aportado por el usuario para la ingesta, transportado como binario (bytes), base64 o ruta local, con su nombre y tipo.
- **Imagen**: Un Archivo_Entrante cuyo formato pertenece a los formatos de imagen soportados (`IMAGE_EXTS`).
- **PDF**: Un Archivo_Entrante en formato PDF (folleto, historia, ficha del municipio).
- **Vision**: La capacidad de un proveedor de LLM multimodal de recibir una Imagen y producir texto que la describe.
- **Alt_Text**: El texto alternativo accesible (`alt`) de una imagen asociada a un Place o Event.
- **Contenido_Derivado**: El contenido propuesto por Puriq a partir de una Imagen (Alt_Text, ayuda a la descripción) o de un PDF (descripciones, Q&A, datos históricos), antes de escribirse en el Contrato.
- **Confirmacion**: El acto explícito del usuario de aceptar el Contenido_Derivado propuesto antes de que se escriba en el Contrato.
- **PDF_Extractor**: El componente que extrae el texto de un PDF usando la PDF_Library.
- **Texto_Extraido**: El texto plano obtenido de un PDF por el PDF_Extractor.
- **PDF_Library**: La biblioteca de extracción de texto de PDF que se agrega como dependencia, con versión fijada.
- **Extract_PDF_Tool**: La tool MCP nueva que recibe un PDF (base64 o ruta local) y devuelve su Texto_Extraido, para que el LLM del cliente externo lo destile.
- **Complete_Chat**: El método `complete_chat` del LLM_Provider, extendido en esta fase para transportar imágenes al modelo además de texto y herramientas.
- **Complete_Chat_TextOnly**: El comportamiento text-only de `complete_chat` del Hito 2, que esta fase debe preservar cuando no se aportan imágenes.
- **LLM_Provider**: La interfaz de proveedor de modelo de lenguaje `LLMProvider` de `agent/puriq/tools/generate_content.py`.
- **Get_Provider**: La fábrica `get_provider()` que resuelve el LLM_Provider según `PURIQ_LLM_MODE`.
- **Chat_Agent**: El bucle conversacional de la superficie web (`agent/puriq/intake/agent.py`).
- **Intake_Prompt**: El system prompt de la superficie web (`agent/puriq/intake/prompt.py`).
- **Chat_Endpoint**: El endpoint `POST /api/chat` de `agent/puriq/wizard/server.py`.
- **Chat_Panel**: El panel de chat de la UI del wizard.
- **MCP_Server**: El servidor MCP `tourism-builder` (`agent/puriq/mcp/server.py`).
- **MCP_Client**: Un cliente MCP externo (Claude Desktop, Kiro, u otro) que trae su propio LLM.
- **IMAGE_EXTS**: El conjunto de extensiones de imagen soportadas, definido en `agent/puriq/wizard/assets.py`.
- **MAX_ASSET_BYTES**: El límite de tamaño de un asset (10 MiB), definido en `agent/puriq/wizard/assets.py`.
- **Redact**: Las funciones `puriq.config.redact` / `puriq.config.redact_value` que enmascaran valores de secretos en cualquier salida.
- **Wizard_Error_Response**: La función `puriq.errors.wizard_error_response` que traduce una excepción a una respuesta accionable aplicando siempre Redact.

## Requisitos

### Requisito 1: Router de ingesta por tipo de archivo (Ingest_Router, Pieza 5)

**Historia de usuario:** Como encargado de turismo, quiero aportar imágenes y PDFs y que Puriq sepa qué hacer con cada uno, para no tener que explicar el tipo de archivo ni el tratamiento.

#### Criterios de aceptación

1. WHEN el Ingest_Router recibe un Archivo_Entrante, THE Ingest_Router SHALL determinar su tipo a partir de su extensión y clasificarlo como Imagen, PDF o tipo no soportado.
2. WHEN el Ingest_Router clasifica un Archivo_Entrante como Imagen, THE Ingest_Router SHALL enrutarlo al tratamiento de imágenes (guardado como asset y, con Vision disponible, descripción para Contenido_Derivado).
3. WHEN el Ingest_Router clasifica un Archivo_Entrante como PDF, THE Ingest_Router SHALL enrutarlo al tratamiento de PDF (extracción de Texto_Extraido para destilado).
4. IF el Archivo_Entrante no es una Imagen soportada ni un PDF, THEN THE Ingest_Router SHALL rechazar la operación y devolver un mensaje que liste los tipos de archivo soportados.
5. THE Ingest_Router SHALL abstenerse de escribir Contenido_Derivado en el Contrato de forma directa, delegando toda escritura en las Intake_Tools existentes (`attach_asset`, `edit_item`, `add_place`, `add_qa`).

### Requisito 2: Ingesta de imágenes con visión (Superficie web, Pieza 5)

**Historia de usuario:** Como encargado de turismo, quiero mandar una foto de un lugar y que Puriq la guarde y proponga su texto alternativo y una descripción, para ilustrar el sitio sin redactar todo a mano.

#### Criterios de aceptación

1. WHEN el Ingest_Router procesa una Imagen en la superficie web, THE Ingest_Router SHALL guardarla como asset y asociarla al Place o Event en contexto mediante la Intake_Tool `attach_asset`.
2. WHERE el proveedor seleccionado soporta Vision, THE Chat_Agent SHALL enviar la Imagen al modelo mediante Complete_Chat para obtener una descripción textual de la Imagen.
3. WHEN el Chat_Agent obtiene la descripción de una Imagen, THE Chat_Agent SHALL proponer al usuario un Alt_Text y una ayuda para la descripción del Place o Event asociado como Contenido_Derivado.
4. WHEN el usuario otorga la Confirmacion del Alt_Text o de la descripción propuestos, THE Chat_Agent SHALL escribirlos en el Place o Event mediante la Intake_Tool `edit_item`.
5. IF el usuario no otorga la Confirmacion del Contenido_Derivado de una Imagen, THEN THE Chat_Agent SHALL abstenerse de escribir el Alt_Text y la descripción propuestos en el Contrato.

### Requisito 3: Ingesta de PDF y destilado a contenido del contrato (Superficie web, Pieza 5)

**Historia de usuario:** Como encargado de turismo, quiero aportar un folleto o una ficha en PDF y que Puriq extraiga su información para poblar descripciones, preguntas y datos históricos, sin publicar el PDF.

#### Criterios de aceptación

1. WHEN el Ingest_Router procesa un PDF en la superficie web, THE PDF_Extractor SHALL extraer el Texto_Extraido del PDF mediante la PDF_Library.
2. WHEN el PDF_Extractor obtiene el Texto_Extraido, THE Chat_Agent SHALL incorporarlo como contexto del turno para proponer Contenido_Derivado (descripciones de lugares, Q&A y datos históricos).
3. WHEN el usuario otorga la Confirmacion del Contenido_Derivado de un PDF, THE Chat_Agent SHALL escribirlo en el Contrato mediante las Intake_Tools existentes (`add_qa`, `edit_item`, `add_place`).
4. IF el usuario no otorga la Confirmacion del Contenido_Derivado de un PDF, THEN THE Chat_Agent SHALL abstenerse de escribir el contenido propuesto en el Contrato.
5. THE Ingest_Router SHALL abstenerse de copiar el PDF al Assets_Dir y de incluirlo en la salida del sitio construido.
6. IF el PDF_Extractor no obtiene texto legible de un PDF, THEN THE PDF_Extractor SHALL devolver un mensaje que indique que no se pudo extraer texto y sugiera una acción.

### Requisito 4: Extensión multimodal de Complete_Chat (Pieza 4, parte de visión)

**Historia de usuario:** Como desarrollador de Puriq, quiero que el proveedor de LLM transporte imágenes al modelo además de texto y herramientas, para que el loop web pueda describir imágenes sin romper el chat text-only existente.

#### Criterios de aceptación

1. THE LLM_Provider SHALL extender Complete_Chat para aceptar una o más imágenes asociadas a los mensajes de conversación, además del texto y las herramientas.
2. WHEN el Chat_Agent aporta una Imagen a Complete_Chat, THE LLM_Provider SHALL transmitir la Imagen al modelo en el formato multimodal nativo del proveedor seleccionado junto con su tipo de medio.
3. WHEN Complete_Chat se invoca sin imágenes, THE LLM_Provider SHALL preservar el comportamiento de Complete_Chat_TextOnly sin cambios para los consumidores existentes.
4. THE LLM_Provider SHALL conservar el método `complete(prompt)` de enriquecimiento de contenido con su firma y comportamiento actuales.
5. WHEN Complete_Chat recibe imágenes y herramientas en el mismo turno, THE LLM_Provider SHALL admitir ambas para que el modelo pueda describir la Imagen y a la vez solicitar Intake_Tools.

### Requisito 5: Proveedor multimodal configurable y degradación accionable (Pieza 4)

**Historia de usuario:** Como desarrollador de Puriq, quiero elegir por configuración el proveedor multimodal y recibir un mensaje claro cuando el proveedor no soporta visión, para demostrar el camino AWS-native y a la vez prototipar sin sorpresas.

#### Criterios de aceptación

1. THE Chat_Agent SHALL resolver el LLM_Provider mediante Get_Provider, usando el mismo mecanismo `PURIQ_LLM_MODE` que el resto del sistema.
2. WHERE la configuración selecciona el modo Bedrock, THE LLM_Provider SHALL implementar Complete_Chat multimodal con la visión nativa de Bedrock Claude.
3. WHERE la configuración selecciona el modo compatible con OpenAI, THE LLM_Provider SHALL implementar Complete_Chat multimodal con la visión del endpoint compatible con OpenAI.
4. IF la configuración selecciona un modo de proveedor cuyo backend no soporta Vision y se aporta una Imagen a Complete_Chat, THEN THE LLM_Provider SHALL rechazar la operación y devolver un mensaje que nombre la variable `PURIQ_LLM_MODE` y los modos con Vision disponibles.
5. IF una credencial requerida por el proveedor seleccionado no está definida, THEN THE LLM_Provider SHALL rechazar la operación y devolver un mensaje que nombre la variable de entorno faltante sin exponer valores de secretos.

### Requisito 6: Aceptación de binarios reales por el canal web (Superficie B, Pieza 5)

**Historia de usuario:** Como encargado de turismo, quiero adjuntar imágenes y PDFs reales en el chat de la web, para que Puriq los interprete en la misma conversación.

#### Criterios de aceptación

1. WHEN el Chat_Endpoint recibe un turno con Archivos_Entrantes binarios, THE Chat_Endpoint SHALL aceptar el binario de cada archivo junto con el mensaje de texto del turno.
2. WHEN el Chat_Endpoint recibe un Archivo_Entrante binario, THE Chat_Endpoint SHALL entregarlo al Chat_Agent para su enrutamiento por el Ingest_Router.
3. THE Chat_Endpoint SHALL seguir aceptando referencias a assets ya subidos (comportamiento text-only del Hito 2) además de los binarios de esta fase.
4. WHEN el Chat_Endpoint responde un turno de ingesta, THE Chat_Endpoint SHALL incluir la respuesta del asistente y el estado resultante del Contrato.

### Requisito 7: Ingesta multimodal por MCP (Superficie A, Pieza 5)

**Historia de usuario:** Como MCP_Client con mi propio LLM, quiero que Puriq me dé la extracción de texto de PDF y el guardado de imágenes, para hacer yo la visión y destilar la información al contrato.

#### Criterios de aceptación

1. THE MCP_Server SHALL exponer la Extract_PDF_Tool, que recibe un PDF como base64 o como ruta local legible por el servidor y devuelve su Texto_Extraido.
2. WHEN el MCP_Client invoca la Extract_PDF_Tool, THE Extract_PDF_Tool SHALL delegar en el PDF_Extractor para obtener el Texto_Extraido y devolverlo redactado.
3. IF el MCP_Client no aporta ni base64 ni ruta local, o aporta ambos, THEN THE Extract_PDF_Tool SHALL rechazar la operación y devolver un mensaje que indique que se requiere exactamente una fuente del binario.
4. THE MCP_Server SHALL conservar la Intake_Tool `attach_asset` existente para guardar imágenes por base64 o ruta local, dejando la Vision sobre esas imágenes al LLM del MCP_Client.
5. WHEN el MCP_Server registra la Extract_PDF_Tool, THE MCP_Server SHALL conservar registradas todas las tools de pipeline, edición e intake ya existentes.

### Requisito 8: Confirmación antes de escribir contenido derivado (transversal)

**Historia de usuario:** Como encargado de turismo, quiero que Puriq me consulte antes de escribir lo que interpretó de mis fotos y PDFs, para conservar el control del contenido de mi sitio.

#### Criterios de aceptación

1. THE Intake_Prompt SHALL instruir al LLM para proponer el Contenido_Derivado de imágenes y PDFs y solicitar la Confirmacion del usuario antes de invocar las Intake_Tools de escritura.
2. WHEN Puriq propone Contenido_Derivado, THE Chat_Agent SHALL presentar el contenido propuesto al usuario en la respuesta del turno antes de escribirlo.
3. IF el usuario rechaza o modifica el Contenido_Derivado propuesto, THEN THE Chat_Agent SHALL respetar la decisión del usuario y no escribir el contenido original propuesto.
4. THE Chat_Agent SHALL escribir el Contenido_Derivado en el Contrato únicamente tras la Confirmacion del usuario.

### Requisito 9: Dependencia de extracción de PDF con versión fijada (Pieza 5)

**Historia de usuario:** Como desarrollador de Puriq, quiero una biblioteca de extracción de PDF mantenida y con versión fijada, para tener una ingesta de PDF reproducible y segura de instalar.

#### Criterios de aceptación

1. THE Puriq SHALL declarar exactamente una PDF_Library de extracción de texto de PDF como dependencia del proyecto.
2. THE Puriq SHALL fijar la PDF_Library a una versión específica en la declaración de dependencias.
3. THE PDF_Extractor SHALL obtener el Texto_Extraido exclusivamente a través de la PDF_Library declarada.
4. WHERE la PDF_Library se declara como dependencia opcional, THE Puriq SHALL indicar el nombre del extra a instalar en el mensaje de error cuando la PDF_Library no esté disponible.

### Requisito 10: Límites de tamaño y validación de archivos (transversal)

**Historia de usuario:** Como usuario avanzado, quiero que Puriq valide el tipo y el tamaño de cada archivo que aporto, para no cargar archivos inválidos ni sobrecargar el sistema.

#### Criterios de aceptación

1. WHEN el Ingest_Router procesa una Imagen, THE Ingest_Router SHALL validar su extensión contra `IMAGE_EXTS` reutilizando `normalize_asset_name`.
2. IF la extensión de una Imagen no pertenece a `IMAGE_EXTS`, THEN THE Ingest_Router SHALL rechazar la operación y devolver un mensaje que liste los formatos de imagen aceptados.
3. IF el tamaño de una Imagen excede `MAX_ASSET_BYTES`, THEN THE Ingest_Router SHALL rechazar la operación y devolver un mensaje que indique el límite de tamaño.
4. THE Ingest_Router SHALL aplicar un límite de tamaño máximo a cada PDF entrante y rechazar un PDF que exceda ese límite con un mensaje que indique el límite.
5. WHEN el Ingest_Router valida el tamaño de un Archivo_Entrante, THE Ingest_Router SHALL comprobarlo sobre los bytes decodificados antes de escribir en disco o invocar al modelo.

### Requisito 11: Seguridad transversal y protección de secretos (transversal)

**Historia de usuario:** Como usuario avanzado, quiero que la ingesta multimodal corra en local, no exponga credenciales y no publique mis documentos fuente, para trabajar de forma segura.

#### Criterios de aceptación

1. THE Chat_Endpoint SHALL atenderse únicamente en la interfaz local `127.0.0.1` del servidor del wizard.
2. THE Ingest_Router SHALL aplicar Redact al Texto_Extraido y a todo Contenido_Derivado antes de devolverlo o incluirlo en una respuesta.
3. WHEN el LLM_Provider usa credenciales del proveedor seleccionado para la Vision, THE LLM_Provider SHALL leerlas mediante el acceso a configuración que las registra como secretos para su enmascarado por Redact.
4. WHEN se produce un error durante la ingesta o la Vision, THE Ingest_Router SHALL traducirlo mediante Wizard_Error_Response sin incluir trazas crudas ni valores de secretos.
5. THE Ingest_Router SHALL abstenerse de persistir el binario de un PDF fuera del procesamiento en memoria necesario para extraer su Texto_Extraido.

### Requisito 12: Guion del intake multimodal (Intake_Prompt y recurso MCP)

**Historia de usuario:** Como encargado de turismo, quiero que Puriq me pida activamente fotos y PDFs y me explique qué hará con ellos, para aprovechar la ingesta multimodal sin conocer los detalles técnicos.

#### Criterios de aceptación

1. THE Intake_Prompt SHALL instruir al LLM para solicitar de forma proactiva imágenes de los lugares y PDFs de contexto en las fases correspondientes del intake.
2. THE Intake_Prompt SHALL instruir al LLM para usar la descripción de una Imagen al proponer el Alt_Text y la descripción del Place o Event asociado.
3. THE Intake_Prompt SHALL instruir al LLM para destilar el Texto_Extraido de un PDF a descripciones, Q&A y datos históricos mediante las Intake_Tools existentes, en lugar de publicar el PDF.
4. THE Intake_Prompt SHALL instruir al LLM para solicitar la Confirmacion del usuario antes de escribir cualquier Contenido_Derivado.
