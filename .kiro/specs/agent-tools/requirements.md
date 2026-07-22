# Documento de Requisitos

## Introducción

Puriq es un agente en Python que convierte los recursos turísticos dispersos de un gobierno local (lugares, eventos, fotos, logo, Q&A) en un sitio web estático (Astro), profesional y mantenible. El agente **compone y configura módulos pre-construidos**; nunca escribe el código de esos módulos. El contrato del sistema son tres documentos JSON validados contra los esquemas de `schemas/` en cada operación: `tourism-data.json` (contenido), `site.config.json` (estructura/módulos) y `theme.tokens.json` (marca).

El esqueleto del agente, el pipeline (`agent/puriq/core.py`) y los contratos ya existen. Este spec cubre la **implementación completa de la lógica real de las tools del agente**, que hoy están en su mayoría como placeholders (`scan_resources` ya está implementado y solo requiere robustez). Las tools cubiertas son: `scan_resources`, `import_open_data`, `generate_content`, `geocode`, `build_site`, `deploy`, más la exposición vía MCP y las consideraciones transversales (errores, validación, configuración, secretos).

Este documento define QUÉ debe hacer cada tool, no CÓMO implementarla (eso corresponde al diseño).

## Glosario

- **Puriq**: El agente completo en Python (CLI + core + tools + wizard + MCP).
- **Contrato**: El conjunto de tres documentos JSON (`tourism-data.json`, `site.config.json`, `theme.tokens.json`) validados contra `schemas/`.
- **Tourism_Data**: Documento `tourism-data.json`; capa de contenido (site, places, events, categories).
- **Site_Config**: Documento `site.config.json`; capa de estructura (layout, módulos, hero, deploy).
- **Theme_Tokens**: Documento `theme.tokens.json`; capa de marca (colores, tipografía, voz).
- **Place**: Un lugar turístico dentro de `Tourism_Data.places`.
- **Event**: Un evento/festividad dentro de `Tourism_Data.events`.
- **Scan_Resources**: Tool `agent/puriq/tools/scan_resources.py`; convierte recursos crudos (site.json, places.csv, events.csv) en `Tourism_Data`.
- **Import_Open_Data**: Tool `agent/puriq/tools/import_open_data.py`; enriquece `Tourism_Data` con fuentes abiertas (OpenStreetMap, Wikidata, Wikimedia Commons).
- **Generate_Content**: Tool `agent/puriq/tools/generate_content.py`; usa el LLM para rellenar descripciones, SEO y traducciones.
- **Geocode**: Tool `agent/puriq/tools/geocode.py`; convierte direcciones en coordenadas.
- **Build_Site**: Tool `agent/puriq/tools/build_site.py`; ensambla el sitio estático Astro a partir del contrato.
- **Deploy**: Tool `agent/puriq/tools/deploy.py`; publica el sitio construido mediante adaptadores por destino.
- **MCP_Server**: Servidor MCP `tourism-builder` (`agent/puriq/mcp/server.py`) que expone las tools a un cliente LLM.
- **LLM_Provider**: El proveedor de modelo de lenguaje; Amazon Bedrock (Claude) por defecto, con fallback local Ollama.
- **Overpass**: API de consulta de OpenStreetMap disponible en `OVERPASS_URL`.
- **Nominatim**: Servicio de geocodificación de OpenStreetMap disponible en `NOMINATIM_URL`.
- **Amazon_Location**: Amazon Location Service, proveedor de geocodificación preferido.
- **Template**: Plantilla Astro en `template/`, con `src/modules/` (catálogo de módulos) y `src/data/` (destino de los datos inyectados).
- **Slug**: Identificador en formato kebab-case ASCII que cumple el patrón `^[a-z0-9-]+$`.
- **Locale**: Código de idioma ISO 639-1 de dos letras (patrón `^[a-z]{2}$`).

## Requisitos

### Requisito 1: Escaneo robusto de recursos del usuario (Scan_Resources)

**Historia de usuario:** Como técnico de un gobierno local, quiero que Puriq lea mis archivos de recursos crudos (site.json, places.csv, events.csv) y los convierta en un `Tourism_Data` válido, para tener el contrato de contenido sin editar JSON a mano.

#### Criterios de aceptación

1. WHEN Scan_Resources recibe un directorio de recursos con `site.json` y `places.csv`, THE Scan_Resources SHALL producir un documento Tourism_Data con las claves `site`, `categories`, `places` y `events`.
2. IF el directorio de recursos no contiene `site.json`, THEN THE Scan_Resources SHALL lanzar un error que indique el archivo faltante y la ruta consultada.
3. IF el directorio de recursos no contiene `places.csv`, THEN THE Scan_Resources SHALL lanzar un error que indique el archivo faltante y la ruta consultada.
4. WHERE el directorio de recursos contiene `events.csv`, THE Scan_Resources SHALL incluir los eventos leídos en `Tourism_Data.events`.
5. WHERE el directorio de recursos no contiene `events.csv`, THE Scan_Resources SHALL asignar una lista vacía a `Tourism_Data.events`.
6. WHEN Scan_Resources procesa el nombre de un Place o Event, THE Scan_Resources SHALL generar el campo `id` como un Slug derivado del nombre.
7. IF una fila de `places.csv` o `events.csv` tiene el campo `name` vacío o compuesto solo por espacios, THEN THE Scan_Resources SHALL omitir esa fila del resultado.
8. WHEN una fila de `places.csv` incluye `lat` y `lng` no vacíos, THE Scan_Resources SHALL asignar `coords` con esos valores convertidos a número.
9. WHEN una fila de `places.csv` omite `lat` o `lng`, THE Scan_Resources SHALL dejar el Place sin campo `coords` para que Geocode lo complete después.
10. WHEN Scan_Resources procesa el campo `place` de una fila de `events.csv`, THE Scan_Resources SHALL asignar `placeId` solo si el valor referencia el `id` de un Place existente en el resultado.
11. IF una fila de `places.csv` contiene un valor no numérico en `lat` o `lng`, THEN THE Scan_Resources SHALL reportar un error que identifique la fila y la columna inválida.

### Requisito 2: Enriquecimiento con datos abiertos (Import_Open_Data)

**Historia de usuario:** Como técnico de un gobierno local, quiero enriquecer mi contenido con lugares turísticos de fuentes abiertas (OpenStreetMap, Wikidata, Wikimedia Commons), para no partir de cero cuando tengo pocos datos.

#### Criterios de aceptación

1. WHEN Import_Open_Data recibe un Tourism_Data con `site.center`, THE Import_Open_Data SHALL consultar Overpass por puntos de interés turísticos dentro del área geográfica de la región.
2. WHEN Import_Open_Data obtiene un punto de interés de OpenStreetMap, THE Import_Open_Data SHALL mapearlo a un Place con `source` igual a `"osm"`.
3. WHEN Import_Open_Data obtiene un punto de interés de Wikidata, THE Import_Open_Data SHALL mapearlo a un Place con `source` igual a `"wikidata"`.
4. WHERE un punto de interés importado tiene una imagen de licencia libre en Wikimedia Commons, THE Import_Open_Data SHALL agregar la URL de esa imagen al campo `images` del Place.
5. WHEN Import_Open_Data genera el `id` de un Place importado, THE Import_Open_Data SHALL usar un Slug único que no colisione con los `id` ya presentes en Tourism_Data.
6. IF un punto de interés importado coincide con un Place ya existente por nombre y proximidad geográfica, THEN THE Import_Open_Data SHALL omitir el duplicado y conservar el Place existente.
7. THE Import_Open_Data SHALL marcar los Places importados de forma que el usuario pueda revisarlos y aprobarlos antes de publicarse.
8. IF una consulta a una fuente de datos abiertos falla o agota su tiempo de espera, THEN THE Import_Open_Data SHALL devolver el Tourism_Data recibido sin cambios y registrar la causa del fallo.
9. THE Import_Open_Data SHALL producir un Tourism_Data que cumpla el esquema `tourism-data.schema.json`.

### Requisito 3: Generación de contenido con el LLM (Generate_Content)

**Historia de usuario:** Como técnico de un gobierno local, quiero que Puriq redacte automáticamente las descripciones faltantes, el SEO y las traducciones usando el tono de mi marca, para publicar contenido completo y coherente sin redactarlo manualmente.

#### Criterios de aceptación

1. WHEN Generate_Content procesa un Place cuyo campo `description` está vacío, THE Generate_Content SHALL generar una descripción usando el LLM_Provider a partir de los datos del Place.
2. WHEN Generate_Content procesa un Event cuyo campo `description` está vacío, THE Generate_Content SHALL generar una descripción usando el LLM_Provider a partir de los datos del Event.
3. WHERE un Place o Event ya tiene `description` no vacía, THE Generate_Content SHALL conservar el texto existente sin modificarlo.
4. WHEN Generate_Content construye un prompt para el LLM_Provider, THE Generate_Content SHALL incluir el tono definido en `Theme_Tokens.voice.tone`.
5. WHERE `Theme_Tokens.voice.formality` está definido, THE Generate_Content SHALL reflejar ese nivel de formalidad en el contenido generado.
6. WHEN Tourism_Data define `site.locales` con más de un Locale, THE Generate_Content SHALL generar traducciones del contenido para cada Locale configurado distinto del `site.defaultLocale`.
7. WHEN Generate_Content produce metadatos SEO para el sitio, THE Generate_Content SHALL basarlos en el nombre, la región y la descripción de Tourism_Data.
8. WHERE la variable de entorno `PURIQ_LLM_MODE` es `local`, THE Generate_Content SHALL usar el proveedor local Ollama en lugar de Amazon Bedrock.
9. WHERE `PURIQ_LLM_MODE` es `bedrock`, THE Generate_Content SHALL invocar Amazon Bedrock usando el modelo indicado en `PURIQ_BEDROCK_MODEL`.
10. IF la invocación al LLM_Provider falla para un ítem, THEN THE Generate_Content SHALL conservar el valor existente de ese ítem y registrar el fallo sin interrumpir el procesamiento de los demás ítems.
11. THE Generate_Content SHALL producir un Tourism_Data que cumpla el esquema `tourism-data.schema.json`.

### Requisito 4: Geocodificación de direcciones (Geocode)

**Historia de usuario:** Como técnico de un gobierno local, quiero que Puriq calcule las coordenadas de los lugares que solo tienen dirección, para que aparezcan correctamente en el mapa del sitio.

#### Criterios de aceptación

1. WHEN Geocode procesa un Place que tiene `address` y no tiene `coords`, THE Geocode SHALL calcular las coordenadas de esa dirección y asignarlas al campo `coords`.
2. WHERE un Place ya tiene `coords`, THE Geocode SHALL conservar las coordenadas existentes sin recalcularlas.
3. WHERE un Place no tiene `address`, THE Geocode SHALL dejar el Place sin modificar.
4. WHEN Geocode asigna `coords`, THE Geocode SHALL producir valores de `lat` entre -90 y 90 y de `lng` entre -180 y 180.
5. WHERE Amazon_Location está configurado y disponible, THE Geocode SHALL usar Amazon_Location como proveedor de geocodificación.
6. WHERE Amazon_Location no está configurado, THE Geocode SHALL usar Nominatim como proveedor de geocodificación.
7. IF el proveedor de geocodificación no encuentra coordenadas para una dirección, THEN THE Geocode SHALL dejar el Place sin `coords` y registrar la dirección no resuelta.
8. THE Geocode SHALL producir un Tourism_Data que cumpla el esquema `tourism-data.schema.json`.

### Requisito 5: Ensamblado del sitio estático (Build_Site)

**Historia de usuario:** Como técnico de un gobierno local, quiero que Puriq ensamble el sitio estático con mi contenido, mis módulos activados y mi marca, para obtener un sitio Astro listo para publicar.

#### Criterios de aceptación

1. WHEN Build_Site ensambla el sitio, THE Build_Site SHALL copiar la Template a un directorio de trabajo excluyendo `node_modules` y `dist`.
2. WHEN Build_Site prepara el directorio de trabajo, THE Build_Site SHALL escribir los tres documentos del contrato en `src/data/` de la Template.
3. WHEN Site_Config marca un módulo con `enabled` verdadero, THE Build_Site SHALL activar ese módulo en el sitio ensamblado.
4. WHEN Site_Config marca un módulo con `enabled` falso, THE Build_Site SHALL desactivar ese módulo en el sitio ensamblado.
5. WHEN Site_Config define el `order` de los módulos activados, THE Build_Site SHALL disponer los módulos en el sitio según ese orden.
6. WHEN Build_Site aplica la marca, THE Build_Site SHALL traducir los colores y la tipografía de Theme_Tokens a variables CSS del sitio.
7. WHEN el contrato está preparado en el directorio de trabajo, THE Build_Site SHALL ejecutar el build de Astro (`npm run build`).
8. WHEN el build de Astro finaliza con éxito, THE Build_Site SHALL dejar la salida estática en el directorio `dist/` del proyecto y devolver esa ruta.
9. IF el build de Astro finaliza con error, THEN THE Build_Site SHALL reportar un error con la salida relevante del proceso de build.
10. THE Build_Site SHALL validar los tres documentos del contrato contra sus esquemas antes de ejecutar el build de Astro.

### Requisito 6: Previsualización local del sitio (Build_Site.serve)

**Historia de usuario:** Como técnico de un gobierno local, quiero previsualizar el sitio ya construido en mi máquina, para revisarlo antes de publicarlo.

#### Criterios de aceptación

1. WHEN el usuario solicita la previsualización con un `dist/` existente, THE Build_Site SHALL servir el contenido de `dist/` en el puerto indicado.
2. IF no existe el directorio `dist/` al solicitar la previsualización, THEN THE Build_Site SHALL reportar un error indicando que debe ejecutarse `puriq build` primero.
3. WHEN el usuario no indica puerto para la previsualización, THE Build_Site SHALL usar el puerto por defecto 4322.

### Requisito 7: Publicación por adaptadores de destino (Deploy)

**Historia de usuario:** Como técnico de un gobierno local, quiero publicar el sitio construido en el destino que elija, para tenerlo en línea con una URL pública.

#### Criterios de aceptación

1. WHEN Deploy recibe un destino soportado y existe `dist/`, THE Deploy SHALL publicar el contenido de `dist/` mediante el adaptador correspondiente y devolver la URL pública del sitio.
2. IF el destino solicitado no está entre los soportados (`aws-amplify`, `s3-cloudfront`, `static-export`, `vercel`, `netlify`), THEN THE Deploy SHALL reportar un error que liste los destinos válidos.
3. IF no existe el directorio `dist/` al solicitar la publicación, THEN THE Deploy SHALL reportar un error indicando que debe ejecutarse `puriq build` primero.
4. WHERE el destino es `aws-amplify`, THE Deploy SHALL publicar el sitio en AWS Amplify Hosting mediante boto3 y devolver la URL pública.
5. WHERE el destino es `s3-cloudfront`, THE Deploy SHALL subir `dist/` a un bucket S3, invalidar la distribución CloudFront asociada mediante boto3 y devolver la URL pública.
6. WHERE el destino es `static-export`, THE Deploy SHALL dejar `dist/` listo para copia manual y devolver la ruta local del directorio.
7. IF el proveedor de destino rechaza la publicación o faltan credenciales, THEN THE Deploy SHALL reportar un error que identifique la causa sin exponer valores de secretos.

### Requisito 8: Exposición de tools vía MCP (MCP_Server)

**Historia de usuario:** Como cliente LLM (por ejemplo Claude), quiero invocar las tools de Puriq a través de MCP, para orquestar la construcción del sitio conversacionalmente.

#### Criterios de aceptación

1. WHEN el MCP_Server se inicia, THE MCP_Server SHALL registrar las tools `scan_resources`, `import_open_data`, `generate_content`, `build_site` y `deploy`.
2. WHEN un cliente MCP invoca una tool registrada, THE MCP_Server SHALL delegar en la misma implementación del core que usa el CLI, sin duplicar la lógica.
3. WHEN el MCP_Server expone una tool, THE MCP_Server SHALL declarar su esquema de entrada conforme a la firma de la tool del core.
4. IF una tool invocada vía MCP lanza un error, THEN THE MCP_Server SHALL devolver al cliente un mensaje de error descriptivo sin exponer valores de secretos.

### Requisito 9: Manejo de errores y configuración transversal

**Historia de usuario:** Como técnico de un gobierno local, quiero mensajes claros en el CLI y una configuración por variables de entorno segura, para operar Puriq sin exponer secretos ni encontrarme con fallos opacos.

#### Criterios de aceptación

1. IF una tool lanza un error durante un comando del CLI, THEN THE Puriq SHALL mostrar en el CLI un mensaje descriptivo que indique la causa y la acción sugerida.
2. THE Puriq SHALL leer la configuración sensible (credenciales AWS, modelo del LLM, modo del LLM, destino de deploy) desde variables de entorno definidas en `agent/.env`.
3. THE Puriq SHALL excluir los valores de secretos de los mensajes de error y de la salida del CLI.
4. WHEN una operación produce o transforma un documento del contrato, THE Puriq SHALL validarlo contra su esquema en `schemas/` antes de escribirlo o usarlo en el build.
5. WHERE una variable de entorno requerida por una tool no está definida, THE Puriq SHALL reportar un error que nombre la variable faltante.
