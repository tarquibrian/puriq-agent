# Plan de Implementación: agent-tools

## Overview

Este plan convierte el diseño aprobado en pasos de codificación incrementales sobre `agent/puriq/`. El objetivo es reemplazar los placeholders de las tools por lógica real, respetando las invariantes de arquitectura (el agente compone módulos, no los genera; el LLM solo toca contenido; el contrato son 3 JSON validados contra `schemas/`).

Se empieza por los fundamentos transversales y el ajuste del pipeline (DD-1) en `core.py`, y luego se implementa una tool por vez, cada una cableada al core/MCP a medida que avanza, sin código huérfano. Todas las tareas se limitan a cambios de código dentro de `agent/`.

Lenguaje de implementación: **Python** (definido en el diseño). Pruebas de propiedad con **Hypothesis** (mínimo 100 iteraciones por propiedad); pruebas de ejemplo/integración con mocks de `boto3`/HTTP.

Convención: las subtareas marcadas con `*` (tests) son opcionales y pueden omitirse para un MVP más rápido.

## Tasks

- [x] 1. Fundamentos transversales de configuración y utilidades
  - [x] 1.1 Crear `agent/puriq/config.py` con acceso seguro al entorno
    - Implementar `get_env(name, *, required=False, secret=False)` que lea variables de `agent/.env`; si `required` y falta, error que nombra la variable
    - Implementar `redact(text)` que enmascare valores de secretos conocidos (credenciales AWS, etc.) en textos de salida/error
    - _Requirements: 9.2, 9.3, 9.5_
  - [x] 1.2 Extraer `slugify` a un módulo utilitario compartido (DD-2)
    - Mover `slugify` desde `scan_resources.py` a `agent/puriq/tools/_slug.py` (NFKD → ASCII → kebab-case, patrón `^[a-z0-9-]+$`)
    - Actualizar `scan_resources.py` para importarlo, sin cambiar su comportamiento
    - _Requirements: 1.6, 2.5_
  - [x] 1.3 Agregar helper de comprobación de coords accionable en el pipeline
    - Añadir una función (p. ej. en `schemas.py` o `core.py`) que recorra los Places sin `coords` y construya un error que nombre cada Place ("Falta ubicación en 'X': agregá dirección o coordenadas"), a invocar antes de `schemas.validate`
    - _Requirements: 4.7, 9.4_
  - [x]* 1.4 Escribir prueba unitaria para `config.py`
    - Verificar `get_env` con variable presente, ausente requerida, y que `redact` no filtra secretos
    - _Requirements: 9.2, 9.3, 9.5_

- [x] 2. Ajustar el pipeline del core según DD-1 (geocode antes de validar en ambos puntos)
  - [x] 2.1 Reordenar `Puriq.collect()` en `core.py`
    - Nuevo orden: `scan → enrich (import_open_data) → geocode.fill_missing_coords → comprobación coords accionable → schemas.validate → persistir`
    - El `tourism-data.json` persistido queda siempre válido (con coords)
    - _Requirements: 1.9, 4.1, 9.4_
  - [x] 2.2 Hacer tolerante la carga del `tourism-data.json` en `Puriq.build()`
    - Cargar el JSON sin validación estricta previa (parseo tolerante, no `schemas.load`)
    - Nuevo orden: `carga tolerante → geocode.fill_missing_coords → comprobación coords accionable → schemas.validate estricto → generate_content.enrich → build_site.assemble`
    - Mantener intacta la forma pública del core (`collect/build/preview/deploy`)
    - _Requirements: 4.1, 4.7, 9.4_
  - [x]* 2.3 Prueba de propiedad: coords garantizadas tras geocode o error accionable
    - **Property 22: Coords garantizadas tras geocode o error accionable que nombra el Place**
    - **Validates: Requirements 1.9, 4.1, 4.7, 9.4**
  - [x]* 2.4 Prueba de propiedad: el contrato se valida después de geocode y antes de escribir/construir
    - **Property 19: El contrato se valida después de geocode y antes de escribirse o construirse**
    - **Validates: Requirements 2.9, 3.11, 4.8, 5.10, 9.4**

- [x] 3. Checkpoint - Asegurar que las pruebas pasan
  - Ejecutar la suite y confirmar que el pipeline reordenado funciona con las tools placeholder; consultar al usuario si surgen dudas.

- [x] 4. Robustecer `scan_resources` (Req 1)
  - [x] 4.1 Endurecer lectura y normalización de recursos
    - Exigir `site.json` y `places.csv`; error con archivo faltante y ruta consultada
    - `events.csv` opcional: presente → eventos incluidos; ausente → `events = []`
    - Generar `id` con `slugify(name)` para Places y Events; omitir filas con `name` vacío o solo espacios
    - `lat`/`lng` numéricos → `coords` con floats; ausentes → sin `coords`
    - Envolver `lat`/`lng` no numéricos en un error que identifique fila (índice) y columna
    - Asignar `event.placeId` solo si referencia un `id` de Place existente
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11_
  - [x]* 4.2 Prueba de propiedad: ids son slugs bien formados
    - **Property 1: Los ids son slugs bien formados derivados del nombre**
    - **Validates: Requirements 1.6**
  - [x]* 4.3 Prueba de propiedad: solo sobreviven filas con nombre no vacío
    - **Property 2: Solo sobreviven filas con nombre no vacío**
    - **Validates: Requirements 1.7**
  - [x]* 4.4 Prueba de propiedad: coords del CSV preservadas/ausentes
    - **Property 3: Las coordenadas del CSV se preservan y son numéricas; su ausencia se respeta**
    - **Validates: Requirements 1.8, 1.9**
  - [x]* 4.5 Prueba de propiedad: integridad referencial de eventos
    - **Property 4: Integridad referencial de eventos**
    - **Validates: Requirements 1.10**
  - [x]* 4.6 Prueba de propiedad: eventos incluidos o vacíos según events.csv
    - **Property 5: Los eventos se incluyen o quedan vacíos según exista events.csv**
    - **Validates: Requirements 1.4, 1.5**
  - [x] 4.7 Pruebas unitarias de condiciones de error de scan
    - Archivos faltantes (`site.json`, `places.csv`) y valor no numérico en `lat`/`lng`
    - _Requirements: 1.2, 1.3, 1.11_

- [x] 5. Implementar `geocode` real con adaptadores de proveedor (Req 4)
  - [x] 5.1 Definir el protocolo `GeocodeProvider` y la fábrica `get_provider`
    - `AmazonLocationProvider` (preferido, vía `boto3` `location`) y `NominatimProvider` (fallback OSM, vía `httpx`)
    - Selección por configuración (DD-4): Amazon Location si está configurado/disponible; si no, Nominatim
    - _Requirements: 4.5, 4.6_
  - [x] 5.2 Implementar `fill_missing_coords`
    - Place con `address` y sin `coords` → calcular y asignar `coords`; Place con `coords` → preservar; Place sin `address` → sin cambios
    - Garantizar `lat ∈ [-90, 90]` y `lng ∈ [-180, 180]`
    - Dirección irresoluble → dejar sin `coords` y registrar la dirección no resuelta
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.7, 4.8_
  - [x]* 5.3 Prueba de propiedad: geocode solo completa lo faltante (idempotencia)
    - **Property 15: Geocode solo completa lo faltante**
    - **Validates: Requirements 4.2, 4.3**
  - [x]* 5.4 Prueba de propiedad: coordenadas asignadas en rango válido
    - **Property 16: Las coordenadas asignadas están en rango válido**
    - **Validates: Requirements 4.1, 4.4**
  - [x]* 5.5 Pruebas unitarias de selección de proveedor de geocoding
    - Amazon Location configurado vs Nominatim fallback (con mocks)
    - _Requirements: 4.5, 4.6_

- [x] 6. Implementar `import_open_data` (Req 2)
  - [x] 6.1 Aislar las fronteras de red de fuentes abiertas
    - `_query_overpass(center, radius_m)`, `_query_wikidata(center)`, `_image_from_commons(entity)` vía `httpx`
    - _Requirements: 2.1_
  - [x] 6.2 Implementar `merge` con mapeo, deduplicación y marcado
    - Mapear POI OSM → Place `source="osm"`; Wikidata → `source="wikidata"`
    - Adjuntar URL de imagen de Wikimedia Commons con licencia libre a `images`
    - Generar `id` slug único (desambiguar con sufijo); deduplicar por nombre + proximidad conservando el existente
    - Ante fallo/timeout de una fuente, devolver `data` sin cambios y registrar la causa (DD-3)
    - Garantizar salida conforme a `tourism-data.schema.json`
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9_
  - [x]* 6.3 Prueba de propiedad: procedencia de los datos importados
    - **Property 6: Procedencia de los datos importados**
    - **Validates: Requirements 2.2, 2.3, 2.7**
  - [x]* 6.4 Prueba de propiedad: unicidad de ids tras la importación
    - **Property 7: Unicidad de ids tras la importación**
    - **Validates: Requirements 2.5**
  - [x]* 6.5 Prueba de propiedad: la importación no duplica y preserva lo existente
    - **Property 8: La importación no duplica y preserva lo existente**
    - **Validates: Requirements 2.6**
  - [x]* 6.6 Prueba de propiedad: un fallo de fuente externa preserva el documento
    - **Property 9: Un fallo de fuente externa preserva el documento**
    - **Validates: Requirements 2.8**
  - [x]* 6.7 Prueba de integración de consulta a Overpass/Wikidata
    - 1-3 ejemplos con respuestas HTTP mock
    - _Requirements: 2.1_

- [x] 7. Implementar `generate_content` con proveedores de LLM (Req 3)
  - [x] 7.1 Definir el protocolo `LLMProvider` y la fábrica `get_provider`
    - `BedrockProvider` (vía `boto3` `bedrock-runtime`, modelo `PURIQ_BEDROCK_MODEL`) y `OllamaProvider` (fallback local)
    - Selección por `PURIQ_LLM_MODE` (DD-4)
    - _Requirements: 3.8, 3.9_
  - [x] 7.2 Implementar `enrich` sobre contenido faltante
    - Place/Event con `description` vacía → generar; `description` no vacía → conservar
    - Prompt incluye `voice.tone` y refleja `voice.formality` cuando está definida
    - `site.locales` con más de un Locale → generar traducciones para cada Locale distinto de `defaultLocale`
    - Metadatos SEO basados en `name`, `region` y `description`
    - Fallo del LLM por ítem → conservar valor y continuar (DD-3); salida conforme al esquema
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.10, 3.11_
  - [x]* 7.3 Prueba de propiedad: completitud de descripciones tras la generación
    - **Property 10: Completitud de descripciones tras la generación**
    - **Validates: Requirements 3.1, 3.2**
  - [x]* 7.4 Prueba de propiedad: preservación del contenido existente
    - **Property 11: Preservación del contenido existente**
    - **Validates: Requirements 3.3**
  - [x]* 7.5 Prueba de propiedad: el prompt refleja la voz de marca
    - **Property 12: El prompt refleja la voz de marca**
    - **Validates: Requirements 3.4, 3.5**
  - [x]* 7.6 Prueba de propiedad: traducciones por locale configurado
    - **Property 13: Traducciones por locale configurado**
    - **Validates: Requirements 3.6**
  - [x]* 7.7 Prueba de propiedad: robustez ante fallo del LLM por ítem
    - **Property 14: Robustez ante fallo del LLM por ítem**
    - **Validates: Requirements 3.10**
  - [x]* 7.8 Pruebas unitarias de selección de proveedor de LLM
    - `PURIQ_LLM_MODE=local` → Ollama; `bedrock` → Bedrock con `PURIQ_BEDROCK_MODEL` (con mocks)
    - _Requirements: 3.8, 3.9_

- [x] 8. Checkpoint - Asegurar que las pruebas pasan
  - Ejecutar la suite de la fase de datos (scan, geocode, import, generate); consultar al usuario si surgen dudas.

- [x] 9. Implementar `build_site.assemble` (Req 5)
  - [x] 9.1 Preparar directorio de trabajo y escribir el contrato
    - Copiar la Template excluyendo `node_modules` y `dist`; escribir los 3 documentos en `src/data/`
    - Validar los 3 documentos contra sus esquemas antes del build
    - _Requirements: 5.1, 5.2, 5.10_
  - [x] 9.2 Resolver módulos y materializar la marca
    - Activar módulos `enabled=true`, desactivar `enabled=false`, disponer según `order` (como datos/flags que la Template lee, no como edición de código)
    - Traducir `colors` y `typography` de `Theme_Tokens` a variables CSS en un archivo de tokens
    - _Requirements: 5.3, 5.4, 5.5, 5.6_
  - [x] 9.3 Ejecutar el build de Astro y devolver la ruta
    - Ejecutar `npm run build` vía `subprocess`; éxito → devolver `dist/`; error → reportar con la salida relevante del proceso
    - _Requirements: 5.7, 5.8, 5.9_
  - [x]* 9.4 Prueba de propiedad: resolución de módulos habilitados y ordenados
    - **Property 17: Resolución de módulos = subconjunto habilitado y ordenado**
    - **Validates: Requirements 5.3, 5.4, 5.5**
  - [x]* 9.5 Prueba de propiedad: tokens de marca como variables CSS
    - **Property 18: Los tokens de marca se materializan como variables CSS**
    - **Validates: Requirements 5.6**
  - [x]* 9.6 Prueba de integración del build de Astro
    - 1-2 ejemplos: build exitoso deja `dist/`; build con error reporta salida
    - _Requirements: 5.7, 5.8, 5.9_

- [x] 10. Implementar `build_site.serve` para previsualización (Req 6)
  - [x] 10.1 Servir `dist/` con manejo de puerto y ausencia de build
    - `dist/` existente → servir en el puerto indicado; ausente → error indicando ejecutar `puriq build` primero; sin puerto → 4322
    - _Requirements: 6.1, 6.2, 6.3_
  - [x]* 10.2 Pruebas unitarias de preview
    - Puerto por defecto y error cuando falta `dist/`
    - _Requirements: 6.2, 6.3_

- [x] 11. Implementar `deploy` con adaptadores por destino (Req 7)
  - [x] 11.1 Definir el protocolo `DeployAdapter` y el registro `ADAPTERS`
    - Validar destino soportado (`aws-amplify`, `s3-cloudfront`, `static-export`, `vercel`, `netlify`); destino inválido → error listando válidos; `dist/` ausente → error de build previo
    - _Requirements: 7.1, 7.2, 7.3_
  - [x] 11.2 Implementar adaptadores AWS y export estático
    - `aws-amplify` → publicar vía `boto3` y devolver URL; `s3-cloudfront` → subir a S3, invalidar CloudFront vía `boto3`, devolver URL; `static-export` → devolver ruta local
    - Rechazo del proveedor o credenciales faltantes → error con la causa sin exponer secretos (usar `redact`)
    - _Requirements: 7.4, 7.5, 7.6, 7.7_
  - [x]* 11.3 Prueba de propiedad: deploy rechaza destinos no soportados
    - **Property 20: Deploy rechaza destinos no soportados**
    - **Validates: Requirements 7.2**
  - [x]* 11.4 Prueba de integración de adaptadores AWS y export estático
    - 1-3 ejemplos con mocks de `boto3`; incluir `static-export`
    - _Requirements: 7.1, 7.4, 7.5, 7.6_

- [x] 12. Exponer las tools vía MCP (Req 8)
  - [x] 12.1 Registrar y cablear las tools en `mcp/server.py`
    - Registrar `scan_resources`, `import_open_data`, `generate_content`, `build_site`, `deploy`, delegando en `puriq.core`/`puriq.tools` sin duplicar lógica
    - Declarar el esquema de entrada de cada tool acorde a su firma
    - Error de una tool → mensaje descriptivo al cliente sin exponer secretos (usar `redact`)
    - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - [x]* 12.2 Pruebas unitarias del registro y esquemas MCP
    - Verificar tools registradas, delegación al core y declaración de esquemas
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 13. Manejo de errores transversal en el CLI (Req 9)
  - [x] 13.1 Capturar excepciones de tool en `cli.py` y presentarlas con `rich`
    - Mensaje descriptivo (causa + acción sugerida) ante errores de tool; enmascarar secretos con `redact`; error que nombra la variable de entorno requerida ausente
    - _Requirements: 9.1, 9.3, 9.5_
  - [x]* 13.2 Prueba de propiedad: no exposición de secretos
    - **Property 21: No exposición de secretos**
    - **Validates: Requirements 7.7, 8.4, 9.3**
  - [x]* 13.3 Pruebas unitarias de mensajes de error del CLI
    - Error de tool traducido a mensaje descriptivo; variable de entorno faltante nombrada
    - _Requirements: 9.1, 9.5_

- [x] 14. Checkpoint final - Asegurar que todas las pruebas pasan
  - Ejecutar la suite completa (unit + property + integración con mocks) y confirmar la cobertura de todos los requisitos; consultar al usuario si surgen dudas.

## Notas

- Las subtareas marcadas con `*` (tests) son opcionales y pueden omitirse para un MVP más rápido; las tareas de implementación no marcadas son obligatorias.
- Cada tarea referencia los requisitos que cubre para trazabilidad; en conjunto cubren los 9 requisitos del documento aprobado.
- Las pruebas de propiedad validan las propiedades universales del diseño (Hypothesis, mínimo 100 iteraciones, etiquetadas con `# Feature: agent-tools, Property {n}: {texto}`); las de ejemplo/integración cubren selección de proveedor, condiciones de error y fronteras externas con mocks.
- El ajuste del pipeline DD-1 (Tarea 2) se hace temprano para que el contrato persistido y el reconstruido siempre pasen por `geocode` antes de la validación estricta.
