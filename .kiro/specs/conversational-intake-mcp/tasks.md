# Plan de Implementación: Registro Conversacional por MCP (Hito 1)

## Overview

El plan implementa las **Piezas 1 y 2** del diseño en Python, sobre `agent/puriq/`, de forma
incremental y sin reimplementar lógica existente. Primero se **relocalizan** los helpers de asset/QA
y las constantes (`MAX_ASSET_BYTES`, `redact_value`) a módulos neutrales (DD-3, DD-4) para que
`intake/tools.py` pueda reutilizarlos sin arrastrar FastAPI. Luego se construye el núcleo
`intake/tools.py` (las 12 funciones de intake, `INTAKE_TOOL_SPECS`, `INTAKE_GUION`,
`run_intake_tool`) y, por último, se **integra aditivamente** en `mcp/server.py` (registro de specs,
ruteo por `run_intake_tool` y recurso `intake://guion`), conservando intactas las tools de edición y
de pipeline.

La estrategia de pruebas es **dual**: pruebas de propiedad con **Hypothesis** (mínimo 100 iteraciones,
sobre proyecto temporal `tmp_path`) para las 14 propiedades de correctitud, y pruebas de
ejemplo/integración (smoke de registro MCP, calidad de mensajes de error, integración de `build`).
Cada prueba de propiedad se etiqueta con `# Feature: conversational-intake-mcp, Property {N}: ...`.

## Tasks

- [x] 1. Relocalizar helpers y constantes a módulos neutrales (DD-3, DD-4)
  - [x] 1.1 Mover `MAX_ASSET_BYTES` a `wizard/assets.py` y `redact_value` a `config.py`
    - Mover la constante `MAX_ASSET_BYTES` desde `wizard/server.py` a `wizard/assets.py`, junto a `IMAGE_EXTS`
    - Mover `_redact_value` desde `wizard/server.py` a `config.py` como `redact_value(value)`, variante recursiva de `redact` (única fuente de verdad)
    - _Requirements: 11.3, 14.5_

  - [x] 1.2 Crear `wizard/asset_store.py` (E/S sin FastAPI) con los helpers de asset
    - Mover `next_available_asset(project, name) -> tuple[str, Path]` y `append_image(project, entity_key, entity_id, rel_path) -> dict` desde `wizard/server.py`, preservando su comportamiento
    - _Requirements: 11.1, 11.4, 11.5_

  - [x] 1.3 Crear `wizard/qa_store.py` (E/S sin FastAPI) con los helpers de QA
    - Mover `append_qa_entry(project, entry) -> str` y `register_knowledge_source(project, rel_path) -> dict` desde `wizard/server.py`, preservando su comportamiento
    - _Requirements: 10.3_

  - [x] 1.4 Actualizar `wizard/server.py` para importar los helpers reubicados
    - Reemplazar las definiciones locales por imports de `wizard.asset_store`, `wizard.qa_store`, `MAX_ASSET_BYTES` (de `wizard.assets`) y `redact_value` (de `config`), sin reescribir lógica
    - _Requirements: 1.2_

  - [x]* 1.5 Escribir pruebas de paridad de comportamiento para los helpers reubicados
    - Verificar que `asset_store`/`qa_store` y los endpoints del wizard mantienen el mismo comportamiento tras la reubicación
    - _Requirements: 1.2_

- [x] 2. Andamiaje del paquete `intake` y helpers internos compartidos
  - [x] 2.1 Crear `intake/__init__.py` e `intake/tools.py` con constantes y helpers base
    - Crear `intake/__init__.py` reexportando `INTAKE_TOOL_SPECS`, `INTAKE_TOOL_NAMES`, `INTAKE_GUION`, `run_intake_tool`
    - En `intake/tools.py`: definir constantes (`_TOURISM`, `_CONFIG`, `_THEME`, `_DEFAULT_BRAND_COLORS`) e implementar `_save(project, doc, patch)` (load→merge→save) y `_state_response(merged)` (envuelve con `config.redact_value`)
    - Importar solo de los cimientos (`contracts`, `config`), nunca de `wizard/server.py`
    - _Requirements: 1.1, 1.3, 1.4_

- [x] 3. Implementar intake tools de identidad, estructura y contenido
  - [x] 3.1 Implementar `set_site`, `configure_modules` y `configure_landing`
    - `set_site`: construir centro con `make_coords`, validar dominio con `validate_domain`, escribir `tourism-data.site` y (si aplica) `site-config.deploy.domain`/`site-config.contact`
    - `configure_modules`/`configure_landing`: delegar en `build_modules`/`build_landing` (order ≥ 1 según orden recibido) y persistir en `site-config`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 9.1, 9.2, 9.3, 9.4_

  - [x] 3.2 Implementar `add_place` y `add_event`
    - Delegar en `build_place`/`build_event` (id = `slugify(name)`); anexar por id vía `merge_document` sin borrar existentes
    - `add_place` con solo `address` persiste como borrador (sin inventar `coords`); coordenada única o fuera de rango → `CoordinateRangeError`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 6.1, 6.2, 6.3, 6.4_

  - [x]* 3.3 Escribir prueba de propiedad para el `order` de módulos y secciones
    - **Property 9: El `order` asignado es 1..n coherente con el orden de la selección**
    - **Validates: Requirements 4.2, 9.2**

  - [x]* 3.4 Escribir prueba de propiedad para el patrón slug de ids y nombres de archivo
    - **Property 7: Los id y nombres de archivo generados cumplen el patrón slug**
    - **Validates: Requirements 5.1, 6.1, 11.1, 14.6**

  - [x]* 3.5 Escribir prueba de propiedad para la aditividad de las colecciones
    - **Property 2: Agregar preserva las entradas preexistentes (aditividad)**
    - **Validates: Requirements 5.6, 6.3, 10.3**

  - [x]* 3.6 Escribir prueba de propiedad para el lugar-borrador con solo dirección
    - **Property 3: Un lugar con solo dirección se persiste como borrador sin inventar coordenadas**
    - **Validates: Requirements 1.5, 5.3**

- [x] 4. Checkpoint - Asegurar que las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implementar edición, marca y Q&A
  - [x] 5.1 Implementar `edit_item` y `remove_item`
    - Delegar en `Puriq(project).edit(id, fields)` y `Puriq(project).delete(id)`; id inexistente → `ValueError` "no encontrado"
    - `remove_item` hereda la integridad referencial de `delete_content` y devuelve `{id, affectedEvents}` junto al estado
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 5.2 Implementar `set_brand` y `add_qa`
    - `set_brand`: armar parche `theme-tokens` solo con lo provisto (`colors`/`typography`/`voice`) y guardar con `save_contract` (validación hex del esquema)
    - `add_qa`: validar con `validate_qa_entry`, anexar con `qa_store.append_qa_entry` y registrar `knowledgeSource` con `qa_store.register_knowledge_source`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 10.1, 10.2, 10.3, 10.4_

  - [x]* 5.3 Escribir prueba de propiedad para la edición parcial
    - **Property 10: `edit_item` cambia solo los campos indicados y preserva el resto**
    - **Validates: Requirements 7.1**

  - [x]* 5.4 Escribir prueba de propiedad para la integridad referencial al eliminar
    - **Property 12: Eliminar un lugar referenciado no deja referencias colgantes**
    - **Validates: Requirements 7.4**

- [x] 6. Implementar el adjunto de imágenes (`attach_asset`)
  - [x] 6.1 Implementar `attach_asset` (DD-6)
    - Obtener bytes de `content_base64` (decodificando) o `source_path`; comprobar `len(bytes) <= MAX_ASSET_BYTES` antes de escribir
    - Normalizar con `normalize_asset_name`, desambiguar con `asset_store.next_available_asset`, verificar contención con `resolve_within_assets`; escribir y asociar con `asset_store.append_image` (id inexistente → `ValueError` "no encontrado")
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

  - [x]* 6.2 Escribir prueba de propiedad para la asociación de imágenes
    - **Property 13: Asociar una imagen es aditivo e idempotente**
    - **Validates: Requirements 11.5**

  - [x]* 6.3 Escribir prueba de propiedad para operar sobre un id inexistente
    - **Property 11: Operar sobre un id inexistente se rechaza como "no encontrado"** (`edit_item`, `remove_item`, `attach_asset`)
    - **Validates: Requirements 7.3, 11.6**

- [x] 7. Implementar el estado del contrato (`get_state`)
  - [x] 7.1 Implementar `get_state` (DD-7)
    - Cargar los tres docs con `_load_contract` sin mutar; computar `missing` comparando contra `contracts._base_document` (site name/region/center, modules vacío, places vacío, colores por defecto); devolver todo con `config.redact_value`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x]* 7.2 Escribir prueba de propiedad para el solo-lectura de `get_state`
    - **Property 4: `get_state` es de solo lectura**
    - **Validates: Requirements 2.1**

  - [x]* 7.3 Escribir prueba de propiedad para el cómputo de `missing`
    - **Property 5: `missing` refleja exactamente las piezas requeridas ausentes**
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6, 2.7**

- [x] 8. Implementar la construcción del sitio (`build`)
  - [x] 8.1 Implementar `build`
    - Delegar en `Puriq(project).build(use_llm=use_llm)`; devolver `{"dist": str(path)}`; propagar errores de pipeline para su traducción posterior
    - _Requirements: 12.1, 12.2, 12.3_

- [x] 9. Checkpoint - Asegurar que las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Declarar specs, guion y despacho compartido
  - [x] 10.1 Definir `INTAKE_TOOL_SPECS`, `INTAKE_TOOL_NAMES` e `INTAKE_GUION`
    - Declarar cada tool con `name`, `description` (incluyendo el guion por fases), `inputSchema` (JSON Schema puro con `project` requerido y `additionalProperties: false`) y `handler` (adaptador `arguments → función`)
    - Definir `INTAKE_GUION` con el texto del guion (fases 1–9 y la regla de "pedir archivos activamente")
    - _Requirements: 1.1, 13.2, 13.4_

  - [x] 10.2 Implementar `run_intake_tool(name, arguments)` (DD-5)
    - Localizar el handler, ejecutarlo y traducir excepciones con `errors.wizard_error_response(exc, documento=<doc afectado>)` (ya redactado y accionable)
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

  - [x]* 10.3 Escribir prueba de propiedad para "una escritura exitosa devuelve el estado persistido"
    - **Property 1: Una escritura exitosa devuelve el estado persistido**
    - **Validates: Requirements 1.4, 3.1, 3.7, 4.5, 6.7, 8.5, 9.4**

  - [x]* 10.4 Escribir prueba de propiedad para "una operación rechazada no cambia el contrato"
    - **Property 8: Una operación rechazada deja el contrato persistido sin cambios**
    - **Validates: Requirements 3.4, 3.6, 4.3, 4.4, 5.4, 5.5, 8.2, 9.3, 10.2, 11.2, 11.3, 11.4, 11.6, 14.2, 14.3**

  - [x]* 10.5 Escribir prueba de propiedad para la protección de secretos en la salida
    - **Property 6: Ningún valor de secreto aparece en la salida de una tool**
    - **Validates: Requirements 2.8, 14.5**

  - [x]* 10.6 Escribir prueba de propiedad para la traducción de errores
    - **Property 14: Todo error se traduce a una respuesta accionable**
    - **Validates: Requirements 14.4**

- [x] 11. Exponer las intake tools por MCP (Pieza 2, integración aditiva)
  - [x] 11.1 Registrar las intake specs y el ruteo en `mcp/server.py`
    - Importar `INTAKE_TOOL_SPECS`, `INTAKE_TOOL_NAMES`, `INTAKE_GUION`, `run_intake_tool`; componer `TOOL_SPECS = [*_EXISTING_SPECS, *INTAKE_TOOL_SPECS]` (existentes primero)
    - En `_call_tool`: si `name in INTAKE_TOOL_NAMES`, ejecutar `run_intake_tool` y serializar con `_serialize`; conservar el camino y el `redact` de las tools existentes como red de seguridad
    - _Requirements: 13.1, 13.2, 13.3, 13.6_

  - [x] 11.2 Registrar el recurso MCP `intake://guion`
    - Añadir `@server.list_resources()`/`@server.read_resource()` que exponen el recurso `intake://guion` (mimeType `text/markdown`) con contenido `INTAKE_GUION`, manteniendo el import diferido del SDK `mcp`
    - _Requirements: 13.4, 13.5_

  - [x]* 11.3 Escribir pruebas smoke del registro MCP
    - `list_tools` incluye las 12 intake tools y conserva las 11 existentes; cada `inputSchema` es objeto con `project`; las descripciones contienen el guion; `read_resource("intake://guion")` devuelve `INTAKE_GUION`; una intake tool enruta por `run_intake_tool`
    - _Requirements: 13.1, 13.2, 13.4, 13.5, 13.6_

- [x] 12. Pruebas de ejemplo e integración complementarias
  - [x]* 12.1 Escribir pruebas de ejemplo (unit) para delegación y calidad de mensajes
    - Delegación a constructores puros (1.2, 1.3), `set_brand` escribe/lee colores (8.1), mensajes de error específicos (rango, formato, campo, "no encontrado") y fallo de `build` con mensaje accionable
    - _Requirements: 1.2, 1.3, 8.1, 12.3_

  - [x]* 12.2 Escribir prueba de integración de `build`
    - Sobre un contrato completo en proyecto temporal, `build` delega en `Puriq.build` y devuelve la ruta `dist/`
    - _Requirements: 12.1, 12.2_

- [x] 13. Checkpoint final - Asegurar que todas las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Las tareas marcadas con `*` son opcionales (pruebas) y pueden omitirse para un MVP más rápido.
- Cada tarea referencia requisitos específicos para trazabilidad; las pruebas de propiedad referencian su propiedad del diseño.
- Las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones sobre un proyecto temporal (`tmp_path`), y cada una lleva el comentario `# Feature: conversational-intake-mcp, Property {N}: ...`.
- La integración por MCP es **aditiva**: no altera el motor `list_tools`/`call_tool` ni desregistra las tools de edición y de pipeline.
- Los checkpoints aseguran validación incremental en cortes razonables.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "2.1"] },
    { "id": 2, "tasks": ["1.5", "3.1"] },
    { "id": 3, "tasks": ["3.2"] },
    { "id": 4, "tasks": ["5.1"] },
    { "id": 5, "tasks": ["5.2"] },
    { "id": 6, "tasks": ["6.1"] },
    { "id": 7, "tasks": ["7.1"] },
    { "id": 8, "tasks": ["8.1"] },
    { "id": 9, "tasks": ["10.1"] },
    { "id": 10, "tasks": ["10.2"] },
    { "id": 11, "tasks": ["11.1", "3.3", "3.4", "3.5", "3.6", "5.3", "5.4", "6.2", "6.3", "7.2", "7.3", "10.3", "10.4", "10.5", "10.6"] },
    { "id": 12, "tasks": ["11.2"] },
    { "id": 13, "tasks": ["11.3", "12.1", "12.2"] }
  ]
}
```
