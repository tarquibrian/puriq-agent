# Documento de Diseño

## Overview

Este diseño cubre la **tercera fase (Hitos 3 y 4)** de la capa conversacional de Puriq: la **ingesta
multimodal**. Completa la **Pieza 5** (ingesta e interpretación de archivos) y la **parte de visión de
la Pieza 4** (extensión multimodal del proveedor de LLM), sobre los cimientos ya implementados en el
Hito 1 (`conversational-intake-mcp`) y el Hito 2 (`conversational-web-chat`).

El principio rector de las fases anteriores se mantiene sin cambios: **una sola implementación
compartida** (el núcleo de intake, `agent/puriq/intake/tools.py`) y **superficies delgadas**. Esta fase
**extiende, no reimplementa**: reutiliza `attach_asset`, `run_intake_tool`, `INTAKE_TOOL_SPECS`, el
`ChatAgent`, el modelo de mensajes neutral (`Message`/`ToolCall`/`ToolResult`/`ChatResult`) y el
proveedor configurable (`get_provider()` / `PURIQ_LLM_MODE`).

Las piezas de esta fase:

- **Pieza 5 — Ingesta e interpretación de archivos:**
  - `agent/puriq/intake/ingest.py` (`Ingest_Router` + `PDF_Extractor`): un router por tipo (Imagen vs
    PDF vs no soportado) y la extracción de texto de PDF en memoria. El router **no escribe** el
    contrato: delega toda escritura en las intake tools existentes (`attach_asset`, `edit_item`,
    `add_place`, `add_qa`).
  - `agent/puriq/intake/tools.py`: una intake tool nueva `extract_pdf` (la **Extract_PDF_Tool**),
    agregada de forma **aditiva** a `INTAKE_TOOL_SPECS`; `attach_asset` se conserva.
  - `agent/puriq/mcp/server.py`: la Extract_PDF_Tool queda expuesta **automáticamente** por MCP (porque
    `TOOL_SPECS = _EXISTING_SPECS + INTAKE_TOOL_SPECS`), sin tocar el motor ni las tools previas.
- **Pieza 4 (parte de visión) — Complete_Chat multimodal:** se extiende el modelo de mensajes neutral
  de `agent/puriq/tools/generate_content.py` para transportar imágenes (bloques de imagen en `Message`),
  y cada proveedor las traduce a su formato multimodal nativo (Bedrock Claude / compatible con OpenAI),
  **conviviendo** con el `complete_chat` text-only del Hito 2 (sin imágenes ⇒ comportamiento idéntico) y
  **sin romper** `complete(prompt)`. Degradación accionable cuando el proveedor/modo no soporta visión.
- **Canal web (Superficie B):** `POST /api/chat` pasa a aceptar **binarios reales** (multipart) además
  del cuerpo JSON text-only del Hito 2 (referencias), de forma **compatible hacia atrás**. El
  `Chat_Panel` adjunta binarios reutilizando el drag & drop ya existente.
- **Superficie A (MCP):** la visión la ejecuta el LLM del cliente externo sobre las imágenes que el
  cliente le pasa; **Puriq aporta la extracción de PDF** (Extract_PDF_Tool) y el **guardado** de
  imágenes (`attach_asset` ya existe).
- **Confirmación transversal:** ningún `Contenido_Derivado` (Alt_Text, descripción, Q&A, datos
  destilados de un PDF) se escribe **sin la confirmación del usuario**; se orquesta con el bucle
  existente y las instrucciones del `Intake_Prompt`.
- **Dependencia nueva:** la **PDF_Library** (`pypdf`), pura Python, con **versión fijada**, como extra
  opcional del proyecto.

### Alcance

Dentro: `intake/ingest.py` (Ingest_Router + PDF_Extractor), la intake tool `extract_pdf`, la extensión
multimodal de `complete_chat` y sus helpers de traducción, la aceptación de binarios en
`POST /api/chat`, el adjunto de binarios en el `Chat_Panel`, las instrucciones multimodales del
`Intake_Prompt`/`INTAKE_GUION`, y la dependencia `pypdf` fijada. Fuera (declarado en los requisitos):
reimplementar el núcleo de intake, el loop del chat, la sesión o el proveedor base (existen); la visión
del lado de Puriq en MCP (la hace el cliente); otros formatos (video, audio, hojas de cálculo);
publicar el PDF crudo o cualquier documento fuente en el sitio construido.

## Investigación y hallazgos que informan el diseño

Antes de diseñar se leyó el código real que esta fase debe reutilizar y extender. Los hallazgos que
condicionan el diseño:

1. **`attach_asset` ya transporta el binario por JSON y valida todo lo necesario.**
   `intake/tools.attach_asset` acepta **exactamente una** fuente (`content_base64` o `source_path`),
   comprueba `len(bytes) <= MAX_ASSET_BYTES` **antes** de escribir (Req 10.3), normaliza el nombre con
   `normalize_asset_name(filename, IMAGE_EXTS)` (extensión no soportada → `ValueError` que lista los
   formatos, Req 10.2), verifica la contención en `assets/` con `resolve_within_assets`,
   desambigua colisiones con `next_available_asset` y asocia por `id` con `append_image`. **El
   Ingest_Router no reimplementa nada de esto**: delega el guardado/asociación de imágenes en
   `attach_asset` vía `run_intake_tool` (Req 1.5, 2.1).

2. **El modelo de mensajes neutral es hoy text-only, pero está listo para extenderse.**
   `generate_content.py` define `Message(role, content, tool_calls, tool_result)` y los traductores por
   proveedor: `BedrockProvider._messages_to_claude` arma bloques `{"type": "text", ...}` /
   `tool_use` / `tool_result`; `OpenAICompatibleProvider._messages_to_openai` arma `content` de texto y
   `tool_calls`. `complete_chat(messages, tools=None)` invoca el modelo y parsea texto o tool-calls.
   **La extensión multimodal se hace agregando bloques de imagen a `Message`** y enseñando a cada
   traductor a emitirlos; cuando no hay imágenes, la salida de los traductores es **idéntica** a la del
   Hito 2 (Req 4.3). La firma de `complete_chat` **no cambia** (las imágenes viajan dentro de los
   `Message`), por lo que los consumidores del Hito 2 no se ven afectados (Req 4.3, 4.4).

3. **El `ChatAgent` ya inyecta `project` en cada tool-call sin que el LLM lo vea (DD-2 del Hito 2).**
   `agent.run_turn` construye el mensaje de usuario (`_build_user_content`, hoy solo texto con
   referencias), corre el bucle de tool-use, e **inyecta** `project` en `arguments` antes de despachar
   por `run_intake_tool`. Este patrón de **inyección** es el que reutilizamos para los binarios: el LLM
   nombra el archivo, y el agente inyecta sus **bytes** (`content_base64`) en la tool-call
   `attach_asset`, de modo que los binarios **nunca transitan por el modelo** (DD-M4).

4. **`POST /api/chat` es hoy JSON text-only y `POST /api/assets` ya acepta multipart binario.**
   `ChatBody{mensaje, archivos[]}` transporta **referencias** a assets ya subidos; `upload_asset`
   (multipart) ya lee `UploadFile`, valida tamaño/extensión y escribe en `assets/`. Esta fase **agrega**
   la aceptación de binarios en `/api/chat` (multipart) **sin romper** el cuerpo JSON del Hito 2
   (Req 6.1, 6.3): el endpoint despacha por `Content-Type`.

5. **El MCP compone sus tools de forma aditiva y data-driven.**
   `mcp/server.TOOL_SPECS = [*_EXISTING_SPECS, *INTAKE_TOOL_SPECS]`; `_HANDLERS`, `list_tools` y
   `call_tool` derivan de esa lista, y las intake tools se enrutan por `run_intake_tool` (que traduce
   errores de forma redactada y accionable). **Agregar `extract_pdf` a `INTAKE_TOOL_SPECS`** la expone
   por MCP y por el `ChatAgent` **sin tocar el motor** y conservando todas las tools previas (Req 7.1,
   7.5). `attach_asset` permanece igual (Req 7.4).

6. **`config`/`errors` son la única fuente de verdad de secretos y mensajes.**
   `config.redact`/`redact_value` enmascaran recursivamente; `get_env(..., secret=True)` registra una
   variable como secreto (Req 11.3); `wizard_error_response(exc, documento=None)` traduce y **siempre**
   redacta (Req 11.4). El diseño reutiliza estas piezas para redactar el `Texto_Extraido` y el
   `Contenido_Derivado` (Req 11.2) y para traducir errores de ingesta/visión (Req 11.4).

7. **La PDF_Library elegida: `pypdf`, pura Python, mantenida y sin binarios del sistema.**
   `pypdf` es una biblioteca pura-Python, activamente mantenida (releases recientes en el rango 5.x–6.x),
   con licencia BSD-3 y API estable de extracción: `from pypdf import PdfReader; PdfReader(stream);
   page.extract_text()` ([pypdf en PyPI](https://pypi.org/project/pypdf/),
   [extracción de texto](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)). Se prefiere
   frente a alternativas más rápidas (PyMuPDF/pdfium) porque **no** requiere binarios nativos ni
   licencias restrictivas, lo que la hace segura de instalar en el entorno local del wizard. Se agrega
   como **extra opcional** con **versión fijada** (Req 9.1, 9.2, 9.4). *Contenido reformulado para
   cumplir con las restricciones de licencia.*

## Architecture

El núcleo sigue siendo `intake/tools.py`. El Hito 3-4 agrega el **Ingest_Router** (router por tipo +
PDF_Extractor), extiende `complete_chat` a multimodal, hace que `POST /api/chat` acepte binarios, y
expone la `Extract_PDF_Tool` por MCP de forma aditiva. Toda escritura del contrato sigue pasando por las
intake tools (`run_intake_tool`).

```mermaid
graph TD
    subgraph Navegador
        CP[Chat_Panel\nadjunta binarios reales]
        LP[Live_Preview + updateSkeleton]
    end

    subgraph "wizard/server.py"
        EP[POST /api/chat\nJSON refs (Hito 2) O multipart binarios]
    end

    subgraph "intake/agent.py (Chat_Agent)"
        AG[run_turn\nbucle + inyeccion de project y de bytes]
    end

    subgraph "intake/ingest.py (Pieza 5)"
        IR[Ingest_Router\nclasifica por extension]
        PE[PDF_Extractor\npypdf, en memoria]
    end

    subgraph "tools/generate_content.py (Pieza 4 vision)"
        CC[complete_chat multimodal\nMessage.images -> bloques nativos]
        SV[supports_vision por proveedor]
    end

    subgraph "intake/tools.py (nucleo, reutilizado + extract_pdf)"
        RIT[run_intake_tool]
        AA[attach_asset]
        EI[edit_item / add_qa / add_place]
        XP[extract_pdf (Extract_PDF_Tool)]
    end

    subgraph "mcp/server.py (Superficie A)"
        MCP[TOOL_SPECS = _EXISTING + INTAKE\n(+ extract_pdf, aditivo)]
    end

    CP -->|multipart: mensaje + binarios| EP
    EP --> AG
    AG --> IR
    IR -->|Imagen| AA
    IR -->|PDF| PE
    AG --> CC
    CC --> SV
    AG -->|despacha con project + bytes inyectados| RIT
    RIT --> AA
    RIT --> EI
    RIT --> XP
    XP --> PE
    EP -->|respuesta, estado| CP
    CP --> LP
    MCP -.expone.-> XP
    MCP -.expone.-> AA
```

### Flujo de un turno multimodal (Superficie web)

```mermaid
sequenceDiagram
    participant U as Chat_Panel
    participant E as POST /api/chat (multipart)
    participant A as Chat_Agent
    participant R as Ingest_Router
    participant P as PDF_Extractor
    participant L as complete_chat (multimodal)
    participant T as run_intake_tool

    U->>E: mensaje + binarios[cerro.jpg, folleto.pdf]
    E->>A: run_turn(ChatRequest{mensaje, archivos, binarios})
    loop cada binario
        A->>R: classify(filename)
        alt Imagen
            R->>R: validar ext (normalize_asset_name) + tamano (MAX_ASSET_BYTES)
            R-->>A: ImageBlock(base64, media_type) + registra bytes por nombre
        else PDF
            R->>R: validar tamano (MAX_PDF_BYTES) sobre bytes decodificados
            R->>P: extract_text(bytes)  (en memoria, no persiste)
            P-->>R: Texto_Extraido (o mensaje accionable si vacio)
            R-->>A: texto para el contexto del turno (redactado)
        else no soportado
            R-->>A: rechazo accionable (lista tipos soportados)
        end
    end
    A->>A: build user Message: texto + refs + Texto_Extraido; images=[...] si hay vision
    A->>L: complete_chat([system, ...historial, user], tools=INTAKE_TOOL_SPECS)
    L-->>A: propone Alt_Text/descripcion/Q&A (texto) y PIDE confirmacion
    Note over A,U: sin confirmacion NO se escribe (Req 8)
    U->>E: "si, correcto"
    E->>A: run_turn(...)
    A->>L: complete_chat(...)
    L-->>A: Tool_Calls: attach_asset(cerro.jpg,...), edit_item(alt/desc), add_qa(...)
    loop cada Tool_Call
        A->>A: inyecta project; si filename == binario del turno, inyecta content_base64
        A->>T: run_intake_tool(name, args)
        T-->>A: estado | error accionable redactado
    end
    A-->>E: ChatResponse{respuesta, estado}
    E-->>U: redact_value({respuesta, estado})
```

### Decisiones de diseño

- **DD-M1 (el Ingest_Router clasifica y enruta, pero no escribe).** El router determina el tipo por
  extensión (Imagen si la extensión ∈ `IMAGE_EXTS`; PDF si es `.pdf`; no soportado en otro caso,
  Req 1.1) y produce artefactos para el turno (bloque de imagen, texto de PDF, o rechazo accionable),
  pero **toda escritura del contrato la delega en las intake tools** vía `run_intake_tool` (Req 1.5). Así
  hereda validación, atomicidad, integridad referencial y traducción de errores del núcleo. *Alternativa
  descartada:* que el router llame a `append_image`/`save_contract` directamente — divergiría del
  comportamiento del MCP y duplicaría la lógica ya endurecida de `attach_asset`.

- **DD-M2 (las imágenes viajan dentro del modelo de mensajes neutral, no en la firma).** Se agrega un
  bloque de imagen a `Message` (`images: list[ImageBlock] | None`), en vez de un parámetro `images` en
  `complete_chat`. Así la firma `complete_chat(messages, tools=None)` **no cambia** (Req 4.3, 4.4) y la
  imagen queda **asociada al turno** de usuario correcto. Cada proveedor traduce los bloques a su formato
  nativo. *Alternativa descartada:* un parámetro `images=` suelto — desliga la imagen de su mensaje y
  obliga a cambiar la firma que el Hito 2 ya fijó.

- **DD-M3 (convivencia estricta con text-only).** Los traductores solo emiten bloques de imagen cuando
  `Message.images` es no vacío; sin imágenes, su salida es **byte a byte idéntica** a la del Hito 2
  (Req 4.3). `complete(prompt)` permanece intacto (Req 4.4). Esta invariante se prueba explícitamente
  (Property 7).

- **DD-M4 (inyección de bytes por nombre de archivo, análoga a la inyección de `project`).** Los
  binarios del turno **no se transmiten al LLM** (serían enormes y superfluos). El `Chat_Agent` mantiene
  un mapa `filename → bytes` del turno; cuando el modelo emite `attach_asset(filename=...)` para un
  archivo del turno, el agente **inyecta** `content_base64` (los bytes retenidos) en `arguments` antes de
  despachar por `run_intake_tool`, igual que ya inyecta `project` (DD-2 del Hito 2). El LLM razona solo
  con el **nombre** del archivo (Req 2.1, 10.5). *Alternativa descartada:* pasar el base64 por el
  contexto del modelo — coste enorme y riesgo de filtrar binarios en el historial de sesión.

- **DD-M5 (el texto del PDF entra como contexto del turno; el PDF no se publica ni se persiste).** El
  PDF se procesa **en memoria** (`io.BytesIO`), se extrae su texto con `pypdf` y ese `Texto_Extraido`
  (redactado) se **inyecta como contexto** en el mensaje de usuario del turno (Req 3.2). El binario del
  PDF **nunca** se escribe a `assets/` ni a ningún archivo (Req 3.5, 11.5). El LLM destila ese texto a
  descripciones/Q&A/datos históricos llamando a las intake tools existentes tras confirmación (Req 3.3).
  *Alternativa descartada:* copiar el PDF a `assets/` como los demás recursos — el PDF fuente no se
  publica.

- **DD-M6 (`extract_pdf` es una intake tool aditiva, no una tool MCP aparte).** La Extract_PDF_Tool se
  agrega a `INTAKE_TOOL_SPECS`, de modo que: (a) MCP la expone automáticamente (`TOOL_SPECS` aditivo,
  Req 7.1, 7.5); (b) se enruta por `run_intake_tool`, heredando la traducción de errores redactada y
  accionable; (c) devuelve el `Texto_Extraido` **redactado** (Req 7.2, 11.2). Comparte el
  `PDF_Extractor` con el flujo web (una sola implementación). En la superficie web el router
  **pre-extrae** el texto (DD-M5), por lo que el LLM web usa el texto ya inyectado y no necesita llamar
  a `extract_pdf`; en MCP, el LLM del cliente **sí** llama a `extract_pdf` para obtener el texto y luego
  destilarlo con las intake tools. *Alternativa descartada:* registrar `extract_pdf` como spec MCP
  separada (fuera de `INTAKE_TOOL_SPECS`) — perdería la traducción redactada de `run_intake_tool` y
  obligaría a un camino de manejo de errores distinto.

- **DD-M7 (visión configurable y degradación accionable).** Cada proveedor declara `supports_vision`
  (Bedrock Claude 3.5: sí; compatible con OpenAI vision: sí; Ollama: no). El `Chat_Agent` resuelve el
  proveedor con `get_provider()`/`PURIQ_LLM_MODE` (Req 5.1). Ante una imagen con un proveedor **sin
  visión**, `complete_chat` **rechaza** con un error accionable que **nombra `PURIQ_LLM_MODE`** y los
  modos con visión (`bedrock`, `openai`) (Req 5.4). Para no abortar el turno de forma abrupta, el agente
  consulta `supports_vision` **antes** de adjuntar la imagen: si no hay visión, **igual guarda el asset**
  (vía `attach_asset`) y **omite** el bloque de imagen, informando al usuario que la visión no está
  disponible (nombrando `PURIQ_LLM_MODE`). El guard de `complete_chat` queda como defensa en profundidad
  para llamadas directas. Una credencial faltante propaga `MissingEnvVarError` que nombra la variable sin
  su valor (Req 5.5), reutilizando `get_env(..., required=True, secret=True)`.

- **DD-M8 (`/api/chat` compatible hacia atrás por `Content-Type`).** El endpoint acepta **dos** formas:
  `application/json` con `{mensaje, archivos[]}` (Hito 2, referencias, intacto) y
  `multipart/form-data` con `mensaje`, `archivos` (referencias) y `binarios` (los `UploadFile` reales,
  Req 6.1). El handler distingue por `Content-Type` (Req 6.3). Ambas construyen un `ChatRequest`
  extendido con `binarios` (vacío en el camino JSON). *Alternativa descartada:* un endpoint nuevo
  `/api/chat/multipart` — fragmentaría la superficie y rompería la simetría del canal.

- **DD-M9 (límites de tamaño validados sobre bytes decodificados antes de cualquier efecto).** La imagen
  reutiliza `MAX_ASSET_BYTES` (10 MiB, ya definido en `wizard/assets.py`); el PDF usa un límite nuevo
  `MAX_PDF_BYTES` (definido junto al Ingest_Router). Ambos se comprueban sobre los **bytes decodificados**
  **antes** de escribir en disco o invocar al modelo/extractor (Req 10.3, 10.4, 10.5). Un archivo
  sobredimensionado se rechaza con un mensaje que indica el límite, sin efectos secundarios.

## Components and Interfaces

### 1. `agent/puriq/intake/ingest.py` (Pieza 5): `Ingest_Router` + `PDF_Extractor`

Módulo **sin FastAPI** y **sin dependencia de `intake/tools.py`** (para evitar ciclos: `tools.py`
importará el `PDF_Extractor` desde aquí). Importa solo de `wizard.assets`
(`IMAGE_EXTS`, `MAX_ASSET_BYTES`, `normalize_asset_name`), `config` (redacción) y, de forma **diferida**,
`pypdf`.

```python
from enum import Enum

class FileKind(Enum):
    IMAGE = "image"
    PDF = "pdf"
    UNSUPPORTED = "unsupported"

@dataclass
class IncomingFile:
    """Un Archivo_Entrante del turno: nombre + bytes ya decodificados + media type."""
    filename: str
    content: bytes
    media_type: str | None = None      # se deriva de la extensión si falta

@dataclass
class ImageBlock:
    """Bloque de imagen para el modelo de mensajes neutral (DD-M2)."""
    media_type: str                     # p. ej. "image/jpeg"
    data: str                           # bytes de la imagen en base64

@dataclass
class IngestResult:
    """Resultado de preparar los binarios de un turno para el Chat_Agent."""
    image_blocks: list[ImageBlock]      # imágenes para complete_chat (si hay visión)
    asset_binaries: dict[str, bytes]    # filename normalizado -> bytes, para inyección (DD-M4)
    pdf_texts: list[str]                # Texto_Extraido (redactado) por PDF, para el contexto
    rejected: list[str]                 # mensajes accionables de archivos no soportados/ inválidos

#: Límite de tamaño de un PDF entrante (Req 10.4). En MiB para el mensaje de rechazo.
MAX_PDF_BYTES = 20 * 1024 * 1024

#: Media types de imagen que un modelo de visión acepta como raster (DD-M7).
_VISION_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}
#: Nombre del extra opcional a instalar si falta la PDF_Library (Req 9.4).
_PDF_EXTRA = "pdf"

def classify_file(filename: str) -> FileKind:
    """Clasifica un Archivo_Entrante por su extensión (Req 1.1).

    IMAGE si la extensión (en minúsculas) ∈ IMAGE_EXTS; PDF si es '.pdf';
    UNSUPPORTED en cualquier otro caso. Función pura sobre el nombre.
    """

def prepare_incoming(
    files: list[IncomingFile], *, supports_vision: bool
) -> IngestResult:
    """Enruta y valida los binarios del turno (Req 1.1–1.4, 2.2, 3.2, 10, DD-M1/5/7/9).

    Por cada archivo:
      - UNSUPPORTED -> agrega un mensaje accionable a `rejected` que lista los tipos
        soportados (Req 1.4); no produce efectos.
      - IMAGE -> valida la extensión con `normalize_asset_name(filename, IMAGE_EXTS)`
        (Req 10.1, 10.2) y el tamaño contra `MAX_ASSET_BYTES` sobre los bytes
        decodificados (Req 10.3, 10.5); registra los bytes en `asset_binaries`
        (para la inyección de DD-M4). Si `supports_vision` y el media type es raster
        soportado, agrega un `ImageBlock` (base64 + media_type) a `image_blocks`
        (Req 2.2). Si no hay visión, la imagen NO se envía al modelo (se guardará
        igual como asset y el agente lo informa, DD-M7).
      - PDF -> valida el tamaño contra `MAX_PDF_BYTES` sobre los bytes decodificados
        (Req 10.4, 10.5) y extrae el texto con `extract_pdf_text` EN MEMORIA
        (Req 3.1, 3.5, 11.5); agrega el `Texto_Extraido` redactado a `pdf_texts`
        (Req 3.2, 11.2). El binario del PDF nunca se escribe a disco (DD-M5).

    NO escribe el contrato ni assets: eso lo hace el Chat_Agent vía las intake tools
    (Req 1.5). Un archivo inválido no aborta el turno: se acumula en `rejected`.
    """

def extract_pdf_text(data: bytes) -> str:
    """Extrae el Texto_Extraido de un PDF en memoria con la PDF_Library (Req 3.1, 9.3).

    Usa `pypdf.PdfReader(io.BytesIO(data))` y concatena `page.extract_text()` de cada
    página. Import diferido de `pypdf`; si el extra no está instalado, lanza un error
    que NOMBRA el extra a instalar (`pip install puriq[pdf]`, Req 9.4). Si el PDF no
    contiene texto legible (p. ej. escaneado), lanza un ValueError accionable que
    indica que no se pudo extraer texto y sugiere una acción (Req 3.6). El binario se
    procesa solo en memoria; nunca se persiste (Req 11.5).
    """
```

### 2. `agent/puriq/tools/generate_content.py` (Pieza 4, visión): `Message.images` + `complete_chat` multimodal

Se agrega el bloque de imagen al modelo neutral y `supports_vision` al protocolo; los traductores emiten
bloques de imagen solo cuando hay imágenes.

```python
@dataclass
class ImageContent:
    """Imagen asociada a un mensaje (DD-M2), independiente del proveedor."""
    media_type: str                     # "image/jpeg" | "image/png" | "image/webp" | "image/gif"
    data: str                           # bytes de la imagen en base64

@dataclass
class Message:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_result: ToolResult | None = None
    images: list[ImageContent] | None = None   # NUEVO: solo en mensajes "user"

class LLMProvider(Protocol):
    supports_vision: bool                       # NUEVO (Req 5.4, DD-M7)
    def complete(self, prompt: str) -> str: ...
    def complete_chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> ChatResult: ...
```

- **`BedrockProvider`** (`supports_vision = True`, Req 5.2): `_messages_to_claude` agrega, en el
  `content` del mensaje `user`, un bloque por imagen
  `{"type": "image", "source": {"type": "base64", "media_type": m, "data": d}}` **junto** a los bloques
  de texto (Req 4.2, 4.5). El resto del cuerpo Claude y el parseo de la respuesta no cambian.
- **`OpenAICompatibleProvider`** (`supports_vision = True`, Req 5.3): `_messages_to_openai` convierte el
  `content` del mensaje `user` con imágenes en una **lista de partes**:
  `[{"type": "text", "text": ...}, {"type": "image_url", "image_url": {"url": "data:<media_type>;base64,<data>"}}]`
  (Req 4.2, 4.5). Sin imágenes, `content` sigue siendo el string de siempre (DD-M3).
- **`OllamaProvider`** (`supports_vision = False`): `complete_chat` ya rechaza el tool-use nombrando
  `PURIQ_LLM_MODE`; el guard multimodal cae en la misma vía (Req 5.4).
- **Guard multimodal compartido** (Req 5.4, DD-M7): al inicio de `complete_chat`, si algún mensaje trae
  `images` y `not self.supports_vision`, se lanza un `RuntimeError` accionable que **nombra
  `PURIQ_LLM_MODE`** y los modos con visión (`bedrock`, `openai`).
- **Invariante text-only (Req 4.3, DD-M3)**: los helpers solo agregan bloques/partes de imagen cuando
  `msg.images` es no vacío; en caso contrario su salida es idéntica a la del Hito 2. `complete(prompt)`
  no cambia (Req 4.4). Las credenciales se siguen leyendo con `get_env(..., secret=True)` (Req 11.3).

### 3. `agent/puriq/intake/tools.py`: la intake tool `extract_pdf` (Extract_PDF_Tool)

Se agrega la función, su handler y su spec a `INTAKE_TOOL_SPECS` (aditivo), reutilizando el patrón de
`attach_asset` para la fuente del binario.

```python
def extract_pdf(
    project: Path, *, content_base64: str | None = None, source_path: str | None = None
) -> dict:
    """Extrae el Texto_Extraido de un PDF (base64 o ruta) y lo devuelve redactado (Req 7.1–7.3).

    Acepta EXACTAMENTE UNA fuente (DD-6 de attach_asset): `content_base64` o
    `source_path`. Si faltan ambas o vienen ambas, lanza un ValueError accionable que
    indica que se requiere exactamente una fuente (Req 7.3). Obtiene los bytes,
    comprueba `len(bytes) <= MAX_PDF_BYTES` antes de procesar (Req 10.4, 10.5),
    delega en `ingest.extract_pdf_text` (Req 7.2) y devuelve
    `config.redact_value({"text": Texto_Extraido})` (Req 11.2). El PDF no se persiste
    (Req 11.5). Excepciones tipadas se dejan propagar para su traducción en el borde
    del intake (run_intake_tool -> wizard_error_response).
    """
```

Su `inputSchema` (JSON Schema puro) declara `project` (requerido), `content_base64` y `source_path`
(ambos opcionales, exactamente uno en tiempo de ejecución), `additionalProperties: false`, y un
`handler` `_h_extract_pdf` que desempaqueta `arguments`. Se agrega a `INTAKE_TOOL_SPECS`, con lo que
`INTAKE_TOOL_NAMES`, `_INTAKE_HANDLERS` y `_INTAKE_TOOL_DOCS` (con `documento = None`, como `get_state`)
lo cubren por construcción. `attach_asset` no se toca (Req 7.4).

### 4. `agent/puriq/intake/agent.py` (`Chat_Agent`): binarios, visión e inyección de bytes

```python
@dataclass
class ChatRequest:
    mensaje: str
    archivos: list[str] = field(default_factory=list)     # referencias (Hito 2)
    binarios: list[IncomingFile] = field(default_factory=list)  # NUEVO: binarios reales
```

`run_turn` se extiende (conservando el bucle del Hito 2):

1. **Cargar sesión** y **estado inicial** (igual que el Hito 2).
2. **Preparar binarios** con `ingest.prepare_incoming(request.binarios,
   supports_vision=self._provider.supports_vision)` (Req 1, 2.2, 3.2, 10, DD-M1/5/7/9). Guarda el mapa
   `asset_binaries` (filename → bytes) para la inyección de DD-M4.
3. **Construir el mensaje de usuario**: texto + referencias (como hoy) + los `pdf_texts` extraídos como
   **contexto** (Req 3.2) + los mensajes de `rejected` (para que el asistente informe). Si hay
   `image_blocks`, se adjuntan como `Message.images` del mensaje de usuario (Req 2.2). Si el proveedor no
   tiene visión pero llegaron imágenes, se agrega una nota accionable que nombra `PURIQ_LLM_MODE`
   (DD-M7).
4. **Bucle de tool-use** (igual que el Hito 2), con la inyección extendida (Req 1.8, 2.1, DD-M2/M4):
   por cada tool-call, inyectar `project`; **además**, si la tool es `attach_asset` y su `filename`
   coincide con una entrada de `asset_binaries`, inyectar `content_base64 = base64(asset_binaries[fname])`
   en `arguments` antes de despachar por `run_intake_tool`. Así el guardado/asociación de la imagen se
   delega en `attach_asset` con los bytes del turno, sin que el LLM los cargue.
5. **Estado final**, **persistir sesión** (redactada; los binarios y el `content_base64` inyectado
   **no** se guardan en el historial, análogo a `project`, DD-M4/DD-2) y **devolver**
   `ChatResponse(respuesta, estado)`.

La confirmación (Req 8) no requiere estado nuevo: el `Intake_Prompt` instruye al LLM a **proponer** el
`Contenido_Derivado` en la respuesta y **no** llamar a las tools de escritura hasta que el usuario
confirme en el turno siguiente; el bucle existente escribe solo cuando el modelo emite la tool-call.

### 5. `agent/puriq/intake/prompt.py` (`Intake_Prompt`) e `INTAKE_GUION`: guion multimodal

Se agregan instrucciones multimodales (Req 12), embebidas en el guion compartido `INTAKE_GUION` (para
que la Superficie A las lea vía el recurso `intake://guion`) y reforzadas en `build_system_prompt`:

- Pedir **proactivamente** imágenes de los lugares y PDFs de contexto en las fases correspondientes
  (Req 12.1; ya presente en las fases 3, 7 y 8 del guion, se refuerza para multimodal).
- Usar la **descripción de la imagen** (que el modelo obtiene por visión) al proponer el `Alt_Text` y la
  descripción del Place/Event asociado (Req 12.2).
- **Destilar** el `Texto_Extraido` de un PDF a descripciones, Q&A y datos históricos mediante las intake
  tools (`add_qa`, `edit_item`, `add_place`), en lugar de publicar el PDF (Req 12.3).
- **Pedir la confirmación** del usuario antes de escribir cualquier `Contenido_Derivado` (Req 8.1, 12.4):
  proponer primero en la respuesta, invocar la tool de escritura recién tras el "sí".

### 6. `agent/puriq/wizard/server.py`: `POST /api/chat` con binarios (multipart)

El endpoint acepta las dos formas por `Content-Type` (DD-M8, Req 6.1, 6.3):

```python
@app.post("/api/chat")
async def chat(request: Request):
    """Corre un turno del Chat_Agent; acepta refs (JSON) o binarios (multipart) (Req 6, 11).

    - application/json  -> {mensaje, archivos[]}  (Hito 2, referencias; binarios=[]).
    - multipart/form-data -> campos `mensaje` (str), `archivos` (referencias, repetidas
      o JSON) y `binarios` (UploadFile[]). Cada UploadFile se lee a bytes y se envuelve
      en IncomingFile(filename, content) para el Ingest_Router (Req 6.1, 6.2).
    Construye ChatAgent(project), corre run_turn y responde
    redact_value({respuesta, estado}) (Req 6.4, 11.2). Errores de validacion -> 422;
    el resto -> 500; ambos traducidos y redactados por wizard_error_response (Req 11.4).
    """
```

Se conserva el modelo `ChatBody` para el camino JSON. La lectura de binarios reutiliza el patrón de
`upload_asset` (leer `UploadFile`), pero **no** escribe a `assets/` desde el endpoint: la escritura la
hace `attach_asset` dentro del `Chat_Agent` (Req 6.2, 1.5). Se sirve solo en `127.0.0.1` (Req 11.1).

### 7. `agent/puriq/wizard/static/` (`Chat_Panel`): adjuntar binarios

El `Chat_Panel` gana un adjuntador de archivos (reutilizando el drag & drop que ya existe para
`POST /api/assets`): cuando el turno lleva binarios, envía `multipart/form-data` a `/api/chat`
(`mensaje`, `archivos`, `binarios`); cuando no, mantiene el `POST` JSON del Hito 2 (Req 6.3). Tras la
respuesta, vuelca `estado` en `state.server` y llama `updateSkeleton()` (igual que el Hito 2). La capa de
UI sigue siendo delgada; su comportamiento se cubre con revisión/DOM, no con PBT.

### 8. `agent/puriq/mcp/server.py`: Extract_PDF_Tool aditiva (Superficie A)

No requiere cambios de código: `extract_pdf` entra por `INTAKE_TOOL_SPECS`, con lo que `TOOL_SPECS`,
`list_tools`, `call_tool` (que enruta las intake tools por `run_intake_tool`) e `INTAKE_TOOL_NAMES` la
cubren por construcción (Req 7.1, 7.5). El cliente MCP invoca `extract_pdf` con base64 o ruta, obtiene el
`Texto_Extraido` redactado y lo destila con las intake tools; la **visión** sobre las imágenes la ejecuta
el LLM del cliente, y el guardado usa `attach_asset` (Req 7.4). Todas las tools de pipeline, edición e
intake previas siguen registradas (Req 7.5).

### 9. `agent/pyproject.toml`: dependencia `pypdf` (extra opcional, versión fijada)

Se agrega un extra opcional con la PDF_Library **fijada a una versión exacta** (Req 9.1, 9.2), siguiendo
la convención de extras del proyecto (`local`, `mcp`, `test`):

```toml
[project.optional-dependencies]
pdf = ["pypdf==6.0.0"]     # extraccion de texto de PDF (pura Python, sin binarios del sistema)
```

El nombre del extra (`pdf`) es el que `extract_pdf_text` menciona en el mensaje de error cuando `pypdf`
no está instalado (`pip install puriq[pdf]`, Req 9.4). La versión exacta se ancla al último estable al
implementar (el rango 5.x–6.x es compatible con `requires-python >=3.10`).

## Data Models

Esta fase **no introduce modelos persistidos nuevos en el contrato**: sigue operando sobre los tres
documentos JSON del Hito 1 vía `run_intake_tool`/`get_state`, y sobre `assets/` (imágenes) por
`attach_asset`. Los modelos nuevos son de **entrada/salida del turno** y de **transporte multimodal**.

### Archivo_Entrante y resultado de ingesta

- **IncomingFile**: `{ filename: str, content: bytes, media_type: str | None }`. El `media_type` se
  deriva de la extensión si falta. Los bytes ya vienen **decodificados** (el endpoint decodifica el
  multipart; el MCP decodifica el base64 en el handler de `extract_pdf`), y el tamaño se valida sobre
  ellos (Req 10.5).
- **IngestResult**: `{ image_blocks: ImageBlock[], asset_binaries: {filename: bytes}, pdf_texts: str[],
  rejected: str[] }` (ver Components). `pdf_texts` va **redactado** (Req 11.2).
- **ImageBlock / ImageContent**: `{ media_type: str, data: str(base64) }`.

### Modelo de mensajes multimodal (`complete_chat`)

`Message` gana `images: list[ImageContent] | None`. Traducción por proveedor (solo cuando hay imágenes):

- **Bedrock/Claude**: bloque `{"type": "image", "source": {"type": "base64", "media_type": m,
  "data": d}}` en el `content` del mensaje `user`, junto al bloque `{"type": "text", ...}`.
- **OpenAI-compatible**: `content` del `user` como partes
  `[{"type": "text", ...}, {"type": "image_url", "image_url": {"url": "data:m;base64,d"}}]`.

Persistencia de sesión: como en el Hito 2, el historial se serializa **sin** los binarios ni el
`content_base64` inyectado (DD-M4); los `Message.images` **no** se guardan en `content/.intake-session.json`
(evita persistir bytes y filtrar contenido), y todo pasa por `config.redact_value` antes de escribir.

### Entrada/salida de la Extract_PDF_Tool

- **Entrada**: `{ project: str, content_base64?: str, source_path?: str }` (exactamente una fuente,
  Req 7.3).
- **Salida**: `{ text: <Texto_Extraido> }`, redactada (Req 7.2, 11.2). Ante error (fuente inválida, PDF
  sin texto, tamaño excedido, extra ausente), `run_intake_tool` devuelve un `{causa, acción}` accionable
  y redactado.

### Límites de tamaño

- **Imagen**: `MAX_ASSET_BYTES` (10 MiB, reutilizado de `wizard/assets.py`).
- **PDF**: `MAX_PDF_BYTES` (20 MiB, nuevo, en `intake/ingest.py`).
- Ambos se comprueban sobre bytes **decodificados**, **antes** de escribir o invocar al modelo/extractor
  (Req 10.5, DD-M9).

## Correctness Properties

*Una propiedad es una característica o comportamiento que debe cumplirse en todas las ejecuciones
válidas del sistema: esencialmente, un enunciado formal de lo que el sistema debe hacer. Las propiedades
son el puente entre las especificaciones legibles por humanos y las garantías de corrección verificables
por máquina.*

Estas propiedades se ejercitan sobre un **proyecto temporal** (con los 3 JSON del contrato) y, cuando
interviene el bucle, un **proveedor de LLM mock** inyectado en `ChatAgent(provider=...)` con un
`supports_vision` configurable, de modo que el flujo es determinista y sin coste externo. La lógica de
cada intake tool (validación, atomicidad, integridad referencial) ya está cubierta por las propiedades
del Hito 1; aquí se prueban las invariantes de la **ingesta multimodal**: clasificación y validación de
archivos, no-persistencia del PDF, traducción multimodal, convivencia text-only, inyección de bytes,
degradación sin visión, aditividad del MCP y redacción.

Tras el prework se realizó una reflexión para eliminar redundancias: 10.1+10.2 se consolidan en la
Property 3; 10.3+10.4+10.5 en la Property 4; 3.5+11.5 en la Property 5; 4.3+4.4 en la Property 7;
4.1+4.2+4.5 en la Property 8; 11.2+7.2 (redacción) en la Property 14. Los criterios de prompt (2.3, 8.x,
12.x), de configuración/red (9.1, 9.2, 11.1) y de backends externos (3.1, 5.2, 5.3) no son propiedades:
se cubren con ejemplos, integración con mocks o smoke/revisión (ver Testing Strategy).

### Property 1: La clasificación por extensión es total y correcta

*Para todo* nombre de Archivo_Entrante, `classify_file` devuelve `IMAGE` si y solo si su extensión (en
minúsculas) pertenece a `IMAGE_EXTS`, `PDF` si y solo si es `.pdf`, y `UNSUPPORTED` en cualquier otro
caso.

**Validates: Requirements 1.1**

### Property 2: Los archivos no soportados se rechazan sin efectos

*Para todo* Archivo_Entrante cuya extensión no está en `IMAGE_EXTS` ni es `.pdf`, la preparación del
Ingest_Router produce un mensaje accionable que lista los tipos soportados y deja los tres documentos del
contrato y el directorio `assets/` byte a byte idénticos.

**Validates: Requirements 1.4, 1.5**

### Property 3: La validación de imagen coincide con `normalize_asset_name`

*Para todo* nombre de archivo de imagen, el Ingest_Router acepta la imagen exactamente cuando
`normalize_asset_name(filename, IMAGE_EXTS)` no lanza, y la rechaza en otro caso con un mensaje que lista
los formatos de imagen aceptados.

**Validates: Requirements 10.1, 10.2**

### Property 4: El tamaño se valida antes de cualquier efecto

*Para todo* Archivo_Entrante cuyos bytes decodificados exceden su límite (imagen: `MAX_ASSET_BYTES`;
PDF: `MAX_PDF_BYTES`), el Ingest_Router lo rechaza con un mensaje que indica el límite y no realiza
ninguna escritura en disco ni invoca al modelo ni al extractor de PDF.

**Validates: Requirements 10.3, 10.4, 10.5**

### Property 5: El binario del PDF nunca se persiste

*Para toda* ingesta de un PDF, no se crea ningún archivo bajo `assets/` ni se escribe el binario del PDF
en el proyecto: la extracción ocurre solo en memoria.

**Validates: Requirements 3.5, 11.5**

### Property 6: El Texto_Extraido entra en el contexto del turno

*Para todo* texto que el PDF_Extractor devuelve, el contenido del mensaje de usuario que el Chat_Agent
pasa a `complete_chat` contiene ese texto.

**Validates: Requirements 3.2**

### Property 7: Sin imágenes, `complete_chat` preserva el comportamiento text-only

*Para toda* lista de mensajes sin imágenes, la traducción de cada proveedor (Bedrock y compatible con
OpenAI) es idéntica a la traducción text-only del Hito 2, y `complete(prompt)` conserva su firma y su
comportamiento.

**Validates: Requirements 4.3, 4.4**

### Property 8: La traducción multimodal transporta cada imagen junto con las tools

*Para toda* lista de mensajes con una o más imágenes y cualquier conjunto de tools, la traducción de cada
proveedor incluye, por cada imagen, un bloque de imagen nativo con sus datos en base64 y su media type, y
además incluye las tools traducidas.

**Validates: Requirements 4.1, 4.2, 4.5**

### Property 9: El agente envía la imagen a un proveedor con visión

*Para todo* binario de imagen válido de un turno, cuando el proveedor declara soporte de visión, el
`Message` de usuario que el Chat_Agent pasa a `complete_chat` incluye esa imagen (base64 + media type).

**Validates: Requirements 2.2**

### Property 10: El agente inyecta los bytes de la imagen por nombre de archivo

*Para toda* tool-call `attach_asset` emitida por el modelo cuyo `filename` coincide con un binario de
imagen del turno, los argumentos que el Chat_Agent entrega a `run_intake_tool` contienen `content_base64`
igual a esos bytes (y `project` igual al Project_Root), de modo que los bytes no transitan por el modelo.

**Validates: Requirements 2.1**

### Property 11: Un proveedor sin visión rechaza las imágenes nombrando `PURIQ_LLM_MODE`

*Para toda* lista de mensajes que contiene al menos una imagen, un proveedor sin soporte de visión
rechaza `complete_chat` con un error accionable que nombra la variable `PURIQ_LLM_MODE` y los modos con
visión disponibles.

**Validates: Requirements 5.4**

### Property 12: La Extract_PDF_Tool exige exactamente una fuente

*Para toda* combinación de presencia/ausencia de `content_base64` y `source_path`, `extract_pdf` procede
solo cuando se provee exactamente una de las dos fuentes; si faltan ambas o vienen ambas, se rechaza con
un mensaje que indica que se requiere exactamente una fuente.

**Validates: Requirements 7.3**

### Property 13: El registro de tools es aditivo y conserva las existentes

*Para todo* nombre de tool registrado antes de esta fase (las 11 de pipeline/edición y las 12 intake,
incluida `attach_asset`), ese nombre sigue presente en `TOOL_SPECS` tras agregar la Extract_PDF_Tool.

**Validates: Requirements 7.4, 7.5**

### Property 14: El Texto_Extraido y el Contenido_Derivado se devuelven redactados

*Para todo* Texto_Extraido o Contenido_Derivado que contendría un valor registrado como secreto, la
respuesta devuelta (por la Extract_PDF_Tool o por el turno del chat) no contiene el valor crudo del
secreto.

**Validates: Requirements 11.2, 7.2**

### Property 15: Los errores de ingesta o visión se entregan traducidos y redactados

*Para toda* excepción que se produce durante la ingesta o la visión, la respuesta de la superficie es el
error traducido por `wizard_error_response` (`{causa, acción}` o `{documento, campo, sugerencia}`), sin
trazas crudas ni valores de secretos.

**Validates: Requirements 11.4**

## Error Handling

El manejo de errores reutiliza la única fuente de verdad (`errors.wizard_error_response` +
`config.redact`/`redact_value`) y el despacho ya endurecido del Hito 1 (`run_intake_tool`). No se
introducen mensajes ad hoc.

- **Archivo no soportado o inválido (Req 1.4, 10.2).** El Ingest_Router no aborta el turno: acumula un
  mensaje accionable en `rejected` (que lista los tipos/formatos soportados) y sigue con el resto de los
  archivos. El Chat_Agent incorpora esos mensajes al contexto para que el asistente los comunique.

- **Tamaño excedido (Req 10.3, 10.4, 10.5).** La comprobación se hace sobre los bytes decodificados
  **antes** de cualquier efecto; una imagen o un PDF sobredimensionado se rechaza con un mensaje que
  indica el límite, sin escribir en disco ni invocar al modelo/extractor.

- **PDF sin texto legible (Req 3.6).** `extract_pdf_text` lanza un `ValueError` accionable ("no se pudo
  extraer texto…") que `wizard_error_response` traduce a `{causa, acción}` redactado. La `Extract_PDF_Tool`
  lo devuelve por `run_intake_tool` sin propagar.

- **PDF_Library ausente (Req 9.4).** El import diferido de `pypdf` falla con un `ModuleNotFoundError`
  traducido a un mensaje que **nombra el extra** a instalar (`pip install puriq[pdf]`).

- **Fuente del PDF inválida (Req 7.3).** Ni base64 ni ruta, o ambas → `ValueError` accionable que indica
  que se requiere exactamente una fuente (mismo criterio que `attach_asset`).

- **Proveedor sin visión (Req 5.4, DD-M7).** `complete_chat` rechaza las imágenes con un error que nombra
  `PURIQ_LLM_MODE` y los modos con visión. El Chat_Agent evita disparar ese error consultando
  `supports_vision` antes de adjuntar la imagen: guarda el asset igual y añade una nota accionable.

- **Credencial faltante (Req 5.5).** `get_env(..., required=True, secret=True)` lanza
  `MissingEnvVarError` que nombra la variable sin exponer su valor; se traduce con `wizard_error_response`.

- **Borde del endpoint (Req 6.4, 11.4).** `POST /api/chat` envuelve el turno en `try/except`: errores de
  validación/entrada → `422`; el resto → `500`; ambos traducidos y redactados. La red de seguridad
  transversal de `server.py` (`RequestValidationError` → 422, `Exception` → 500) cubre lo inesperado.

- **Redacción del `Texto_Extraido`/`Contenido_Derivado` (Req 11.2).** Todo texto extraído o derivado pasa
  por `config.redact_value` antes de devolverse o incluirse en una respuesta.

## Testing Strategy

Enfoque dual: **property-based** para las invariantes universales de la ingesta multimodal, y
**ejemplo/integración** para el cableado, los backends externos multimodales, la extracción real de PDF,
la configuración y la UI. Coherente con el estilo de los Hitos 1 y 2.

### Property-based testing

- **Librería.** **Hypothesis** (Python), ya presente en el proyecto (`agent/.hypothesis/`, extra
  `test`). No se implementa PBT desde cero.
- **Configuración.** Cada prueba de propiedad corre un **mínimo de 100 iteraciones**
  (`@settings(max_examples=100)` o superior).
- **Aislamiento.** Las propiedades operan sobre un **proyecto temporal** (`tmp_path`) y, cuando
  interviene el bucle, un **proveedor mock** inyectado en `ChatAgent(provider=...)` con `supports_vision`
  configurable y un **PDF_Extractor mock** (para no depender de PDFs reales). El proveedor mock cuenta
  sus invocaciones (para la Property 4).
- **Generadores.** Estrategias para: nombres de archivo con extensiones dentro/fuera de `IMAGE_EXTS` y
  `.pdf`; bytes de imagen de tamaños alrededor de `MAX_ASSET_BYTES`; bytes de PDF alrededor de
  `MAX_PDF_BYTES`; textos extraídos arbitrarios (con y sin un valor marcado como secreto); listas de
  `Message` con y sin `images` y con y sin tools; combinaciones de presencia de `content_base64`/
  `source_path`; y subconjuntos de `INTAKE_TOOL_SPECS`/`TOOL_SPECS`.
- **Mapa propiedad → prueba.** Cada una de las 15 propiedades se implementa con **una sola** prueba de
  propiedad.
- **Etiquetado.** Cada prueba lleva un comentario con el formato:
  `# Feature: multimodal-ingest, Property {N}: {texto de la propiedad}`.

### Pruebas de ejemplo y de integración

- **Ejemplos (unit):**
  - Router: una imagen válida produce `ImageBlock`/`asset_binaries` y no extracción (1.2); un PDF invoca
    el extractor y no el tratamiento de imagen (1.3).
  - Bucle: un mock que emite `edit_item`/`add_qa` tras "confirmación" despacha por `run_intake_tool`
    (2.4, 3.3); un mock sin tool-calls de escritura deja el contrato sin cambios (2.5, 3.4, 8.4).
  - `complete(prompt)` sin cambios (regresión, 4.4); `get_provider()` resuelve por `PURIQ_LLM_MODE`
    (5.1); sin credencial → `MissingEnvVarError` que la nombra sin valor (5.5); la clave se lee con
    `get_env(secret=True)` (11.3).
  - Prompt: contiene la instrucción de pedir imágenes/PDFs (12.1), de usar la descripción para
    alt/descripción (12.2), de destilar el PDF sin publicarlo (12.3) y de pedir confirmación antes de
    escribir (8.1, 12.4).
  - MCP: `extract_pdf` está en `TOOL_SPECS` con su `inputSchema` (7.1); `attach_asset` sigue presente
    (7.4); `extract_pdf` delega en el extractor (mock) y devuelve `{text}` (7.2).
  - PDF: extra ausente → mensaje que nombra `pdf` (9.4); PDF sin texto → mensaje accionable (3.6).
- **Integración (mock del backend, 1–3 ejemplos):**
  - **Bedrock multimodal (5.2):** mock de `invoke_model`; verificar que el cuerpo Claude incluye un
    bloque `image` con `source.base64` + `media_type` y que coexiste con `tools`. El E2E real queda fuera
    de PBT.
  - **OpenAI vision (5.3):** mock de `httpx.post`; verificar `content` con parte `image_url`
    (`data:...;base64,...`). E2E real fuera de PBT.
  - **PDF real (3.1, 9.3):** 1–3 PDFs de muestra reales verifican que `extract_pdf_text` (pypdf) devuelve
    su texto.
  - **Endpoint (6.1, 6.2, 6.3, 6.4):** `TestClient` de FastAPI con un `ChatAgent` mock; `POST` multipart
    con `binarios` responde 200 con `{respuesta, estado}` (6.1); `POST` JSON con `archivos` mantiene el
    comportamiento del Hito 2 (6.3).
- **Smoke:** `serve()` liga el servidor a `127.0.0.1` (11.1); `pyproject.toml` declara exactamente una
  PDF_Library con pin exacto (9.1, 9.2).
- **UI del Chat_Panel (adjunto de binarios):** JS vanilla sin toolchain; se cubre con revisión/DOM
  ligera (envío multipart cuando hay binarios; refresco del preview tras la respuesta). No se aplica PBT
  a la capa de rendering, coherente con la guía de cuándo NO usar property-based testing.

### Balance

Las pruebas de propiedad cubren las invariantes universales de la ingesta multimodal (clasificación,
validación de tipo/tamaño, no-persistencia del PDF, traducción multimodal y convivencia text-only,
inyección de bytes, degradación sin visión, aditividad del MCP y redacción). Los ejemplos se reservan
para el cableado, los backends externos multimodales (con mocks), la extracción real de PDF, la
configuración y la UI; se evita multiplicar ejemplos donde una propiedad ya cubre el espacio de entradas.
El comportamiento del núcleo de intake (incluido `attach_asset`) y del bucle del chat no se re-testea
aquí: ya lo cubren las propiedades de los Hitos 1 y 2.
