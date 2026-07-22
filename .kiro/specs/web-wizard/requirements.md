# Documento de Requisitos

## Introducción

El **wizard web local** es la interfaz primaria para el encargado de turismo no programador: un flujo guiado, por pasos y **basado en formularios** (no conversacional) que lleva de recursos dispersos a un sitio publicado. El recorrido es: elegir módulos → cargar/ingresar recursos (fotos, lugares, eventos, logo, Q&A) → definir marca (colores, tipografía, tono) → generar → previsualizar → publicar.

Hoy `agent/puriq/wizard/server.py` y `wizard/static/index.html` son *stubs* (una tarjeta estática con un botón deshabilitado y sin endpoints). Este spec cubre la implementación real de la experiencia de intake.

El wizard es una **capa web fina** sobre `puriq.core.Puriq` (collect/build/preview/deploy) y `puriq.tools`, análoga a como `cli.py` es una capa fina sobre el mismo core. No reimplementa las tools ni genera código de módulos: **configura módulos (on/off/orden) y edita contenido y marca**. Produce y edita los tres documentos del contrato (`tourism-data.json`, `site.config.json`, `theme.tokens.json`) más `/assets` y `/content`, y **nunca escribe un documento sin validarlo** contra los esquemas de `schemas/`. La edición es **segura y por capas**: el usuario vuelve periódicamente a agregar un evento, cambiar un banner o subir fotos, y sus ediciones no pisan el contenido existente.

Es una aplicación **local** (FastAPI + uvicorn) que escucha en localhost para un único usuario local, y sirve endpoints de carga de archivos y de disparo de build, por lo que las consideraciones de acceso y de no exposición de secretos son parte del contrato.

**Fuera de alcance (no incluir):** el chatbot RAG del visitante (chatweb), el panel de administración con login y roles, y el i18n avanzado. El wizard se limita al ciclo intake → build → preview → publish. La captura de Q&A se almacena como contenido para un chatweb futuro, pero el wizard no lo consume.

## Glosario

- **Wizard**: La aplicación web local completa (servidor FastAPI + UI de formularios) que guía el intake, build, preview y publish.
- **Wizard_Server**: El backend FastAPI en `agent/puriq/wizard/server.py` que expone los endpoints del Wizard.
- **Wizard_UI**: La interfaz de formularios por pasos servida en `wizard/static/`.
- **Core**: `puriq.core.Puriq`, con las fases `collect`, `build`, `preview` y `deploy`.
- **Contrato**: El conjunto de tres documentos JSON validados contra `schemas/`.
- **Tourism_Data**: Documento `tourism-data.json`; capa de contenido (site, places, events, categories).
- **Site_Config**: Documento `site.config.json`; capa de estructura (layout, módulos, hero, deploy).
- **Theme_Tokens**: Documento `theme.tokens.json`; capa de marca (colores, tipografía, voz, logo).
- **Place**: Un lugar turístico dentro de `Tourism_Data.places`.
- **Event**: Un evento/festividad dentro de `Tourism_Data.events`.
- **Module**: Una funcionalidad componible del sitio (`map`, `places`, `events`, `blog`, `chatweb`) configurable en `Site_Config.modules` con `enabled` y `order`.
- **Asset**: Un archivo binario subido por el usuario (foto, video, logo) almacenado en el directorio `/assets` del proyecto.
- **QA_Entry**: Un par pregunta/respuesta capturado como conocimiento para el chatbot futuro, almacenado en `/content` del proyecto.
- **Deploy_Target**: El destino de publicación soportado (`aws-amplify`, `s3-cloudfront`, `static-export`, `vercel`, `netlify`).
- **Build_Progress**: El flujo de mensajes de progreso emitidos durante `collect`/`build`, transmitido en vivo al Wizard_UI.
- **Schema_Validation**: La validación de un documento del contrato contra su esquema en `schemas/` mediante `puriq.schemas`.
- **Slug**: Identificador en formato kebab-case ASCII que cumple el patrón `^[a-z0-9-]+$`.
- **Loopback**: La interfaz de red local `127.0.0.1` (localhost).
- **Redact**: La función `puriq.config.redact` que enmascara valores de secretos en cualquier texto de salida.

## Requisitos

### Requisito 1: Flujo guiado por pasos basado en formularios

**Historia de usuario:** Como encargado de turismo no programador, quiero un asistente por pasos con formularios claros, para construir mi sitio sin editar JSON ni escribir código.

#### Criterios de aceptación

1. WHEN el usuario abre la raíz del Wizard, THE Wizard_Server SHALL servir el Wizard_UI con el flujo de pasos: selección de módulos, intake de recursos, carga de assets, captura de Q&A, definición de marca, generación, previsualización y publicación.
2. WHEN el usuario completa un paso, THE Wizard_UI SHALL permitir avanzar al paso siguiente y regresar al paso anterior sin perder los datos ya ingresados en la sesión.
3. WHEN el usuario ingresa datos en un paso, THE Wizard_Server SHALL persistir esos datos en el documento del Contrato correspondiente del proyecto.
4. THE Wizard_UI SHALL presentar la experiencia mediante formularios, sin requerir interacción conversacional.
5. WHERE existe un proyecto con documentos del Contrato previos, THE Wizard_Server SHALL cargar los valores existentes en los campos del Wizard_UI al iniciar el flujo.

### Requisito 2: Selección y orden de módulos

**Historia de usuario:** Como encargado de turismo, quiero elegir qué secciones tendrá mi sitio y en qué orden, para armar la estructura que necesito sin tocar el diseño base.

#### Criterios de aceptación

1. WHEN el usuario activa o desactiva un Module en el Wizard_UI, THE Wizard_Server SHALL escribir el valor `enabled` correspondiente en `Site_Config.modules`.
2. WHEN el usuario ordena los Modules activados, THE Wizard_Server SHALL asignar a cada Module un valor `order` entero mayor o igual a 1 que refleje ese orden.
3. THE Wizard_Server SHALL restringir los Modules configurables al catálogo soportado (`map`, `places`, `events`, `blog`, `chatweb`).
4. WHEN el usuario guarda la selección de Modules, THE Wizard_Server SHALL aplicar Schema_Validation al `Site_Config` contra `site-config.schema.json` antes de escribirlo.
5. IF la selección de Modules produce un `Site_Config` que no cumple `site-config.schema.json`, THEN THE Wizard_Server SHALL rechazar el guardado y devolver un mensaje que identifique el campo inválido.

### Requisito 3: Intake de recursos por formulario (sitio, lugares, eventos)

**Historia de usuario:** Como encargado de turismo, quiero ingresar los datos de mi región, mis lugares y mis eventos en formularios, para construir el contenido sin cargar CSV ni JSON.

#### Criterios de aceptación

1. WHEN el usuario completa el formulario de sitio con nombre, región, idioma por defecto y centro del mapa, THE Wizard_Server SHALL escribir esos valores en `Tourism_Data.site`.
2. WHEN el usuario agrega un Place con nombre y categoría, THE Wizard_Server SHALL crear una entrada en `Tourism_Data.places` con un `id` en formato Slug derivado del nombre.
3. WHEN el usuario agrega un Event con nombre y fecha de inicio, THE Wizard_Server SHALL crear una entrada en `Tourism_Data.events` con un `id` en formato Slug derivado del nombre.
4. WHERE el usuario ingresa una dirección para un Place sin coordenadas, THE Wizard_Server SHALL conservar la dirección para que Geocode calcule las coordenadas durante la fase de generación.
5. WHERE el usuario ingresa latitud y longitud para un Place, THE Wizard_Server SHALL asignar `coords` con `lat` entre -90 y 90 y `lng` entre -180 y 180.
6. IF el usuario ingresa una latitud o longitud fuera de los rangos válidos, THEN THE Wizard_Server SHALL rechazar el valor y devolver un mensaje que indique el rango permitido.
7. WHEN el usuario guarda datos de contenido, THE Wizard_Server SHALL aplicar Schema_Validation al `Tourism_Data` contra `tourism-data.schema.json` antes de escribirlo.

### Requisito 4: Carga de fotos, logo y assets

**Historia de usuario:** Como encargado de turismo, quiero subir fotos de los lugares, imágenes de eventos y el logo de la provincia, para que mi sitio tenga imágenes propias.

#### Criterios de aceptación

1. WHEN el usuario sube un Asset de imagen, THE Wizard_Server SHALL almacenar el archivo en el directorio `/assets` del proyecto y devolver la ruta relativa del Asset.
2. WHEN el Wizard_Server almacena un Asset asociado a un Place o Event, THE Wizard_Server SHALL agregar la ruta relativa del Asset al campo `images` de ese Place o Event.
3. WHEN el usuario sube un logo, THE Wizard_Server SHALL almacenar el archivo en `/assets` y escribir su ruta relativa en `Theme_Tokens.logo`.
4. IF el usuario sube un archivo cuyo tipo no está entre los formatos de imagen soportados, THEN THE Wizard_Server SHALL rechazar la carga y devolver un mensaje que indique los formatos aceptados.
5. IF el tamaño de un Asset supera el límite configurado, THEN THE Wizard_Server SHALL rechazar la carga y devolver un mensaje que indique el tamaño máximo permitido.
6. WHEN el Wizard_Server almacena un Asset, THE Wizard_Server SHALL asignar un nombre de archivo en formato Slug para evitar rutas conflictivas.

### Requisito 5: Captura de la base de conocimiento Q&A

**Historia de usuario:** Como encargado de turismo, quiero cargar preguntas y respuestas frecuentes, para dejar preparado el conocimiento del futuro chatbot del sitio.

#### Criterios de aceptación

1. WHEN el usuario agrega un QA_Entry con pregunta y respuesta, THE Wizard_Server SHALL almacenar el QA_Entry en el directorio `/content` del proyecto.
2. WHERE el usuario ha capturado al menos un QA_Entry, THE Wizard_Server SHALL registrar la ruta de la base de conocimiento en `Site_Config.modules.chatweb.knowledgeSource`.
3. THE Wizard_Server SHALL almacenar los QA_Entry sin consumirlos ni indexarlos durante el flujo del Wizard.
4. IF el usuario envía un QA_Entry con pregunta o respuesta vacía, THEN THE Wizard_Server SHALL rechazar la entrada y devolver un mensaje que indique el campo faltante.

### Requisito 6: Definición de marca y tema

**Historia de usuario:** Como encargado de turismo, quiero elegir los colores, la tipografía y el tono de mi sitio, para que refleje la identidad de mi provincia.

#### Criterios de aceptación

1. WHEN el usuario define los colores primario, de fondo y de texto, THE Wizard_Server SHALL escribir esos valores en `Theme_Tokens.colors`.
2. WHEN el usuario define la tipografía de títulos y de cuerpo, THE Wizard_Server SHALL escribir esos valores en `Theme_Tokens.typography`.
3. WHERE el usuario define el tono de voz, THE Wizard_Server SHALL escribir ese valor en `Theme_Tokens.voice.tone`.
4. IF el usuario ingresa un color que no cumple el patrón hexadecimal, THEN THE Wizard_Server SHALL rechazar el valor y devolver un mensaje que indique el formato de color esperado.
5. WHEN el usuario guarda la marca, THE Wizard_Server SHALL aplicar Schema_Validation al `Theme_Tokens` contra `theme-tokens.schema.json` antes de escribirlo.

### Requisito 7: Validación y errores accionables en la interfaz

**Historia de usuario:** Como encargado de turismo, quiero ver mensajes claros cuando un dato está mal, para corregirlo sin ayuda técnica.

#### Criterios de aceptación

1. WHEN el Wizard_Server produce o transforma un documento del Contrato, THE Wizard_Server SHALL aplicar Schema_Validation contra su esquema en `schemas/` antes de escribirlo o usarlo en el build.
2. IF una operación del Wizard_Server falla la Schema_Validation, THEN THE Wizard_Server SHALL devolver al Wizard_UI un mensaje que identifique el documento y el campo inválido.
3. WHEN el Wizard_UI recibe un mensaje de error del Wizard_Server, THE Wizard_UI SHALL mostrar la causa y la corrección sugerida en el paso correspondiente.
4. IF un Place carece de coordenadas y de dirección al momento de generar, THEN THE Wizard_Server SHALL devolver un mensaje que nombre cada Place afectado.
5. WHEN el Wizard_Server compone un mensaje de error, THE Wizard_Server SHALL aplicar Redact para excluir los valores de secretos del texto devuelto.

### Requisito 8: Generación con progreso en vivo

**Historia de usuario:** Como encargado de turismo, quiero disparar la generación del sitio y ver el avance en tiempo real, para saber que el proceso funciona y cuándo termina.

#### Criterios de aceptación

1. WHEN el usuario solicita generar el sitio, THE Wizard_Server SHALL invocar las fases `collect` y `build` del Core sobre el proyecto.
2. WHILE una fase de generación está en ejecución, THE Wizard_Server SHALL transmitir Build_Progress al Wizard_UI mediante una conexión WebSocket.
3. WHEN el build del Core finaliza con éxito, THE Wizard_Server SHALL notificar al Wizard_UI la finalización y la ruta del sitio construido.
4. IF una fase de generación lanza un error, THEN THE Wizard_Server SHALL transmitir al Wizard_UI un mensaje descriptivo con la causa y la acción sugerida, tras aplicar Redact.
5. THE Wizard_Server SHALL delegar la lógica de generación en el Core sin reimplementar las tools.

### Requisito 9: Previsualización del sitio construido

**Historia de usuario:** Como encargado de turismo, quiero previsualizar el sitio generado antes de publicarlo, para revisar cómo quedó.

#### Criterios de aceptación

1. WHEN el usuario solicita la previsualización con un sitio construido disponible, THE Wizard_Server SHALL servir la previsualización mediante la fase `preview` del Core.
2. IF el usuario solicita la previsualización sin un sitio construido disponible, THEN THE Wizard_Server SHALL devolver un mensaje que indique que debe generarse el sitio primero.
3. WHEN la previsualización está disponible, THE Wizard_UI SHALL ofrecer al usuario un acceso para abrir el sitio previsualizado.

### Requisito 10: Publicación con selección de destino

**Historia de usuario:** Como encargado de turismo, quiero elegir dónde publicar mi sitio y publicarlo, para tenerlo en línea con una URL.

#### Criterios de aceptación

1. WHEN el usuario elige un Deploy_Target soportado y solicita publicar con un sitio construido disponible, THE Wizard_Server SHALL invocar la fase `deploy` del Core con ese Deploy_Target y devolver la URL pública.
2. THE Wizard_UI SHALL restringir la selección de destino a los Deploy_Target soportados (`aws-amplify`, `s3-cloudfront`, `static-export`, `vercel`, `netlify`).
3. IF el usuario solicita publicar sin un sitio construido disponible, THEN THE Wizard_Server SHALL devolver un mensaje que indique que debe generarse el sitio primero.
4. IF la publicación falla o faltan credenciales, THEN THE Wizard_Server SHALL devolver un mensaje que identifique la causa tras aplicar Redact.
5. WHEN el usuario selecciona un Deploy_Target, THE Wizard_Server SHALL escribir el valor en `Site_Config.deploy.target`.

### Requisito 11: Edición segura por capas sin pisar contenido existente

**Historia de usuario:** Como encargado de turismo, quiero volver periódicamente a agregar un evento o cambiar el banner sin perder lo que ya cargué, para mantener mi sitio a lo largo del tiempo.

#### Criterios de aceptación

1. WHEN el usuario edita un documento del Contrato en una sesión posterior, THE Wizard_Server SHALL conservar los valores existentes que el usuario no modificó.
2. WHEN el usuario agrega un Place o Event a un proyecto existente, THE Wizard_Server SHALL anexar la nueva entrada sin eliminar las entradas existentes.
3. WHERE un Place o Event ya tiene una `description` no vacía, THE Wizard_Server SHALL conservar ese texto durante la generación.
4. WHEN el usuario sube un Asset nuevo con el mismo propósito que uno existente, THE Wizard_Server SHALL conservar los Assets previos referenciados por el Contrato salvo que el usuario los reemplace explícitamente.
5. WHEN el usuario reconstruye el sitio, THE Wizard_Server SHALL preservar las personalizaciones de contenido, marca y estructura ya presentes en los documentos del Contrato.

### Requisito 12: Ejecución local y postura de seguridad

**Historia de usuario:** Como encargado de turismo, quiero que el asistente corra en mi máquina de forma segura, para no exponer archivos ni credenciales.

#### Criterios de aceptación

1. WHEN el Wizard_Server inicia, THE Wizard_Server SHALL escuchar únicamente en la interfaz Loopback (`127.0.0.1`).
2. THE Wizard_Server SHALL excluir los valores de secretos de toda respuesta HTTP y de todo mensaje WebSocket mediante Redact.
3. WHERE una operación requiere una variable de entorno no definida, THE Wizard_Server SHALL devolver un mensaje que nombre la variable faltante sin exponer su valor.
4. WHEN el Wizard_Server recibe una carga de Asset, THE Wizard_Server SHALL almacenar el archivo dentro del directorio `/assets` del proyecto y rechazar rutas que escapen de ese directorio.
5. THE Wizard_Server SHALL construir el contenido del sitio componiendo Modules pre-construidos, sin generar ni modificar el código de los Modules.
