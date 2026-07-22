# Documento de Requisitos

## Introducción

Este spec amplía Puriq con un conjunto de **tools de gestión de contenido** que dan a un usuario avanzado (a través de cualquier agente de IA compatible con MCP y también desde el CLI) un control mucho más rico sobre un proyecto turístico ya existente. Las tools operan sobre los documentos del contrato y el contenido del **proyecto local**, exactamente igual que las tools actuales de Puriq: son una capa fina sobre `puriq.core`/`puriq.tools`, nunca generan código de módulos, el LLM solo toca contenido/configuración, y toda escritura del contrato se valida contra `schemas/` (vía `puriq.schemas`) antes de persistirse.

El usuario decidió mantener Puriq **local**: sin servicio alojado, sin autenticación ni roles, sin base de datos y sin analítica. En consecuencia, estas tools trabajan sobre los archivos del proyecto (`tourism-data.json` y el contenido bajo `/content`) en la máquina del usuario.

Las capacidades cubiertas por este spec son:

1. **Gestión de artículos/blog con CRUD completo**: crear (asistido por LLM), listar y filtrar (por rango de fechas y por etiqueta/categoría), editar y eliminar artículos. Hoy el "blog" son archivos markdown sueltos bajo `/content` sin un modelo formal; este spec **define un modelo formal de Article** (markdown con frontmatter) para que los artículos se puedan listar, filtrar, editar y eliminar de forma fiable.
2. **Consulta de contenido con filtros**: listar/filtrar Places y Events de `tourism-data.json` (por categoría, etiqueta, rango de fechas, búsqueda por nombre, etc.).
3. **Edición y eliminación de Places y Events**: editar campos de un Place/Event existente por `id`; eliminar un Place/Event por `id`, manejando la integridad referencial (p. ej. un Event que referencia un Place eliminado).
4. **Actualización masiva desde CSV**: importar/actualizar Places o Events desde un archivo CSV, fusionando de forma segura (agrega nuevos, actualiza los que coinciden por `id`, sin pisar ediciones del usuario salvo que la actualización apunte explícitamente a un campo).
5. **Análisis SEO local**: analizar el contenido/sitio generado localmente para detectar problemas de SEO (meta descripciones/títulos faltantes, estructura de encabezados, texto alternativo de imágenes, calidad de slugs, etc.) y devolver sugerencias accionables.

Todas las tools se exponen **tanto por el MCP_Server** (agnóstico de agente: cualquier cliente MCP como Claude Desktop, Kiro o Cline puede usarlas) **como por el CLI**, sin duplicar lógica: el CLI, el MCP y cualquier agente llaman a la misma función de tool subyacente.

Este documento define QUÉ debe hacer cada tool, no CÓMO implementarla (eso corresponde al diseño).

### Alcance y exclusiones

Queda **explícitamente fuera de alcance** de este spec:

- **Analítica de visitas / métricas de tráfico del sitio**: requiere una integración de analítica alojada que no existe en el proyecto local.
- **Revisión de seguridad de base de datos**: no hay base de datos; el sitio es estático.
- **Control de acceso basado en roles / autenticación / multiusuario**: Puriq permanece local y de un solo usuario.
- **El chatbot RAG orientado al visitante**: es una funcionalidad separada.
- **Análisis de una URL publicada en vivo**: el análisis SEO se realiza sobre la salida/contenido local, no sobre un sitio ya publicado.

## Glosario

- **Puriq**: El agente completo en Python (CLI + core + tools + wizard + MCP).
- **Contrato**: El conjunto de documentos JSON validados contra `schemas/` (principalmente `tourism-data.json`).
- **Tourism_Data**: Documento `tourism-data.json`; capa de contenido (site, places, events, categories).
- **Place**: Un lugar turístico dentro de `Tourism_Data.places`, identificado por `id`.
- **Event**: Un evento/festividad dentro de `Tourism_Data.events`, identificado por `id`.
- **Content_Store**: El directorio `/content` del proyecto donde residen los artículos del blog como archivos markdown.
- **Article**: Una entrada de blog representada como un archivo markdown con **frontmatter** en el Content_Store. Modelo formal definido por este spec.
- **Article_Frontmatter**: El bloque de metadatos al inicio de un Article (id/slug, title, date, tags, category, summary), seguido del cuerpo markdown (`body`).
- **Article_Schema**: El esquema (`schemas/article.schema.json`) contra el que se valida el Article_Frontmatter antes de persistir.
- **Manage_Articles**: Tool que implementa el CRUD de artículos (crear, listar/filtrar, editar, eliminar) sobre el Content_Store.
- **Query_Content**: Tool que lista y filtra Places y Events de Tourism_Data según criterios (categoría, etiqueta, fechas, nombre).
- **Edit_Content**: Tool que edita campos de un Place o Event existente por `id`.
- **Delete_Content**: Tool que elimina un Place o Event por `id`, manejando integridad referencial.
- **Bulk_Update**: Tool que importa/actualiza Places o Events desde un archivo CSV, fusionando de forma segura.
- **Analyze_SEO**: Tool que analiza el contenido/sitio generado localmente y devuelve sugerencias de SEO accionables.
- **MCP_Server**: Servidor MCP `tourism-builder` (`agent/puriq/mcp/server.py`) que expone las tools a cualquier cliente MCP.
- **CLI**: Interfaz de línea de comandos de Puriq (`agent/puriq/cli.py`).
- **LLM_Provider**: El proveedor de modelo de lenguaje; Amazon Bedrock (Claude) por defecto, con fallback local Ollama.
- **Slug**: Identificador en formato kebab-case ASCII que cumple el patrón `^[a-z0-9-]+$`, generado con `slugify`.
- **Redact**: La función `puriq.config.redact` que enmascara valores de secretos en cualquier texto de salida o error.

## Requisitos

### Requisito 1: Modelo formal de artículo (Article)

**Historia de usuario:** Como usuario avanzado, quiero que los artículos del blog tengan un modelo formal con metadatos estructurados, para poder listarlos, filtrarlos, editarlos y eliminarlos de forma fiable en lugar de manipular archivos markdown sueltos.

#### Criterios de aceptación

1. THE Manage_Articles SHALL representar cada Article como un archivo markdown en el Content_Store con un Article_Frontmatter que contenga los campos `id`, `title`, `date`, `tags`, `category`, `summary` y un cuerpo markdown (`body`).
2. THE Manage_Articles SHALL definir el campo `id` de un Article como un Slug que cumpla el patrón `^[a-z0-9-]+$`.
3. THE Manage_Articles SHALL definir el campo `date` de un Article como una fecha ISO en formato `YYYY-MM-DD`.
4. WHEN Manage_Articles enumera los artículos del proyecto, THE Manage_Articles SHALL derivar la colección de artículos leyendo el Article_Frontmatter de los archivos markdown del Content_Store.
5. WHEN Manage_Articles persiste un Article, THE Manage_Articles SHALL validar su Article_Frontmatter contra el Article_Schema antes de escribir el archivo.
6. IF el Article_Frontmatter de un archivo del Content_Store no cumple el Article_Schema, THEN THE Manage_Articles SHALL reportar un error que identifique el archivo y el campo que incumple.

### Requisito 2: Creación de artículos asistida por LLM (Manage_Articles)

**Historia de usuario:** Como usuario avanzado, quiero crear un artículo a partir de la información que aporto, con ayuda del LLM para redactar el contenido, para publicar entradas de blog completas sin escribir todo el markdown a mano.

#### Criterios de aceptación

1. WHEN Manage_Articles recibe una solicitud de creación con un título, THE Manage_Articles SHALL generar el `id` del Article como un Slug derivado del título.
2. WHERE el usuario no proporciona el cuerpo del Article, THE Manage_Articles SHALL generar el cuerpo con el LLM_Provider a partir de la información aportada por el usuario.
3. WHERE el usuario proporciona el cuerpo del Article, THE Manage_Articles SHALL conservar el cuerpo aportado sin modificarlo.
4. WHERE la solicitud de creación no incluye una fecha, THE Manage_Articles SHALL asignar la fecha actual al campo `date` del Article.
5. IF ya existe un Article con el mismo `id` en el Content_Store, THEN THE Manage_Articles SHALL reportar un error indicando que el artículo ya existe y no sobrescribir el archivo existente.
6. IF la solicitud de creación no incluye un título o el título está vacío, THEN THE Manage_Articles SHALL reportar un error indicando que el título es obligatorio.
7. WHEN Manage_Articles crea un Article correctamente, THE Manage_Articles SHALL escribir el archivo markdown en el Content_Store y devolver el `id` y la ruta del archivo creado.

### Requisito 3: Listado y filtrado de artículos (Manage_Articles)

**Historia de usuario:** Como usuario avanzado, quiero listar y filtrar los artículos por fecha y por etiqueta o categoría, para encontrar rápidamente el contenido que quiero revisar o modificar.

#### Criterios de aceptación

1. WHEN Manage_Articles recibe una solicitud de listado sin filtros, THE Manage_Articles SHALL devolver todos los artículos del Content_Store con sus campos de Article_Frontmatter.
2. WHERE la solicitud de listado incluye un rango de fechas, THE Manage_Articles SHALL devolver solo los artículos cuyo `date` esté dentro del rango indicado, inclusive los extremos.
3. WHERE la solicitud de listado incluye una etiqueta, THE Manage_Articles SHALL devolver solo los artículos cuyo campo `tags` contenga esa etiqueta.
4. WHERE la solicitud de listado incluye una categoría, THE Manage_Articles SHALL devolver solo los artículos cuyo campo `category` sea igual a la categoría indicada.
5. WHERE la solicitud de listado incluye más de un filtro, THE Manage_Articles SHALL devolver solo los artículos que satisfagan todos los filtros indicados.
6. WHEN ningún Article satisface los filtros indicados, THE Manage_Articles SHALL devolver una lista vacía sin reportar un error.
7. WHEN Manage_Articles devuelve la lista de artículos, THE Manage_Articles SHALL ordenar los resultados por `date` de forma descendente.

### Requisito 4: Edición de artículos (Manage_Articles)

**Historia de usuario:** Como usuario avanzado, quiero editar los campos de un artículo existente, para corregir o actualizar su contenido y metadatos de forma intencional.

#### Criterios de aceptación

1. WHEN Manage_Articles recibe una solicitud de edición con un `id` existente y uno o más campos, THE Manage_Articles SHALL actualizar en el Article solo los campos indicados y preservar los campos no indicados.
2. IF el `id` indicado en la solicitud de edición no corresponde a ningún Article del Content_Store, THEN THE Manage_Articles SHALL reportar un error que indique que el artículo no fue encontrado.
3. WHEN Manage_Articles edita el `title` de un Article, THE Manage_Articles SHALL conservar el `id` original del Article sin regenerarlo.
4. WHEN Manage_Articles aplica una edición, THE Manage_Articles SHALL validar el Article_Frontmatter resultante contra el Article_Schema antes de escribir el archivo.
5. IF la edición deja vacío un campo obligatorio del Article_Frontmatter, THEN THE Manage_Articles SHALL rechazar la edición y reportar un error que nombre el campo obligatorio.
6. WHEN Manage_Articles edita un Article correctamente, THE Manage_Articles SHALL escribir el archivo actualizado en el Content_Store y devolver el `id` del Article modificado.

### Requisito 5: Eliminación de artículos (Manage_Articles)

**Historia de usuario:** Como usuario avanzado, quiero eliminar un artículo por su identificador, para retirar de forma intencional contenido que ya no quiero publicar.

#### Criterios de aceptación

1. WHEN Manage_Articles recibe una solicitud de eliminación con un `id` existente, THE Manage_Articles SHALL eliminar del Content_Store el archivo markdown correspondiente a ese Article.
2. IF el `id` indicado en la solicitud de eliminación no corresponde a ningún Article del Content_Store, THEN THE Manage_Articles SHALL reportar un error que indique que el artículo no fue encontrado.
3. WHEN Manage_Articles elimina un Article correctamente, THE Manage_Articles SHALL devolver el `id` del Article eliminado.

### Requisito 6: Consulta y filtrado de Places y Events (Query_Content)

**Historia de usuario:** Como usuario avanzado, quiero listar y filtrar los lugares y eventos de mi contenido por distintos criterios, para localizar los elementos que quiero revisar o modificar.

#### Criterios de aceptación

1. WHEN Query_Content recibe una solicitud de consulta de Places sin filtros, THE Query_Content SHALL devolver todos los Places de Tourism_Data.
2. WHEN Query_Content recibe una solicitud de consulta de Events sin filtros, THE Query_Content SHALL devolver todos los Events de Tourism_Data.
3. WHERE la solicitud de consulta de Places incluye una categoría, THE Query_Content SHALL devolver solo los Places cuyo campo `category` sea igual a la categoría indicada.
4. WHERE la solicitud de consulta incluye una etiqueta, THE Query_Content SHALL devolver solo los Places cuyo campo `tags` contenga esa etiqueta.
5. WHERE la solicitud de consulta incluye un texto de búsqueda por nombre, THE Query_Content SHALL devolver solo los elementos cuyo campo `name` contenga ese texto sin distinguir mayúsculas de minúsculas.
6. WHERE la solicitud de consulta de Events incluye un rango de fechas, THE Query_Content SHALL devolver solo los Events cuyo `startDate` esté dentro del rango indicado, inclusive los extremos.
7. WHERE la solicitud de consulta incluye más de un filtro, THE Query_Content SHALL devolver solo los elementos que satisfagan todos los filtros indicados.
8. WHEN ningún elemento satisface los filtros indicados, THE Query_Content SHALL devolver una lista vacía sin reportar un error.

### Requisito 7: Edición de Places y Events (Edit_Content)

**Historia de usuario:** Como usuario avanzado, quiero editar los campos de un lugar o evento existente por su identificador, para actualizar mi contenido de forma intencional y validada.

#### Criterios de aceptación

1. WHEN Edit_Content recibe una solicitud de edición de un Place con un `id` existente y uno o más campos, THE Edit_Content SHALL actualizar en el Place solo los campos indicados y preservar los campos no indicados.
2. WHEN Edit_Content recibe una solicitud de edición de un Event con un `id` existente y uno o más campos, THE Edit_Content SHALL actualizar en el Event solo los campos indicados y preservar los campos no indicados.
3. IF el `id` indicado en la solicitud de edición no corresponde a ningún Place ni Event de Tourism_Data, THEN THE Edit_Content SHALL reportar un error que indique que el elemento no fue encontrado.
4. WHEN Edit_Content aplica una edición, THE Edit_Content SHALL validar el Tourism_Data resultante contra `tourism-data.schema.json` antes de persistirlo.
5. IF la edición produce un Tourism_Data que no cumple `tourism-data.schema.json`, THEN THE Edit_Content SHALL rechazar la edición, no escribir el archivo y reportar un error que identifique el campo que incumple.
6. WHEN Edit_Content edita un elemento correctamente, THE Edit_Content SHALL persistir el `tourism-data.json` actualizado y devolver el `id` del elemento modificado.

### Requisito 8: Eliminación de Places y Events con integridad referencial (Delete_Content)

**Historia de usuario:** Como usuario avanzado, quiero eliminar un lugar o evento por su identificador de forma segura, para retirar contenido sin dejar referencias rotas en el resto del contenido.

#### Criterios de aceptación

1. WHEN Delete_Content recibe una solicitud de eliminación de un Event con un `id` existente, THE Delete_Content SHALL eliminar ese Event de Tourism_Data.
2. WHEN Delete_Content recibe una solicitud de eliminación de un Place con un `id` existente, THE Delete_Content SHALL eliminar ese Place de Tourism_Data.
3. IF el `id` indicado en la solicitud de eliminación no corresponde a ningún Place ni Event de Tourism_Data, THEN THE Delete_Content SHALL reportar un error que indique que el elemento no fue encontrado.
4. IF existe al menos un Event cuyo `placeId` referencia el Place que se solicita eliminar, THEN THE Delete_Content SHALL informar los Events afectados por la referencia antes de completar la eliminación.
5. WHEN Delete_Content elimina un Place referenciado por Events, THE Delete_Content SHALL dejar el Tourism_Data resultante sin referencias `placeId` que apunten al Place eliminado.
6. WHEN Delete_Content aplica una eliminación, THE Delete_Content SHALL validar el Tourism_Data resultante contra `tourism-data.schema.json` antes de persistirlo.
7. WHEN Delete_Content elimina un elemento correctamente, THE Delete_Content SHALL persistir el `tourism-data.json` actualizado y devolver el `id` del elemento eliminado.

### Requisito 9: Actualización masiva desde CSV (Bulk_Update)

**Historia de usuario:** Como usuario avanzado, quiero importar o actualizar lugares y eventos en lote desde un archivo CSV, para mantener mi contenido al día sin editar cada elemento manualmente.

#### Criterios de aceptación

1. WHEN Bulk_Update recibe un archivo CSV de Places y una fila cuyo `id` no existe en Tourism_Data, THE Bulk_Update SHALL agregar un nuevo Place a Tourism_Data a partir de esa fila.
2. WHEN Bulk_Update recibe un archivo CSV de Places y una fila cuyo `id` coincide con un Place existente, THE Bulk_Update SHALL actualizar en el Place existente solo los campos presentes en la fila y preservar los campos no presentes en la fila.
3. WHEN Bulk_Update recibe un archivo CSV de Events, THE Bulk_Update SHALL aplicar la misma regla de fusión por `id` que para los Places (agregar los nuevos y actualizar por coincidencia de `id`).
4. IF una fila del CSV no incluye un `id` y no incluye un `name` del que derivar un Slug, THEN THE Bulk_Update SHALL omitir esa fila y registrar el número de fila omitida.
5. IF una fila del CSV contiene un valor inválido para un campo tipado (por ejemplo `lat`/`lng` no numérico o una fecha con formato incorrecto), THEN THE Bulk_Update SHALL reportar un error que identifique el número de fila y la columna inválida.
6. WHEN Bulk_Update finaliza la fusión, THE Bulk_Update SHALL validar el Tourism_Data resultante contra `tourism-data.schema.json` antes de persistirlo.
7. IF el Tourism_Data resultante de la fusión no cumple `tourism-data.schema.json`, THEN THE Bulk_Update SHALL no escribir el archivo y reportar un error que identifique el campo que incumple.
8. WHEN Bulk_Update aplica la actualización correctamente, THE Bulk_Update SHALL persistir el `tourism-data.json` actualizado y devolver un resumen con la cantidad de elementos agregados y actualizados.

### Requisito 10: Análisis SEO del contenido local (Analyze_SEO)

**Historia de usuario:** Como usuario avanzado, quiero analizar la calidad SEO de mi contenido y sitio generado localmente, para recibir sugerencias accionables antes de publicar.

#### Criterios de aceptación

1. WHEN Analyze_SEO recibe una solicitud de análisis, THE Analyze_SEO SHALL analizar el contenido y la salida generada localmente por el proyecto, sin consultar ninguna URL publicada en vivo.
2. WHERE un Place, Event o Article carece de meta descripción o de resumen, THE Analyze_SEO SHALL reportar una sugerencia que identifique el elemento y el campo faltante.
3. WHERE el contenido generado de un elemento carece de un título adecuado, THE Analyze_SEO SHALL reportar una sugerencia que identifique el elemento.
4. WHERE una imagen del contenido carece de texto alternativo, THE Analyze_SEO SHALL reportar una sugerencia que identifique la imagen y el elemento asociado.
5. WHERE la estructura de encabezados de una página generada no sigue una jerarquía correcta, THE Analyze_SEO SHALL reportar una sugerencia que identifique la página afectada.
6. WHERE el Slug de un elemento no cumple el patrón `^[a-z0-9-]+$` o excede una longitud recomendada, THE Analyze_SEO SHALL reportar una sugerencia que identifique el elemento y el problema del Slug.
7. WHEN Analyze_SEO no detecta ningún problema de SEO, THE Analyze_SEO SHALL devolver un resultado que indique que no se encontraron problemas.
8. WHEN Analyze_SEO finaliza el análisis, THE Analyze_SEO SHALL devolver la lista de sugerencias sin modificar el contenido del proyecto.

### Requisito 11: Exposición de las tools vía MCP y CLI (MCP_Server y CLI)

**Historia de usuario:** Como usuario avanzado, quiero usar las tools de gestión de contenido tanto desde cualquier agente compatible con MCP como desde la línea de comandos, para operar mi proyecto con la interfaz que prefiera sin diferencias de comportamiento.

#### Criterios de aceptación

1. WHEN el MCP_Server se inicia, THE MCP_Server SHALL registrar las tools `manage_articles`, `query_content`, `edit_content`, `delete_content`, `bulk_update` y `analyze_seo`.
2. WHEN un cliente MCP invoca una de estas tools, THE MCP_Server SHALL delegar en la misma implementación de `puriq.core`/`puriq.tools` que usa el CLI, sin duplicar la lógica.
3. WHEN el CLI invoca una de estas tools, THE CLI SHALL delegar en la misma implementación de `puriq.core`/`puriq.tools` que usa el MCP_Server, sin duplicar la lógica.
4. WHEN el MCP_Server expone una de estas tools, THE MCP_Server SHALL declarar su esquema de entrada conforme a la firma de la tool subyacente.
5. IF una de estas tools invocada vía MCP lanza un error, THEN THE MCP_Server SHALL devolver al cliente un mensaje de error descriptivo sin exponer valores de secretos.

### Requisito 12: Mutaciones explícitas, validación y protección de secretos (transversal)

**Historia de usuario:** Como usuario avanzado, quiero que las ediciones y eliminaciones sean intencionales, validadas y seguras, para operar mi contenido sin corromper el contrato ni exponer secretos.

#### Criterios de aceptación

1. WHEN una tool de gestión de contenido produce o transforma un documento del contrato, THE Puriq SHALL validar el documento contra su esquema en `schemas/` antes de escribirlo.
2. IF una operación de edición o eliminación indica un `id` que no existe, THEN THE Puriq SHALL rechazar la operación y reportar un error que indique que el elemento no fue encontrado.
3. WHEN una tool de gestión de contenido rechaza una operación por validación, THE Puriq SHALL dejar el contenido persistido sin cambios.
4. THE Puriq SHALL excluir los valores de secretos de los mensajes de error y de la salida producidos por las tools de gestión de contenido, aplicando Redact.
5. WHEN una tool de gestión de contenido genera el `id` o el nombre de archivo de un elemento, THE Puriq SHALL derivarlo con `slugify` para cumplir el patrón `^[a-z0-9-]+$`.
