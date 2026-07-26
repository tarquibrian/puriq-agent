# Plan de Implementación: Ingesta Multimodal (Hitos 3 y 4)

## Overview

El plan implementa la **Pieza 5** (ingesta e interpretación de archivos) y la **parte de visión de la
Pieza 4** (extensión multimodal del proveedor de LLM) en Python, sobre `agent/puriq/`, de forma
incremental y de **bajo riesgo**. El orden sigue las dependencias del diseño: primero la **dependencia
`pypdf`** y el **Ingest_Router** (que no depende del bucle), luego la **extensión multimodal de
`complete_chat`**, la **Extract_PDF_Tool** (aditiva en `INTAKE_TOOL_SPECS`), el **Chat_Agent** con
binarios e inyección de bytes, el **Intake_Prompt** multimodal, y por último el **canal web**
(`POST /api/chat` multipart + `Chat_Panel`). Esta fase **extiende, no reimplementa**: reutiliza
`attach_asset`, `run_intake_tool`, `INTAKE_TOOL_SPECS`, el `ChatAgent`, el modelo de mensajes neutral y
`get_provider()`/`PURIQ_LLM_MODE`, y **no rompe** el camino text-only del Hito 2 ni `complete(prompt)`.

La estrategia de pruebas es **dual**: pruebas de propiedad con **Hypothesis** (mínimo 100 iteraciones,
sobre un proyecto temporal `tmp_path`, un proveedor mock inyectado en `ChatAgent(provider=...)` con
`supports_vision` configurable y un `PDF_Extractor` mock) para las **15 propiedades de correctitud**, y
pruebas de ejemplo/integración (mocks de `invoke_model` multimodal de Bedrock y de `httpx.post` de
OpenAI vision, un PDF real de muestra con `pypdf`, `TestClient` multipart para el endpoint, smoke de
`127.0.0.1` y del pin de `pypdf`, y DOM ligera para el panel). Cada prueba de propiedad se etiqueta con
`# Feature: multimodal-ingest, Property {N}: ...`.

## Tasks

- [x] 1. Dependencia de extracción de PDF (Pieza 5)
  - [x] 1.1 Agregar el extra opcional `pdf` con `pypdf` fijado en `agent/pyproject.toml`
    - Añadir a `[project.optional-dependencies]` la entrada `pdf = ["pypdf==6.0.0"]` (versión exacta), siguiendo la convención de extras existentes (`local`, `mcp`, `test`)
    - El nombre del extra (`pdf`) es el que menciona el mensaje de error de `extract_pdf_text` cuando `pypdf` no está instalado (`pip install puriq[pdf]`)
    - _Requirements: 9.1, 9.2, 9.4_

- [x] 2. Ingest_Router + PDF_Extractor (Pieza 5): `intake/ingest.py`
  - [x] 2.1 Implementar el módulo `intake/ingest.py`
    - Crear `agent/puriq/intake/ingest.py` (sin FastAPI, sin importar `intake/tools.py` para evitar ciclos), importando de `wizard.assets` (`IMAGE_EXTS`, `MAX_ASSET_BYTES`, `normalize_asset_name`) y `config` (redacción), con import **diferido** de `pypdf`
    - Definir `FileKind(Enum)`, los dataclasses `IncomingFile`, `ImageBlock`, `IngestResult`, la constante `MAX_PDF_BYTES = 20 * 1024 * 1024`, el mapa `_VISION_MEDIA_TYPES` y `_PDF_EXTRA = "pdf"`
    - `classify_file(filename)`: función pura que devuelve `IMAGE` si la extensión (en minúsculas) ∈ `IMAGE_EXTS`, `PDF` si es `.pdf`, `UNSUPPORTED` en otro caso (DD-M1)
    - `extract_pdf_text(data)`: extrae texto en memoria con `pypdf.PdfReader(io.BytesIO(data))` concatenando `page.extract_text()`; import diferido que, si el extra falta, lanza un error que **nombra** el extra a instalar (Req 9.4); si el PDF no tiene texto legible, lanza un `ValueError` accionable (Req 3.6); nunca persiste el binario (Req 11.5)
    - `prepare_incoming(files, *, supports_vision)`: por archivo, enruta y valida — `UNSUPPORTED` → mensaje accionable en `rejected` sin efectos (Req 1.4); `IMAGE` → valida extensión con `normalize_asset_name(filename, IMAGE_EXTS)` (Req 10.1, 10.2) y tamaño contra `MAX_ASSET_BYTES` sobre bytes decodificados (Req 10.3, 10.5), registra bytes en `asset_binaries` y, si `supports_vision` y media type raster soportado, agrega `ImageBlock` a `image_blocks` (Req 2.2); `PDF` → valida tamaño contra `MAX_PDF_BYTES` (Req 10.4, 10.5) y agrega el `Texto_Extraido` **redactado** a `pdf_texts` (Req 3.1, 3.2, 11.2, 11.5). No escribe el contrato ni assets (Req 1.5, DD-M1/M5/M9)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.2, 3.1, 3.2, 3.5, 3.6, 9.3, 9.4, 10.1, 10.2, 10.3, 10.4, 10.5, 11.2, 11.5_

  - [x]* 2.2 Escribir prueba de propiedad para la clasificación por extensión
    - **Property 1: La clasificación por extensión es total y correcta**
    - **Validates: Requirements 1.1**

  - [x]* 2.3 Escribir prueba de propiedad para el rechazo sin efectos de no soportados
    - **Property 2: Los archivos no soportados se rechazan sin efectos**
    - **Validates: Requirements 1.4, 1.5**

  - [x]* 2.4 Escribir prueba de propiedad para la validación de imagen
    - **Property 3: La validación de imagen coincide con `normalize_asset_name`**
    - **Validates: Requirements 10.1, 10.2**

  - [x]* 2.5 Escribir prueba de propiedad para la validación de tamaño previa a efectos
    - **Property 4: El tamaño se valida antes de cualquier efecto**
    - **Validates: Requirements 10.3, 10.4, 10.5**

  - [x]* 2.6 Escribir prueba de propiedad para la no persistencia del PDF
    - **Property 5: El binario del PDF nunca se persiste**
    - **Validates: Requirements 3.5, 11.5**

  - [x]* 2.7 Escribir pruebas de ejemplo del router y del extractor
    - Una imagen válida produce `ImageBlock`/`asset_binaries` y no extracción (1.2); un PDF invoca el extractor y no el tratamiento de imagen (1.3); PDF sin texto → mensaje accionable (3.6); extra ausente → mensaje que nombra `pdf` (9.4)
    - _Requirements: 1.2, 1.3, 3.6, 9.4_

- [x] 3. Checkpoint - Asegurar que las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Extensión multimodal de `complete_chat` (Pieza 4, visión): `tools/generate_content.py`
  - [x] 4.1 Agregar el modelo multimodal neutral, `supports_vision` y el guard compartido
    - Definir el dataclass `ImageContent {media_type, data}` y agregar `images: list[ImageContent] | None = None` a `Message` (solo en mensajes `user`, DD-M2)
    - Agregar `supports_vision: bool` al `Protocol` `LLMProvider` y fijarlo por proveedor: `BedrockProvider = True` (Req 5.2), `OpenAICompatibleProvider = True` (Req 5.3), `OllamaProvider = False`
    - Guard multimodal compartido al inicio de `complete_chat`: si algún mensaje trae `images` y `not self.supports_vision`, lanzar `RuntimeError` accionable que **nombra `PURIQ_LLM_MODE`** y los modos con visión (`bedrock`, `openai`) (Req 5.4, DD-M7); conservar `complete(prompt)` y la lectura de credenciales con `get_env(..., secret=True)` (Req 4.4, 11.3)
    - _Requirements: 4.1, 4.3, 4.4, 5.4, 11.3_

  - [x]* 4.2 Escribir prueba de propiedad para la convivencia text-only
    - **Property 7: Sin imágenes, `complete_chat` preserva el comportamiento text-only**
    - **Validates: Requirements 4.3, 4.4**

  - [x]* 4.3 Escribir prueba de propiedad para el rechazo sin visión
    - **Property 11: Un proveedor sin visión rechaza las imágenes nombrando `PURIQ_LLM_MODE`**
    - **Validates: Requirements 5.4**

  - [x] 4.4 Implementar la traducción multimodal de Bedrock (`_messages_to_claude`)
    - En el `content` del mensaje `user`, agregar por imagen un bloque `{"type": "image", "source": {"type": "base64", "media_type": m, "data": d}}` **junto** a los bloques de texto, solo cuando `msg.images` es no vacío (DD-M3); el resto del cuerpo Claude y el parseo no cambian; las imágenes coexisten con `tools` (Req 4.2, 4.5, 5.2)
    - _Requirements: 4.2, 4.5, 5.2_

  - [x] 4.5 Implementar la traducción multimodal de OpenAI (`_messages_to_openai`)
    - Cuando el mensaje `user` trae imágenes, convertir su `content` en partes `[{"type": "text", ...}, {"type": "image_url", "image_url": {"url": "data:<media_type>;base64,<data>"}}]`; sin imágenes, `content` sigue siendo el string de siempre (DD-M3); las imágenes coexisten con `tools` (Req 4.2, 4.5, 5.3)
    - _Requirements: 4.2, 4.5, 5.3_

  - [x]* 4.6 Escribir prueba de propiedad para la traducción multimodal con tools
    - **Property 8: La traducción multimodal transporta cada imagen junto con las tools**
    - **Validates: Requirements 4.1, 4.2, 4.5**

  - [x]* 4.7 Escribir pruebas de ejemplo e integración de la visión por proveedor
    - Integración con mock de `invoke_model` (Bedrock): el cuerpo Claude incluye un bloque `image` con `source.base64` + `media_type` y coexiste con `tools` (5.2); mock de `httpx.post` (OpenAI): `content` con parte `image_url` (`data:...;base64,...`) (5.3); regresión de `complete(prompt)` sin cambios (4.4); `get_provider()` resuelve por `PURIQ_LLM_MODE` (5.1); sin credencial → `MissingEnvVarError` que la nombra sin valor (5.5)
    - _Requirements: 4.4, 5.1, 5.2, 5.3, 5.5_

- [x] 5. Checkpoint - Asegurar que las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Extract_PDF_Tool aditiva (Pieza 5): `intake/tools.py`
  - [x] 6.1 Implementar `extract_pdf`, su handler y su spec en `INTAKE_TOOL_SPECS`
    - Agregar `extract_pdf(project, *, content_base64=None, source_path=None)` que exige **exactamente una** fuente (patrón de `attach_asset`); si faltan ambas o vienen ambas, lanza un `ValueError` accionable (Req 7.3); obtiene los bytes, comprueba `len(bytes) <= MAX_PDF_BYTES` antes de procesar (Req 10.4, 10.5), delega en `ingest.extract_pdf_text` (Req 7.2) y devuelve `config.redact_value({"text": Texto_Extraido})` (Req 11.2); el PDF no se persiste (Req 11.5)
    - Definir el `inputSchema` (JSON Schema puro): `project` requerido, `content_base64`/`source_path` opcionales, `additionalProperties: false`; agregar el handler `_h_extract_pdf` y la entrada a `INTAKE_TOOL_SPECS` (con `documento = None` en `_INTAKE_TOOL_DOCS`, como `get_state`); **no** tocar `attach_asset` (Req 7.4). Queda expuesta por MCP automáticamente vía `TOOL_SPECS` (DD-M6)
    - _Requirements: 7.1, 7.2, 7.3, 10.4, 10.5, 11.2, 11.5_

  - [x]* 6.2 Escribir prueba de propiedad para la fuente única de la Extract_PDF_Tool
    - **Property 12: La Extract_PDF_Tool exige exactamente una fuente**
    - **Validates: Requirements 7.3**

  - [x]* 6.3 Escribir prueba de propiedad para la redacción del texto y contenido derivado
    - **Property 14: El Texto_Extraido y el Contenido_Derivado se devuelven redactados**
    - **Validates: Requirements 11.2, 7.2**

  - [x]* 6.4 Escribir prueba de propiedad para la aditividad del registro de tools
    - **Property 13: El registro de tools es aditivo y conserva las existentes**
    - **Validates: Requirements 7.4, 7.5**

  - [x]* 6.5 Escribir pruebas de ejemplo e integración de la tool y del MCP
    - `extract_pdf` está en `TOOL_SPECS` con su `inputSchema` (7.1); `attach_asset` sigue presente (7.4); `extract_pdf` delega en el extractor (mock) y devuelve `{text}` (7.2); PDF real de muestra: `extract_pdf_text` (pypdf) devuelve su texto (3.1, 9.3)
    - _Requirements: 3.1, 7.1, 7.2, 7.4, 9.3_

- [x] 7. Chat_Agent multimodal (Pieza 5): `intake/agent.py`
  - [x] 7.1 Extender `ChatRequest` y `run_turn` con binarios, visión e inyección de bytes
    - Agregar `binarios: list[IncomingFile]` a `ChatRequest` (vacío en el camino JSON del Hito 2)
    - En `run_turn`: llamar `ingest.prepare_incoming(request.binarios, supports_vision=self._provider.supports_vision)` (Req 1, 2.2, 3.2, 10, DD-M1/M5/M7/M9) y guardar el mapa `asset_binaries`
    - Construir el mensaje de usuario: texto + referencias + `pdf_texts` como **contexto** (Req 3.2) + mensajes de `rejected`; adjuntar `image_blocks` como `Message.images` si hay visión (Req 2.2); si el proveedor no tiene visión pero llegaron imágenes, agregar una nota accionable que nombra `PURIQ_LLM_MODE` (DD-M7)
    - En el bucle de tool-use: por cada tool-call inyectar `project` y, si la tool es `attach_asset` y su `filename` coincide con una entrada de `asset_binaries`, inyectar `content_base64 = base64(asset_binaries[fname])` antes de despachar por `run_intake_tool` (DD-M4, Req 2.1); escribir `Contenido_Derivado` solo cuando el modelo emite la tool-call (confirmación, Req 8)
    - Persistir la sesión redactada **sin** binarios ni `content_base64` inyectado ni `Message.images` (DD-M4); devolver `ChatResponse(respuesta, estado)` redactada (Req 11.2)
    - _Requirements: 1.5, 2.1, 2.2, 2.4, 2.5, 3.2, 3.3, 3.4, 5.1, 8.2, 8.3, 8.4, 11.2_

  - [x]* 7.2 Escribir prueba de propiedad para el texto de PDF en el contexto del turno
    - **Property 6: El Texto_Extraido entra en el contexto del turno**
    - **Validates: Requirements 3.2**

  - [x]* 7.3 Escribir prueba de propiedad para el envío de la imagen con visión
    - **Property 9: El agente envía la imagen a un proveedor con visión**
    - **Validates: Requirements 2.2**

  - [x]* 7.4 Escribir prueba de propiedad para la inyección de bytes por nombre de archivo
    - **Property 10: El agente inyecta los bytes de la imagen por nombre de archivo**
    - **Validates: Requirements 2.1**

  - [x]* 7.5 Escribir pruebas de ejemplo del agente
    - Un mock que emite `edit_item`/`add_qa` tras "confirmación" despacha por `run_intake_tool` (2.4, 3.3); un mock sin tool-calls de escritura deja el contrato sin cambios (2.5, 3.4, 8.4)
    - _Requirements: 2.4, 2.5, 3.3, 3.4, 8.4_

- [x] 8. Guion multimodal (Pieza 5): `Intake_Prompt`/`INTAKE_GUION` en `intake/prompt.py`
  - [x] 8.1 Agregar las instrucciones multimodales
    - Embeber en `INTAKE_GUION` (para que la Superficie A las lea vía `intake://guion`) y reforzar en `build_system_prompt`: pedir **proactivamente** imágenes de los lugares y PDFs de contexto (Req 12.1); usar la **descripción de la imagen** al proponer `Alt_Text` y descripción del Place/Event (Req 12.2); **destilar** el `Texto_Extraido` de un PDF a descripciones, Q&A y datos históricos con las intake tools, sin publicar el PDF (Req 12.3); **pedir confirmación** del usuario antes de escribir cualquier `Contenido_Derivado` (Req 8.1, 12.4)
    - _Requirements: 8.1, 12.1, 12.2, 12.3, 12.4_

  - [ ]* 8.2 Escribir pruebas de ejemplo del prompt
    - El prompt contiene la instrucción de pedir imágenes/PDFs (12.1), de usar la descripción para alt/descripción (12.2), de destilar el PDF sin publicarlo (12.3) y de pedir confirmación antes de escribir (8.1, 12.4)
    - _Requirements: 8.1, 12.1, 12.2, 12.3, 12.4_
    - _(no implementada: las instrucciones multimodales del prompt ya están en `build_system_prompt` (tarea 8.1), pero ningún test de ejemplo verifica esos textos todavía)_

- [x] 9. Checkpoint - Asegurar que las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Canal web con binarios (Superficie B, Pieza 5): `POST /api/chat` en `wizard/server.py`
  - [x] 10.1 Aceptar binarios reales por multipart además del JSON text-only
    - Extender `POST /api/chat` para distinguir por `Content-Type` (DD-M8): `application/json` → `{mensaje, archivos[]}` (Hito 2, referencias; `binarios=[]`, intacto, Req 6.3); `multipart/form-data` → campos `mensaje`, `archivos` (referencias) y `binarios` (`UploadFile[]`), leyendo cada uno a bytes y envolviéndolo en `IncomingFile(filename, content)` para el Ingest_Router (Req 6.1, 6.2)
    - No escribir a `assets/` desde el endpoint (la escritura la hace `attach_asset` en el Chat_Agent, Req 1.5); responder `redact_value({respuesta, estado})` (Req 6.4, 11.2); errores de validación → 422 y el resto → 500, traducidos y redactados por `wizard_error_response` (Req 11.4); servir solo en `127.0.0.1` (Req 11.1)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 11.1, 11.4_

  - [x]* 10.2 Escribir prueba de propiedad para la traducción y redacción de errores
    - **Property 15: Los errores de ingesta o visión se entregan traducidos y redactados**
    - **Validates: Requirements 11.4**

  - [x]* 10.3 Escribir pruebas de integración y smoke del endpoint
    - Con `TestClient` y un `ChatAgent` mock: `POST` multipart con `binarios` responde 200 con `{respuesta, estado}` (6.1, 6.2); `POST` JSON con `archivos` mantiene el comportamiento del Hito 2 (6.3); smoke de que `serve()` liga el servidor a `127.0.0.1` (11.1); smoke de que `pyproject.toml` declara exactamente una PDF_Library con pin exacto (9.1, 9.2)
    - _Requirements: 6.1, 6.2, 6.3, 9.1, 9.2, 11.1_

- [x] 11. Adjunto de binarios (Superficie B, Pieza 5): `Chat_Panel` en `wizard/static/`
  - [x] 11.1 Adjuntar binarios reutilizando el drag & drop existente
    - Sumar un adjuntador de archivos al `Chat_Panel` reutilizando el drag & drop que ya existe para `POST /api/assets`; cuando el turno lleva binarios, enviar `multipart/form-data` a `/api/chat` (`mensaje`, `archivos`, `binarios`); cuando no, mantener el `POST` JSON del Hito 2 (Req 6.1, 6.3)
    - Tras la respuesta, volcar `estado` en `state.server` y llamar `updateSkeleton()` (igual que el Hito 2)
    - _Requirements: 6.1, 6.3_

  - [ ]* 11.2 Escribir pruebas de ejemplo/DOM ligeras del Chat_Panel
    - Envío multipart cuando hay binarios y `POST` JSON cuando no (6.3); refresco del preview tras la respuesta
    - _Requirements: 6.1, 6.3_
    - _(no implementada: el proyecto no tiene arnés de testing JS)_

- [x] 12. Checkpoint final - Asegurar que todas las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Las tareas marcadas con `*` son opcionales (pruebas) y pueden omitirse para un MVP más rápido, igual que en los specs de los Hitos 1 y 2.
- Cada tarea referencia requisitos específicos para trazabilidad; cada prueba de propiedad referencia su `Property N` del diseño con su `Validates`.
- Las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones, sobre un proyecto temporal (`tmp_path`), un proveedor mock inyectado en `ChatAgent(provider=...)` con `supports_vision` configurable y un `PDF_Extractor` mock; cada una lleva el comentario `# Feature: multimodal-ingest, Property {N}: ...`.
- Las 15 propiedades quedan cubiertas exactamente una vez: P1→2.2, P2→2.3, P3→2.4, P4→2.5, P5→2.6, P6→7.2, P7→4.2, P8→4.6, P9→7.3, P10→7.4, P11→4.3, P12→6.2, P13→6.4, P14→6.3, P15→10.2.
- Esta fase **extiende** el núcleo de intake, el bucle del chat y el proveedor base (existen de los Hitos 1 y 2); su comportamiento previo (incluido `attach_asset` y el camino text-only) no se re-testea aquí.
- Los checkpoints aseguran validación incremental en cortes razonables (dependencia + router → visión del proveedor → tool + agente + prompt → canal web).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "4.1", "8.1"] },
    { "id": 1, "tasks": ["2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "4.2", "4.3", "4.4", "6.1", "8.2"] },
    { "id": 2, "tasks": ["4.5", "6.2", "6.3", "6.4", "6.5", "7.1"] },
    { "id": 3, "tasks": ["4.6", "4.7", "7.2", "7.3", "7.4", "7.5", "10.1"] },
    { "id": 4, "tasks": ["10.2", "10.3", "11.1"] },
    { "id": 5, "tasks": ["11.2"] }
  ]
}
```
