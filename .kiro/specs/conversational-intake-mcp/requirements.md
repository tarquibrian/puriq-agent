# Documento de Requisitos

## Introducción

Este spec cubre la **primera fase (Hito 1)** de la capa conversacional descrita en `docs/registro-conversacional.md`: dotar a Puriq de un **núcleo de acciones de registro (intake)** y exponerlo por **MCP**, de modo que un cliente MCP externo (Claude Desktop, Kiro, u otro) pueda **conversar** con su propio LLM y construir el contrato de un sitio turístico (los 3 documentos JSON + `assets/` + `content/`) desde cero: crear el sitio, activar módulos, agregar lugares y eventos, definir la marca, la portada y el Q&A, adjuntar imágenes y, finalmente, construir el sitio.

Hoy Puriq expone por MCP la **edición** de contenido existente (`edit_content`, `delete_content`, `query_content`, `bulk_update`, `manage_articles`) y el **pipeline** (`scan_resources`, `import_open_data`, `generate_content`, `build_site`, `deploy`, `analyze_seo`), pero **no** expone el **intake inicial**. Ese es el hueco que llena este spec.

El núcleo de intake es una **capa fina** sobre cimientos que ya existen: constructores puros (`build_place`, `build_event`, `make_coords`, `build_modules`, `build_landing`), validadores (`validate_domain`, `validate_qa_entry`, `validate_deploy_target`), la capa de contrato atómica (`_load_contract`, `merge_document`, `save_contract`), las utilidades de assets (`normalize_asset_name`, `resolve_within_assets`, `IMAGE_EXTS`, `MAX_ASSET_BYTES`), los métodos de edición del core (`Puriq.edit`, `Puriq.delete`) y el pipeline de build (`Puriq.build`). Las intake tools **envuelven** estas piezas: no reimplementan lógica.

Cada intake tool sigue el mismo ciclo: **validar → persistir con `save_contract` (borrador cuando aplica) → devolver el estado nuevo**. La tool `get_state` es central: devuelve el estado del contrato más una lista de **faltantes** que el LLM del cliente usa para saber qué preguntar a continuación. Los errores se traducen a mensajes accionables sin secretos, reutilizando `wizard_error_response` + `Redact`.

Este documento define QUÉ debe hacer cada intake tool y cómo se exponen por MCP, no CÓMO implementarlas (eso corresponde al diseño).

### Alcance y exclusiones

Este spec abarca **exclusivamente** las Piezas 1 y 2 del diseño (Hito 1):

- **Pieza 1 — Intake tools (núcleo compartido):** un módulo nuevo `agent/puriq/intake/tools.py` con las tools `set_site`, `configure_modules`, `add_place`, `add_event`, `edit_item`, `remove_item`, `set_brand`, `configure_landing`, `add_qa`, `attach_asset`, `get_state` y `build`.
- **Pieza 2 — Exposición por MCP:** extender `agent/puriq/mcp/server.py` para registrar las intake tools en `list_tools`/`call_tool`, con el **guion** del intake en las descripciones de las tools y, opcionalmente, en un recurso MCP.

Queda **explícitamente fuera de alcance** de este spec (fases posteriores):

- **Pieza 3 — Loop conversacional propio de la web** (`intake/agent.py`, `intake/prompt.py`).
- **Pieza 4 — Provider LLM con tool-use y visión** (`complete_chat`, multimodal). En MCP el LLM lo aporta el cliente externo.
- **Pieza 5 — Ingesta e interpretación de imágenes/PDFs** (visión para autocompletar `alt`, extracción de texto de PDF). `attach_asset` en esta fase solo guarda/asocia el archivo, no lo interpreta.
- **Pieza 6 — Canal web `POST /api/chat` y la UI de chat.**
- **Pieza 7 — Estado de sesión / historial** (`content/.intake-session.json`).

## Glosario

- **Puriq**: El agente completo en Python (CLI + core + tools + wizard + MCP).
- **Contrato**: El conjunto de documentos JSON validados contra `schemas/`: `tourism-data.json`, `site.config.json` y `theme.tokens.json`.
- **Tourism_Data**: Documento `tourism-data.json`; capa de contenido (site, places, events, categories).
- **Site_Config**: Documento `site.config.json`; capa de configuración (layout, modules, landing, deploy, contact).
- **Theme_Tokens**: Documento `theme.tokens.json`; capa de marca (colors, typography, voice, logo).
- **Place**: Un lugar turístico dentro de `Tourism_Data.places`, identificado por `id`.
- **Event**: Un evento/festividad dentro de `Tourism_Data.events`, identificado por `id`.
- **Assets_Dir**: El directorio `<proyecto>/assets` donde se copian las imágenes del sitio.
- **QA_Store**: El archivo `content/qa.json` del proyecto con la lista de QA_Entry.
- **QA_Entry**: Una entrada de preguntas y respuestas `{"question", "answer"}` del QA_Store.
- **Intake_Tools**: El módulo `agent/puriq/intake/tools.py` que declara las acciones de registro como tools con su JSON Schema, envolviendo los constructores puros existentes.
- **Set_Site**: Intake tool que escribe la identidad del sitio en `Tourism_Data.site` y los datos de publicación/contacto en `Site_Config`.
- **Configure_Modules**: Intake tool que escribe `Site_Config.modules` a partir de una selección ordenada de módulos.
- **Add_Place**: Intake tool que agrega un Place a `Tourism_Data.places`.
- **Add_Event**: Intake tool que agrega un Event a `Tourism_Data.events`.
- **Edit_Item**: Intake tool que edita campos de un Place o Event existente por `id`.
- **Remove_Item**: Intake tool que elimina un Place o Event por `id` con integridad referencial.
- **Set_Brand**: Intake tool que escribe colores, tipografía y voz de la marca en `Theme_Tokens`.
- **Configure_Landing**: Intake tool que escribe `Site_Config.landing` a partir de una selección ordenada de secciones.
- **Add_QA**: Intake tool que anexa un QA_Entry al QA_Store.
- **Attach_Asset**: Intake tool que copia una imagen al Assets_Dir de forma segura y la asocia a un Place o Event.
- **Get_State**: Intake tool de solo lectura que devuelve el estado del Contrato y la lista de Faltantes.
- **Build_Tool**: Intake tool que construye el sitio estático a partir del Contrato persistido.
- **Faltantes**: La lista de piezas del Contrato aún ausentes o incompletas que Get_State devuelve para guiar la siguiente pregunta del intake.
- **Constructor_Puro**: Una función existente sin E/S que transforma datos de entrada en una porción del Contrato (`build_place`, `build_event`, `make_coords`, `build_modules`, `build_landing`).
- **Save_Contract**: La función `save_contract(project, doc, merged)` que valida un documento contra su esquema y lo persiste de forma atómica; para `tourism-data` aplica validación de **borrador** (`coords` opcional por Place).
- **Borrador**: Un estado del Contrato válido para persistir aunque incompleto para publicar; en particular, un Place con solo `address` y sin `coords` (que `geocode` completará en el build).
- **MCP_Server**: El servidor MCP `tourism-builder` (`agent/puriq/mcp/server.py`) que expone las tools a cualquier cliente MCP.
- **MCP_Client**: Un cliente MCP externo (Claude Desktop, Kiro, u otro) que trae su propio LLM y conduce la conversación.
- **Guion_Intake**: El texto que indica qué preguntar y en qué orden (las fases del §5 del diseño), embebido en las descripciones de las tools y, opcionalmente, en un recurso MCP.
- **Slug**: Identificador en formato kebab-case ASCII que cumple el patrón `^[a-z0-9-]+$`, generado con `slugify`.
- **Redact**: La función `puriq.config.redact` que enmascara valores de secretos en cualquier texto de salida o error.
- **Wizard_Error_Response**: La función `puriq.errors.wizard_error_response` que traduce una excepción a una respuesta accionable aplicando siempre Redact.

## Requisitos

### Requisito 1: Núcleo de intake como capa fina sobre los cimientos existentes

**Historia de usuario:** Como desarrollador de Puriq, quiero que las intake tools envuelvan los constructores y validadores que ya existen, para no duplicar lógica ni divergir del comportamiento del wizard.

#### Criterios de aceptación

1. THE Intake_Tools SHALL declarar cada acción de registro como una tool con su nombre, descripción y esquema de entrada en formato JSON Schema, siguiendo el mismo patrón que las tools de `MCP_Server`.
2. WHEN una intake tool construye una porción del Contrato, THE Intake_Tools SHALL delegar en el Constructor_Puro o validador existente correspondiente sin reimplementar su lógica.
3. WHEN una intake tool persiste un cambio en el Contrato, THE Intake_Tools SHALL escribirlo mediante Save_Contract.
4. WHEN una intake tool completa una operación de escritura correctamente, THE Intake_Tools SHALL devolver el estado resultante del Contrato afectado.
5. WHERE una intake tool escribe en `Tourism_Data`, THE Intake_Tools SHALL permitir persistir un Borrador conforme a la validación relajada de Save_Contract.

### Requisito 2: Estado y faltantes del contrato (Get_State)

**Historia de usuario:** Como MCP_Client, quiero consultar el estado actual del contrato y qué falta para completarlo, para saber qué preguntar al usuario a continuación.

#### Criterios de aceptación

1. WHEN Get_State recibe una solicitud, THE Get_State SHALL cargar los tres documentos del Contrato mediante `_load_contract` sin modificarlos.
2. WHEN Get_State devuelve su resultado, THE Get_State SHALL incluir el estado de los tres documentos del Contrato y una lista de Faltantes.
3. IF `Tourism_Data.site` carece de nombre, región o centro del mapa, THEN THE Get_State SHALL incluir en los Faltantes la identidad del sitio con el campo ausente.
4. IF `Site_Config.modules` no tiene ningún módulo habilitado, THEN THE Get_State SHALL incluir en los Faltantes la selección de módulos.
5. IF `Tourism_Data.places` está vacío, THEN THE Get_State SHALL incluir en los Faltantes la carga de lugares.
6. IF `Theme_Tokens` conserva los colores marcadores por defecto del documento base, THEN THE Get_State SHALL incluir en los Faltantes la definición de la marca.
7. WHEN el Contrato contiene toda pieza requerida para construir, THE Get_State SHALL devolver una lista de Faltantes vacía.
8. THE Get_State SHALL aplicar Redact al estado devuelto.

### Requisito 3: Alta de la identidad del sitio (Set_Site)

**Historia de usuario:** Como MCP_Client, quiero registrar el nombre, la región, el centro del mapa y el idioma del sitio, para crear la identidad base del proyecto.

#### Criterios de aceptación

1. WHEN Set_Site recibe un nombre, una región y un centro de mapa con latitud y longitud, THE Set_Site SHALL escribir esos valores en `Tourism_Data.site`.
2. WHERE la solicitud de Set_Site incluye un idioma por defecto, THE Set_Site SHALL escribirlo en `Tourism_Data.site.defaultLocale`.
3. WHERE la solicitud de Set_Site incluye una dirección web, THE Set_Site SHALL validarla con `validate_domain` y escribir el dominio normalizado en `Site_Config.deploy.domain`.
4. IF la dirección web indicada no tiene un formato de dominio válido, THEN THE Set_Site SHALL rechazar la operación y devolver un mensaje que muestre el formato esperado.
5. WHERE la solicitud de Set_Site incluye datos de contacto, THE Set_Site SHALL escribirlos en `Site_Config.contact`.
6. IF la latitud está fuera del rango [-90, 90] o la longitud fuera del rango [-180, 180], THEN THE Set_Site SHALL rechazar la operación y devolver un mensaje que nombre el rango permitido.
7. WHEN Set_Site persiste los cambios correctamente, THE Set_Site SHALL devolver el estado resultante del Contrato.

### Requisito 4: Configuración de módulos (Configure_Modules)

**Historia de usuario:** Como MCP_Client, quiero activar los módulos del sitio (mapa, lugares, eventos, blog, asistente) en un orden, para definir qué secciones tendrá el sitio.

#### Criterios de aceptación

1. WHEN Configure_Modules recibe una selección ordenada de módulos, THE Configure_Modules SHALL construir `Site_Config.modules` mediante `build_modules`.
2. THE Configure_Modules SHALL asignar a cada módulo un `order` entero mayor o igual a 1 coherente con el orden de la selección recibida.
3. IF la selección incluye una clave de módulo fuera del catálogo soportado, THEN THE Configure_Modules SHALL rechazar la operación y devolver un mensaje que liste el catálogo soportado.
4. IF la selección incluye un módulo repetido, THEN THE Configure_Modules SHALL rechazar la operación y devolver un mensaje que indique el módulo repetido.
5. WHEN Configure_Modules persiste los cambios correctamente, THE Configure_Modules SHALL devolver el estado resultante del Contrato.

### Requisito 5: Alta de lugares (Add_Place)

**Historia de usuario:** Como MCP_Client, quiero agregar un lugar con su nombre, categoría y ubicación, para poblar el contenido del sitio uno a uno.

#### Criterios de aceptación

1. WHEN Add_Place recibe un nombre y una categoría, THE Add_Place SHALL construir el Place mediante `build_place` derivando su `id` como un Slug del nombre.
2. WHERE la solicitud de Add_Place incluye latitud y longitud, THE Add_Place SHALL asignar las coordenadas al Place mediante `make_coords`.
3. WHERE la solicitud de Add_Place incluye solo una dirección sin coordenadas, THE Add_Place SHALL conservar la dirección y persistir el Place como Borrador sin inventar coordenadas.
4. IF se indica solo una de las dos coordenadas (latitud o longitud), THEN THE Add_Place SHALL rechazar la operación y devolver un mensaje que indique que se requieren ambas.
5. IF la latitud o la longitud está fuera de su rango permitido, THEN THE Add_Place SHALL rechazar la operación y devolver un mensaje que nombre el rango permitido.
6. WHEN Add_Place agrega un Place, THE Add_Place SHALL anexarlo a `Tourism_Data.places` sin eliminar los lugares existentes.
7. WHEN Add_Place persiste los cambios correctamente, THE Add_Place SHALL devolver el estado resultante del Contrato.

### Requisito 6: Alta de eventos (Add_Event)

**Historia de usuario:** Como MCP_Client, quiero agregar un evento con su fecha y el lugar asociado, para incluir festividades y actividades en el sitio.

#### Criterios de aceptación

1. WHEN Add_Event recibe un nombre y una fecha de inicio, THE Add_Event SHALL construir el Event mediante `build_event` derivando su `id` como un Slug del nombre.
2. WHERE la solicitud de Add_Event incluye fecha de fin, lugar asociado, descripción o recurrencia, THE Add_Event SHALL incluir esos campos en el Event.
3. WHEN Add_Event agrega un Event, THE Add_Event SHALL anexarlo a `Tourism_Data.events` sin eliminar los eventos existentes.
4. WHEN Add_Event persiste los cambios correctamente, THE Add_Event SHALL devolver el estado resultante del Contrato.

### Requisito 7: Edición y eliminación de lugares y eventos (Edit_Item, Remove_Item)

**Historia de usuario:** Como MCP_Client, quiero corregir o retirar un lugar o evento por su identificador, para mantener el contenido del sitio actualizado durante la conversación.

#### Criterios de aceptación

1. WHEN Edit_Item recibe un `id` existente y uno o más campos, THE Edit_Item SHALL delegar en `Puriq.edit` para actualizar solo los campos indicados y preservar los no indicados.
2. WHEN Remove_Item recibe un `id` existente, THE Remove_Item SHALL delegar en `Puriq.delete` para eliminar el Place o Event correspondiente.
3. IF el `id` indicado a Edit_Item o a Remove_Item no corresponde a ningún Place ni Event, THEN THE Intake_Tools SHALL rechazar la operación y devolver un mensaje que indique que el elemento no fue encontrado.
4. WHEN Remove_Item elimina un Place referenciado por Events, THE Remove_Item SHALL dejar el Contrato resultante sin referencias `placeId` que apunten al Place eliminado.
5. WHEN Edit_Item o Remove_Item persiste los cambios correctamente, THE Intake_Tools SHALL devolver el estado resultante del Contrato.

### Requisito 8: Definición de la marca (Set_Brand)

**Historia de usuario:** Como MCP_Client, quiero definir los colores, la tipografía y la voz de la marca, para dar identidad visual al sitio.

#### Criterios de aceptación

1. WHEN Set_Brand recibe colores de marca, THE Set_Brand SHALL escribirlos en `Theme_Tokens.colors`.
2. IF un color indicado no es un valor hexadecimal válido, THEN THE Set_Brand SHALL rechazar la operación y devolver un mensaje que indique el formato de color esperado.
3. WHERE la solicitud de Set_Brand incluye tipografías, THE Set_Brand SHALL escribirlas en `Theme_Tokens.typography`.
4. WHERE la solicitud de Set_Brand incluye una voz de marca, THE Set_Brand SHALL escribirla en `Theme_Tokens.voice`.
5. WHEN Set_Brand persiste los cambios correctamente, THE Set_Brand SHALL devolver el estado resultante del Contrato.

### Requisito 9: Configuración de la portada (Configure_Landing)

**Historia de usuario:** Como MCP_Client, quiero definir las secciones de la portada en un orden, para armar la página de inicio del sitio.

#### Criterios de aceptación

1. WHEN Configure_Landing recibe una selección ordenada de secciones, THE Configure_Landing SHALL construir `Site_Config.landing` mediante `build_landing`.
2. THE Configure_Landing SHALL asignar a cada sección un `order` entero mayor o igual a 1 coherente con el orden de la selección recibida.
3. IF la selección incluye un tipo de sección fuera del catálogo soportado, THEN THE Configure_Landing SHALL rechazar la operación y devolver un mensaje que liste el catálogo soportado.
4. WHEN Configure_Landing persiste los cambios correctamente, THE Configure_Landing SHALL devolver el estado resultante del Contrato.

### Requisito 10: Alta de preguntas y respuestas (Add_QA)

**Historia de usuario:** Como MCP_Client, quiero agregar preguntas y respuestas al asistente, para alimentar la base de conocimiento del sitio.

#### Criterios de aceptación

1. WHEN Add_QA recibe una pregunta y una respuesta, THE Add_QA SHALL validarlas con `validate_qa_entry`.
2. IF la pregunta o la respuesta está vacía o es solo espacios, THEN THE Add_QA SHALL rechazar la operación y devolver un mensaje que nombre el campo faltante.
3. WHEN Add_QA valida un QA_Entry correctamente, THE Add_QA SHALL anexarlo al QA_Store sin eliminar las entradas existentes.
4. WHEN Add_QA persiste el QA_Entry correctamente, THE Add_QA SHALL devolver el estado resultante del QA_Store.

### Requisito 11: Adjuntar imágenes (Attach_Asset)

**Historia de usuario:** Como MCP_Client, quiero adjuntar una imagen y asociarla a un lugar o evento, para ilustrar el contenido del sitio.

#### Criterios de aceptación

1. WHEN Attach_Asset recibe una imagen y su nombre, THE Attach_Asset SHALL normalizar el nombre con `normalize_asset_name` a la forma `slug.ext`.
2. IF la extensión de la imagen no pertenece a los formatos soportados (`IMAGE_EXTS`), THEN THE Attach_Asset SHALL rechazar la operación y devolver un mensaje que liste los formatos aceptados.
3. IF el tamaño de la imagen excede `MAX_ASSET_BYTES`, THEN THE Attach_Asset SHALL rechazar la operación y devolver un mensaje que indique el límite de tamaño.
4. WHEN Attach_Asset resuelve la ruta destino, THE Attach_Asset SHALL confirmar con `resolve_within_assets` que queda contenida en el Assets_Dir y rechazar cualquier ruta que escape de ese directorio.
5. WHEN Attach_Asset copia la imagen correctamente, THE Attach_Asset SHALL asociarla al Place o Event indicado en el Contrato.
6. IF el Place o Event indicado para asociar la imagen no existe, THEN THE Attach_Asset SHALL rechazar la operación y devolver un mensaje que indique que el elemento no fue encontrado.
7. WHEN Attach_Asset persiste los cambios correctamente, THE Attach_Asset SHALL devolver el estado resultante del Contrato.

### Requisito 12: Construcción del sitio (Build_Tool)

**Historia de usuario:** Como MCP_Client, quiero construir el sitio a partir del contrato registrado, para obtener la salida estática lista para previsualizar o publicar.

#### Criterios de aceptación

1. WHEN Build_Tool recibe una solicitud de construcción, THE Build_Tool SHALL delegar en `Puriq.build` sobre el Contrato persistido.
2. WHEN Build_Tool completa la construcción correctamente, THE Build_Tool SHALL devolver la ruta del directorio `dist/` generado.
3. IF la construcción falla por un Contrato incompleto o inválido, THEN THE Build_Tool SHALL devolver un mensaje accionable que identifique la causa sin exponer valores de secretos.

### Requisito 13: Exposición de las intake tools por MCP (MCP_Server)

**Historia de usuario:** Como MCP_Client, quiero descubrir e invocar las intake tools por MCP, para conducir el registro conversacional con mi propio LLM sin tocar la web de Puriq.

#### Criterios de aceptación

1. WHEN el MCP_Server se inicia, THE MCP_Server SHALL registrar en su `list_tools` las intake tools `set_site`, `configure_modules`, `add_place`, `add_event`, `edit_item`, `remove_item`, `set_brand`, `configure_landing`, `add_qa`, `attach_asset`, `get_state` y `build`.
2. WHEN el MCP_Server expone una intake tool, THE MCP_Server SHALL declarar su esquema de entrada conforme a la firma de la intake tool subyacente.
3. WHEN un MCP_Client invoca una intake tool, THE MCP_Server SHALL delegar en la implementación de Intake_Tools sin duplicar la lógica.
4. THE MCP_Server SHALL incluir el Guion_Intake en las descripciones de las intake tools, indicando qué preguntar y en qué orden.
5. WHERE el MCP_Server expone un recurso MCP con las instrucciones del intake, THE MCP_Server SHALL ofrecer en ese recurso el Guion_Intake para que el MCP_Client lo cargue como contexto.
6. WHEN el MCP_Server registra las intake tools, THE MCP_Server SHALL conservar registradas las tools de edición y de pipeline ya existentes.

### Requisito 14: Validación, persistencia atómica y protección de secretos (transversal)

**Historia de usuario:** Como usuario avanzado, quiero que cada acción de registro sea validada, atómica y segura, para no corromper el contrato ni exponer secretos durante la conversación.

#### Criterios de aceptación

1. WHEN una intake tool transforma un documento del Contrato, THE Intake_Tools SHALL validarlo contra su esquema en `schemas/` mediante Save_Contract antes de escribirlo.
2. IF la validación previa a escribir falla, THEN THE Intake_Tools SHALL no escribir el documento y devolver un mensaje que nombre el documento y el campo que incumple.
3. WHEN una intake tool rechaza una operación, THE Intake_Tools SHALL dejar el Contrato persistido sin cambios.
4. WHEN una intake tool encuentra un error, THE Intake_Tools SHALL traducirlo a un mensaje accionable mediante Wizard_Error_Response.
5. THE Intake_Tools SHALL aplicar Redact a todo mensaje de error y a todo estado devuelto, de modo que ningún valor de secreto aparezca en la respuesta.
6. WHEN una intake tool genera el `id` o el nombre de archivo de un elemento, THE Intake_Tools SHALL derivarlo con `slugify` para cumplir el patrón `^[a-z0-9-]+$`.
