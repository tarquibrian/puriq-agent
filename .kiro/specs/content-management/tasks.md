# Plan de Implementación: content-management

## Overview

Este plan convierte el diseño aprobado en pasos de codificación incrementales sobre `agent/puriq/`. Añade seis tools de gestión de contenido (`manage_articles`, `query_content`, `edit_content`, `delete_content`, `bulk_update`, `analyze_seo`) como una **capa fina** sobre `puriq.core`/`puriq.tools`, respetando las invariantes de arquitectura: el agente compone y configura (no genera código de módulos); el LLM solo redacta contenido; toda mutación del contrato se valida contra `schemas/` **antes** de escribir, de forma atómica; ids/nombres de archivo se derivan con `slugify`; los secretos se enmascaran con `config.redact`; y `analyze_seo` es de solo lectura.

El orden sigue las dependencias: primero la **lógica pura y sus helpers compartidos** (Article_Schema, `FrontmatterCodec`, merge de campos DD-5, escritura atómica validada DD-6, `ArticleStore` DD-1) con sus pruebas de propiedad, luego cada tool que los consume, después el cableado en `puriq.core.Puriq`, y por último la exposición vía MCP y CLI sin duplicar lógica. Todas las tareas se limitan a cambios de código dentro de `agent/` y `schemas/`.

Lenguaje de implementación: **Python** (definido en el diseño y en el código existente del agente). Pruebas de propiedad con **Hypothesis** (mínimo 100 iteraciones por propiedad, etiquetadas `# Feature: content-management, Property {n}: {texto}`); pruebas de ejemplo/integración con mocks (LLM_Provider, sin red).

Convención: las subtareas marcadas con `*` (tests) son opcionales y pueden omitirse para un MVP más rápido; las tareas de implementación no marcadas son obligatorias.

## Tasks

- [x] 1. Definir y registrar el Article_Schema (Req 1)
  - [x] 1.1 Crear `schemas/article.schema.json`
    - Requeridos: `id` (`pattern: ^[a-z0-9-]+$`), `title` (`minLength: 1`), `date` (`format: date`, `YYYY-MM-DD`); opcionales: `tags` (array de strings), `category` (string), `summary` (string); `additionalProperties: false`
    - Seguir el mismo patrón de los tres esquemas existentes en `schemas/`
    - _Requirements: 1.1, 1.2, 1.3_
  - [x] 1.2 Registrar `"article"` en `puriq.schemas`
    - Añadir la entrada `"article": "article.schema.json"` a `_FILES` en `agent/puriq/schemas.py` para que `validate/load` funcionen contra el nuevo esquema
    - _Requirements: 1.5, 12.1_
  - [ ]* 1.3 Prueba unitaria de carga/validación del Article_Schema
    - Frontmatter válido pasa; `id` con patrón inválido, `date` mal formada y campo desconocido fallan
    - _Requirements: 1.5, 12.1_

- [x] 2. Implementar el `FrontmatterCodec` (tools/_frontmatter.py, DD-2)
  - [x] 2.1 Implementar `parse` y `serialize`
    - `parse(text) -> (frontmatter: dict, body: str)`: bloque delimitado por `---`, claves escalares (`id`, `title`, `date`, `category`, `summary`) y listas simples (`tags`)
    - `serialize(frontmatter, body) -> str`: escribe el bloque `---` al inicio seguido del cuerpo markdown
    - Sin dependencias nuevas (parser mínimo, no YAML arbitrario)
    - _Requirements: 1.1_
  - [ ]* 2.2 Prueba de propiedad: round-trip del codec de frontmatter
    - **Property 1: Round-trip del codec de frontmatter**
    - **Validates: Requirements 1.1**
  - [ ]* 2.3 Prueba unitaria: frontmatter inválido nombra archivo y campo
    - Un bloque no parseable o que no cumple `article.schema.json` reporta el archivo y el campo que incumple
    - _Requirements: 1.6_

- [x] 3. Implementar helpers compartidos de mutación (DD-5, DD-6)
  - [x] 3.1 Implementar el helper de merge a nivel de campo (tools/_merge.py)
    - `merge_fields(target: dict, fields: dict) -> dict`: sobrescribe solo los campos presentes en `fields`, preserva el resto; nunca regenera el `id`
    - Semántica compartida por `edit_article`, `edit_content` y `bulk_update`
    - _Requirements: 4.1, 4.3, 7.1, 7.2, 9.2_
  - [x] 3.2 Implementar el helper de escritura atómica validada (tools/_persist.py)
    - `validate_then_write(doc, schema_name, path, serialize)`: valida `doc` contra su esquema con `puriq.schemas` **antes** de tocar disco; si falla, no escribe nada y reporta el campo que incumple; en éxito escribe a temporal + `os.replace` (atómico)
    - _Requirements: 12.1, 12.3_
  - [ ]* 3.3 Prueba de propiedad: la edición preserva los campos no indicados
    - **Property 8: La edición preserva los campos no indicados (merge de campos)**
    - **Validates: Requirements 4.1, 4.3, 7.1, 7.2**
  - [ ]* 3.4 Prueba de propiedad: validación antes de escribir y no-cambios ante rechazo
    - **Property 16: Validación antes de escribir y no-cambios ante rechazo**
    - **Validates: Requirements 1.5, 4.4, 7.4, 7.5, 8.6, 9.6, 9.7, 12.1, 12.3**

- [x] 4. Implementar el `ArticleStore` escaneando `/content` (tools/_article_store.py, DD-1)
  - [x] 4.1 Implementar `read_all`, `read`, `write`, `delete`
    - `read_all(content_dir) -> list[Article]`: escanea `/content/*.md`, parsea el frontmatter con el codec; deriva la colección sin índice separado
    - `read(content_dir, id)`, `delete(content_dir, id)`: localizan el `.md` por `id`
    - `write(content_dir, article) -> Path`: valida el frontmatter contra `article.schema.json` y persiste con el helper de escritura atómica (task 3.2); frontmatter inválido reporta archivo + campo
    - _Requirements: 1.4, 1.5, 1.6, 3.1, 5.1_
  - [ ]* 4.2 Prueba de propiedad: la colección de artículos se recupera por escaneo
    - **Property 3: La colección de artículos se recupera por escaneo (round-trip del store)**
    - **Validates: Requirements 1.4, 3.1**

- [x] 5. Implementar `manage_articles` CRUD (tools/manage_articles.py, Req 2–5)
  - [x] 5.1 Implementar `create_article`
    - `id = slugify(title)`; título ausente/vacío → error "título obligatorio"; `id` duplicado → error "ya existe" sin sobrescribir; sin `date` → fecha actual
    - Sin `body` → generar con `generate_content.get_provider().complete(prompt)` (DD-3); con `body` → conservarlo sin invocar al LLM
    - Éxito → escribe el `.md` vía `ArticleStore.write` y devuelve `{id, path}`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 12.5_
  - [ ]* 5.2 Prueba de propiedad: id es un slug bien formado derivado del título
    - **Property 2: El id es un slug bien formado derivado del título**
    - **Validates: Requirements 1.2, 2.1, 12.5**
  - [ ]* 5.3 Prueba de propiedad: preservación del cuerpo aportado
    - **Property 4: Preservación del cuerpo aportado**
    - **Validates: Requirements 2.3**
  - [x] 5.4 Implementar `list_articles` (filtros + orden)
    - Sin filtros → todos con su frontmatter; filtros por rango de fechas (inclusive), etiqueta y categoría; múltiples filtros → conjunción; sin coincidencias → lista vacía; orden por `date` descendente
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  - [ ]* 5.5 Prueba de propiedad: los filtros de artículos devuelven el subconjunto correcto
    - **Property 5: Los filtros de artículos devuelven el subconjunto correcto**
    - **Validates: Requirements 3.2, 3.3, 3.4**
  - [ ]* 5.6 Prueba de propiedad: conjunción de filtros de artículos
    - **Property 6: Conjunción de filtros de artículos**
    - **Validates: Requirements 3.5**
  - [ ]* 5.7 Prueba de propiedad: el listado de artículos está ordenado por fecha descendente
    - **Property 7: El listado de artículos está ordenado por fecha descendente**
    - **Validates: Requirements 3.7**
  - [x] 5.8 Implementar `edit_article`
    - Merge de solo los campos indicados (helper task 3.1), preserva el resto; editar `title` no regenera el `id`; valida el frontmatter resultante antes de escribir; vaciar un campo obligatorio → rechazo que nombra el campo; éxito → devuelve `{id}`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  - [x] 5.9 Implementar `delete_article`
    - Borra el `.md` correspondiente vía `ArticleStore.delete`; `id` inexistente → error "no encontrado"; éxito → devuelve `{id}`
    - _Requirements: 5.1, 5.2, 5.3_
  - [ ]* 5.10 Pruebas de ejemplo/integración de `manage_articles`
    - Fecha por defecto sin `date` (2.4); `id` duplicado (2.5); título vacío (2.6); retorno `id`+ruta (2.7); creación asistida por LLM con `provider` mockeado (2.2); `id` inexistente en edición/eliminación (4.2, 5.2); edición que vacía un campo obligatorio (4.5); listado sin coincidencias → lista vacía (3.6)
    - _Requirements: 2.2, 2.4, 2.5, 2.6, 2.7, 3.6, 4.2, 4.5, 4.6, 5.2, 5.3_

- [x] 6. Checkpoint - Asegurar que las pruebas pasan
  - Ejecutar la suite de artículos (schema, codec, helpers, store, manage_articles); consultar al usuario si surgen dudas.

- [x] 7. Implementar `query_content` (tools/query_content.py, Req 6)
  - [x] 7.1 Implementar `query` (solo lectura)
    - `kind` places/events; sin filtros → todos del tipo; filtro por categoría (Places), etiqueta (Places), búsqueda por nombre sin distinguir mayúsculas/minúsculas, rango de fechas de `startDate` (Events, inclusive); múltiples filtros → conjunción; sin coincidencias → lista vacía; no persiste
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_
  - [ ]* 7.2 Prueba de propiedad: los filtros de Query_Content devuelven el subconjunto correcto
    - **Property 9: Los filtros de Query_Content devuelven el subconjunto correcto**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6**
  - [ ]* 7.3 Prueba de propiedad: conjunción de filtros de Query_Content
    - **Property 10: Conjunción de filtros de Query_Content**
    - **Validates: Requirements 6.7**
  - [ ]* 7.4 Prueba unitaria: consulta sin coincidencias devuelve lista vacía
    - Filtros que no coinciden → `[]` sin error
    - _Requirements: 6.8_

- [x] 8. Implementar `edit_content` (tools/edit_content.py, Req 7)
  - [x] 8.1 Implementar `edit`
    - Localiza Place o Event por `id`; merge de solo los campos indicados (helper task 3.1), preserva el resto; `id` inexistente en Places y Events → error "no encontrado"; valida el `tourism-data` resultante contra `tourism-data.schema.json` antes de persistir (helper task 3.2); campo desconocido/valor inválido → rechazo que identifica el campo, sin escribir; éxito → devuelve el `id` modificado
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_
  - [ ]* 8.2 Pruebas unitarias de condiciones de error de `edit_content`
    - `id` inexistente → "no encontrado" (7.3); edición que produce contrato inválido → rechazo que nombra el campo, contenido sin cambios (7.5)
    - _Requirements: 7.3, 7.5, 7.6_

- [x] 9. Implementar `delete_content` con integridad referencial (tools/delete_content.py, Req 8)
  - [x] 9.1 Implementar `delete`
    - Elimina Place o Event por `id`; `id` inexistente → error "no encontrado"; al eliminar un Place, informa los Events con `placeId` que lo referencian y limpia ese `placeId` para no dejar referencias colgantes; valida el resultado antes de persistir (helper task 3.2); éxito → devuelve `{id, affectedEvents}`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_
  - [ ]* 9.2 Prueba de propiedad: la eliminación quita exactamente el elemento objetivo
    - **Property 11: La eliminación quita exactamente el elemento objetivo**
    - **Validates: Requirements 5.1, 8.1, 8.2**
  - [ ]* 9.3 Prueba de propiedad: integridad referencial al eliminar un Place
    - **Property 12: Integridad referencial al eliminar un Place**
    - **Validates: Requirements 8.4, 8.5**
  - [ ]* 9.4 Prueba unitaria: `id` inexistente en eliminación
    - `id` que no corresponde a Place ni Event → "no encontrado", sin cambios
    - _Requirements: 8.3, 8.7_

- [x] 10. Extraer los helpers CSV y implementar `bulk_update` (Req 9, DD-4)
  - [x] 10.1 Extraer los helpers de parseo CSV a un módulo neutro compartido (tools/_csv.py)
    - Mover `_read_csv`, `_split_tags`, `_parse_coord` (error con fila/columna) y la normalización de fila a Place/Event desde `scan_resources.py` a `tools/_csv.py`
    - Actualizar `scan_resources.py` para importarlos, sin cambiar su comportamiento
    - _Requirements: 9.5, 12.5_
  - [x] 10.2 Implementar `bulk_update`
    - Fila con `id` inexistente → alta; `id` coincidente → merge de solo los campos presentes en la fila (helper task 3.1), misma regla para Places y Events; fila sin `id` ni `name` → se omite y se registra su número; valor tipado inválido (`lat`/`lng`, fecha) → error con fila/columna (helpers task 10.1); valida el resultado antes de persistir (helper task 3.2); éxito → devuelve `{added, updated, skipped, data}`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_
  - [ ]* 10.3 Prueba de propiedad: la fusión CSV agrega los nuevos y actualiza por id preservando lo ausente
    - **Property 13: La fusión CSV agrega los nuevos y actualiza por id preservando lo ausente**
    - **Validates: Requirements 9.1, 9.2, 9.3**
  - [ ]* 10.4 Prueba de propiedad: las filas sin id ni name se omiten y se registran
    - **Property 14: Las filas sin id ni name se omiten y se registran**
    - **Validates: Requirements 9.4**
  - [ ]* 10.5 Prueba de propiedad: el resumen de la fusión cuenta correctamente altas y actualizaciones
    - **Property 15: El resumen de la fusión cuenta correctamente altas y actualizaciones**
    - **Validates: Requirements 9.8**
  - [ ]* 10.6 Prueba unitaria: valor tipado inválido en CSV con fila/columna
    - `lat`/`lng` no numérico o fecha mal formada → error que identifica fila y columna; contrato resultante inválido → no escribe
    - _Requirements: 9.5, 9.7_

- [x] 11. Implementar `analyze_seo` de solo lectura (tools/analyze_seo.py, Req 10)
  - [x] 11.1 Implementar `analyze_seo`
    - Analiza `tourism-data.json`, `/content` y `dist/` locales sin consultar ninguna URL en vivo; detecta falta de meta descripción/resumen, título inadecuado, imágenes sin texto alternativo, jerarquía de encabezados incorrecta y slugs inválidos/demasiado largos; sin problemas → resultado "sin problemas"; nunca muta el contenido; devuelve `{issues, ok}`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_
  - [ ]* 11.2 Prueba de propiedad: las sugerencias corresponden exactamente a los defectos
    - **Property 17: Análisis SEO — las sugerencias corresponden exactamente a los defectos**
    - **Validates: Requirements 10.2, 10.3, 10.4, 10.6**
  - [ ]* 11.3 Prueba de propiedad: detección de jerarquía de encabezados
    - **Property 18: Análisis SEO — detección de jerarquía de encabezados**
    - **Validates: Requirements 10.5**
  - [ ]* 11.4 Prueba de propiedad: el análisis SEO no muta el contenido
    - **Property 19: El análisis SEO no muta el contenido**
    - **Validates: Requirements 10.8**
  - [ ]* 11.5 Pruebas de ejemplo/integración de `analyze_seo`
    - Resultado "sin problemas" cuando no hay defectos (10.7); verificación de que no realiza llamadas de red (10.1, con mock/monkeypatch del cliente HTTP)
    - _Requirements: 10.1, 10.7_

- [x] 12. Checkpoint - Asegurar que las pruebas pasan
  - Ejecutar la suite de las tools de contenido (query, edit, delete, bulk_update, analyze_seo); consultar al usuario si surgen dudas.

- [x] 13. Cablear las seis tools en `puriq.core.Puriq` (Req 11)
  - [x] 13.1 Añadir los métodos de orquestación en `core.py`
    - Añadir `create_article`, `list_articles`, `edit_article`, `delete_article`, `query`, `edit`, `delete`, `bulk_update`, `analyze_seo` que carguen el contrato/Content_Store del `project`, deleguen en las tools y persistan de forma atómica el resultado; punto único compartido por CLI y MCP, sin duplicar lógica
    - _Requirements: 11.2, 11.3_

- [x] 14. Exponer las tools vía MCP y CLI (Req 11)
  - [x] 14.1 Registrar y cablear las tools en `mcp/server.py`
    - Añadir a `TOOL_SPECS` `manage_articles`, `query_content`, `edit_content`, `delete_content`, `bulk_update`, `analyze_seo` con su `inputSchema` acorde a la firma delegada; cada handler delega en los métodos del core (task 13.1); error → mensaje descriptivo enmascarado con `redact`
    - _Requirements: 11.1, 11.2, 11.4, 11.5_
  - [x] 14.2 Añadir los subcomandos del CLI en `cli.py`
    - Añadir `articles`, `query`, `edit`, `delete`, `bulk-update`, `seo` que delegan en los mismos métodos del core (task 13.1), envueltos por `@manejar_errores` (mensajes descriptivos + `redact`)
    - _Requirements: 11.3, 12.4_
  - [ ]* 14.3 Pruebas de ejemplo/integración de exposición y no-fuga de secretos
    - Registro de las 6 tools en MCP (11.1); delegación compartida CLI/MCP sobre el mismo callable del core (11.2, 11.3); `inputSchema` acorde a la firma (11.4)
    - **Property 20: No exposición de secretos**
    - **Validates: Requirements 11.5, 12.4**

- [x] 15. Checkpoint final - Asegurar que todas las pruebas pasan
  - Ejecutar la suite completa (unit + property + integración con mocks) y confirmar la cobertura de los 12 requisitos; consultar al usuario si surgen dudas.

## Notas

- Las subtareas marcadas con `*` (tests) son opcionales y pueden omitirse para un MVP más rápido; las tareas de implementación no marcadas son obligatorias.
- Cada tarea referencia los requisitos que cubre para trazabilidad; en conjunto cubren los 12 requisitos del documento aprobado.
- Las pruebas de propiedad validan las 20 propiedades universales del diseño (Hypothesis, mínimo 100 iteraciones, etiquetadas `# Feature: content-management, Property {n}: {texto}`); las de ejemplo/integración cubren la creación asistida por LLM (proveedor mockeado), condiciones de error, resultados vacíos, wiring MCP/CLI y ausencia de red en `analyze_seo`.
- La lógica pura y los helpers compartidos (Article_Schema, `FrontmatterCodec`, merge de campos DD-5, escritura atómica validada DD-6, `ArticleStore` DD-1) se implementan y prueban **antes** que las tools que los consumen.
- Los archivos compartidos (`puriq/core.py`, `mcp/server.py`, `cli.py`, `schemas.py`, `scan_resources.py`) los toca una única tarea o se secuencian en waves distintas para evitar conflictos de escritura.

## Task Dependency Graph

```mermaid
flowchart LR
    subgraph W0[Wave 0: base pura]
      T11[1.1 article.schema.json]
      T21[2.1 FrontmatterCodec]
      T31[3.1 merge_fields]
      T71[7.1 query]
      T101[10.1 _csv extract]
    end
    subgraph W1[Wave 1]
      T12[1.2 registrar article]
      T22[2.2* P1 round-trip]
      T33[3.3* P8 merge]
      T72[7.2* P9]
      T73[7.3* P10]
      T74[7.4* vacio]
    end
    subgraph W2[Wave 2]
      T32[3.2 escritura atomica]
      T23[2.3* frontmatter invalido]
      T13[1.3* schema test]
    end
    subgraph W3[Wave 3]
      T41[4.1 ArticleStore]
      T81[8.1 edit_content]
      T91[9.1 delete_content]
      T102[10.2 bulk_update]
      T34[3.4* P16]
    end
    subgraph W4[Wave 4]
      T51[5.1 create_article]
      T42[4.2* P3]
      T82[8.2* edit test]
      T92[9.2* P11]
      T93[9.3* P12]
      T94[9.4* delete test]
      T103[10.3* P13]
      T104[10.4* P14]
      T105[10.5* P15]
      T106[10.6* csv test]
      T111[11.1 analyze_seo]
    end
    subgraph W5[Wave 5]
      T54[5.4 list_articles]
      T112[11.2* P17]
      T113[11.3* P18]
      T114[11.4* P19]
      T115[11.5* seo test]
    end
    subgraph W6[Wave 6]
      T58[5.8 edit_article]
    end
    subgraph W7[Wave 7]
      T59[5.9 delete_article]
    end
    subgraph W8[Wave 8]
      T52[5.2* P2]
      T53[5.3* P4]
      T55[5.5* P5]
      T56[5.6* P6]
      T57[5.7* P7]
      T510[5.10* ejemplos]
      T131[13.1 core wiring]
    end
    subgraph W9[Wave 9]
      T141[14.1 MCP]
      T142[14.2 CLI]
    end
    subgraph W10[Wave 10]
      T143[14.3* wiring/P20]
    end
    W0 --> W1 --> W2 --> W3 --> W4 --> W5 --> W6 --> W7 --> W8 --> W9 --> W10
```

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "7.1", "10.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "3.3", "7.2", "7.3", "7.4"] },
    { "id": 2, "tasks": ["3.2", "2.3", "1.3"] },
    { "id": 3, "tasks": ["4.1", "8.1", "9.1", "10.2", "3.4"] },
    { "id": 4, "tasks": ["5.1", "4.2", "8.2", "9.2", "9.3", "9.4", "10.3", "10.4", "10.5", "10.6", "11.1"] },
    { "id": 5, "tasks": ["5.4", "11.2", "11.3", "11.4", "11.5"] },
    { "id": 6, "tasks": ["5.8"] },
    { "id": 7, "tasks": ["5.9"] },
    { "id": 8, "tasks": ["5.2", "5.3", "5.5", "5.6", "5.7", "5.10", "13.1"] },
    { "id": 9, "tasks": ["14.1", "14.2"] },
    { "id": 10, "tasks": ["14.3"] }
  ]
}
```
