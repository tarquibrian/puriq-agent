# Plan de Implementación: landing-and-design-system

## Overview

Este plan convierte el diseño aprobado en pasos de codificación incrementales sobre dos superficies acopladas: la **Template Astro** (`template/src/`, en TypeScript/Astro) y el lado **Python** del agente y el Wizard (`agent/puriq/`). El objetivo es entregar, como una sola funcionalidad, (1) el **Design_System** guiado por datos y (2) el **Landing_Module** de portada componible, respetando de forma estricta las invariantes de arquitectura: el agente/Wizard **componen y configuran** secciones pre-construidas y **nunca generan su código**; el LLM solo redacta copy dentro del Contrato; toda escritura del Contrato pasa por `schemas.validate` **antes** de persistir o construir; y la retrocompatibilidad es total (defaults del sistema, propiedades opcionales, el build nunca falla por ausencia de `landing` o de tokens ampliados).

El orden sigue la estrategia de testabilidad del diseño y evita código huérfano: primero las **extensiones aditivas de esquema** (para que los tokens y `landing` sean válidos), luego la **capa pura de tokens** (`resolveTokens`/`tokensToCssVars`) que alimenta `Base.astro`, después la **biblioteca de UI_Component** que consumen todas las secciones, luego el **Landing_Module** (resolución + registro + componentes de sección), en paralelo el **lado Python** (`build_site._theme_to_css`, `generate_content.enrich_landing`) y el **Wizard** (`build_landing` + endpoint + UI), después el **uplift de los Content_Module** que reutiliza la biblioteca ya construida, y finalmente el **cableado end-to-end** y la revisión de invariantes.

Lenguajes de implementación (definidos en el diseño): **TypeScript/Astro** para la lógica y los componentes de la Template; **Python** para el agente y el Wizard. Pruebas de propiedad con **fast-check** para la lógica pura de la Template (`resolveTokens`, `tokensToCssVars`, `resolveLanding`, contraste WCAG) e **Hypothesis** para la lógica del agente/Wizard (`build_landing`, `enrich_landing`, validación-antes-de-escribir), mínimo **100 iteraciones** por propiedad. La capa de render/visual se cubre con pruebas de ejemplo/snapshot y las invariantes de "sin marca hardcodeada" y "el agente no genera código de secciones" con revisión de código/lint.

Convención: las subtareas marcadas con `*` (tests) son opcionales y pueden omitirse para un MVP más rápido; las tareas de implementación no marcadas son obligatorias. Cada prueba de propiedad se etiqueta con `// Feature: landing-and-design-system, Property {n}: {texto}` (o `#` en Python).

## Tasks

- [x] 1. Extensiones aditivas y retrocompatibles del esquema del Contrato
  - [x] 1.1 Extender `schemas/theme-tokens.schema.json` con los tokens ampliados opcionales (DD-2)
    - Añadir bajo `properties` (ninguno en `required`): `spacing`, `typeScale`, `shadows`, `radii`, `breakpoints`, `motion`, `container` con la forma y tipos del diseño
    - `typeScale.*` exige `size` (con `lineHeight` opcional); `motion` restringe `durationFast`/`durationBase`/`easing`; mantener `additionalProperties: false` en la raíz para seguir detectando typos
    - Un valor con tipo/formato inválido (p. ej. `spacing.md` numérico) debe ser rechazado por la validación nombrando el token; los documentos previos que omiten todo siguen validando
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 16.2_
  - [x] 1.2 Extender `schemas/site-config.schema.json` con la propiedad opcional `landing` (DD-3)
    - Añadir `landing` como array opcional de secciones con `type` (enum `hero|features|cta|gallery|stats`), `enabled` (bool), `order` (entero ≥ 1) y `content` (objeto abierto); conservar el `hero` heredado y mantener `additionalProperties: false`
    - Una sección con `type` fuera del catálogo, `order < 1` o `enabled` no booleano debe ser rechazada nombrando el campo; los documentos previos sin `landing` siguen validando
    - _Requirements: 13.1, 13.4, 16.1_
  - [ ]* 1.3 Pruebas de ejemplo de validez de esquema con/sin extensiones
    - Theme_Tokens con todos, algunos y ningún token ampliado validan; token con tipo inválido se rechaza nombrándolo
    - Site_Config con `landing`, sin `landing` y con `landing` inválida (tipo/orden/enabled) — validez y rechazo con campo
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 13.1, 13.4, 16.1, 16.2_

- [ ] 2. Capa pura de tokens y defaults en la Template (DD-1)
  - [x] 2.1 Crear `template/src/design-system/defaults.ts` con `DESIGN_DEFAULTS`, tipos y `resolveTokens`
    - Definir los tipos `ThemeTokens` (parcial) y `ResolvedTokens` (completo) y la tabla `DESIGN_DEFAULTS` con el conjunto completo de tokens del sistema (colors, typography, spacing, typeScale, shadows, radii, breakpoints, motion, container, radius)
    - Implementar `resolveTokens(theme): ResolvedTokens` puro: fusiona `DESIGN_DEFAULTS` con `theme` sin pisar lo definido por el usuario; token ausente ⇒ default correspondiente (incluido motion)
    - _Requirements: 1.4, 6.4, 16.2, 16.5_
  - [ ] 2.2 Implementar `tokensToCssVars(tokens)` en `template/src/design-system/defaults.ts`
    - Aplanar los tokens resueltos a un mapa `Record<string,string>` de variables CSS (`--space-*`, `--fs-*`/`--lh-*`, `--shadow-*`, `--radius-*`, `--bp-*`, `--motion-*`, `--container-*`, `--color-*`, `--font-*`), incluidos `colors.secondary`/`colors.accent` cuando existen
    - _Requirements: 2.1, 2.4_
  - [ ]* 2.3 Prueba de propiedad: los tokens ausentes se completan con el default (fast-check)
    - **Property 1: Los tokens ausentes se completan con el default del Design_System**
    - **Validates: Requirements 1.4, 6.4, 16.2**
  - [ ]* 2.4 Prueba de propiedad: el merge de defaults es idempotente (fast-check)
    - **Property 2: El merge de defaults es idempotente**
    - **Validates: Requirements 1.4, 16.5**
  - [ ]* 2.5 Prueba de propiedad: cada token resuelto se materializa como variable CSS (fast-check)
    - **Property 3: Cada token resuelto se materializa como una variable CSS**
    - **Validates: Requirements 2.1, 2.4**

- [ ] 3. Base_Layout, estilos globales y Layout_Variant
  - [ ] 3.1 Reescribir `template/src/layouts/Base.astro` para derivarse solo de tokens
    - Consumir `tokensToCssVars(resolveTokens(themeTokens))` en `define:vars`; eliminar todo valor de marca fijado en el código (anchos, paddings, `#fff`) derivándolo de variables con defaults
    - Aplicar la Type_Scale a `h1..h3` y cuerpo (tamaños ordenados de mayor a menor), limitar el ancho al `container` de tokens y centrarlo, estilos globales mobile-first, HTML semántico (`header`/`nav`/`main`/`footer`), foco visible y navegación accesible en pantalla estrecha
    - Escribir `data-variant={siteConfig.layout ?? "clasico"}` en el elemento raíz
    - _Requirements: 2.1, 2.2, 3.1, 3.2, 3.4, 5.1, 5.2, 5.4, 7.5, 8.1, 8.2_
  - [ ] 3.2 Implementar las reglas de Layout_Variant `clasico`/`moderno` por `data-variant` (DD-4)
    - Reglas condicionadas por `[data-variant="clasico"]`/`[data-variant="moderno"]` que difieren en composición del header, tratamiento del Hero_Section y estilo de tarjetas, leyendo **las mismas** variables `--color-*`/`--fs-*`/`--space-*` (sin fijar colores/tipografías en ninguna variante)
    - _Requirements: 7.1, 7.2, 7.3, 7.4_
  - [ ]* 3.3 Prueba de propiedad: la Layout_Variant resuelta respeta el valor o el default (fast-check)
    - **Property 10: La Layout_Variant resuelta respeta el valor configurado o el default**
    - **Validates: Requirements 7.5**
  - [ ]* 3.4 Pruebas de snapshot de Base.astro, Type_Scale y variantes
    - Type_Scale ordenada y ancho de contenedor centrado (Req 3.1, 3.4); HTML semántico y foco visible (Req 8.1, 8.2); diferencias reales entre `clasico` y `moderno` (Req 7.1–7.3)
    - _Requirements: 3.1, 3.4, 5.1, 5.2, 7.1, 7.2, 7.3, 8.1, 8.2_

- [ ] 4. Biblioteca de UI_Component reutilizables
  - [ ] 4.1 Crear `template/src/design-system/ui/Container.astro` y `Section.astro`
    - `Container`: aplica `--container-*` y padding `--space-*` (Req 4.1); `Section`: ritmo vertical con `--space-*` y separación entre secciones consecutivas (Req 3.2, 3.3)
    - Apariencia derivada exclusivamente de variables CSS; contenido por slots/props conservando estilos del sistema
    - _Requirements: 3.2, 3.3, 4.1, 4.4, 4.5_
  - [ ] 4.2 Crear `template/src/design-system/ui/Button.astro`
    - Props `href?`, `variant('primary'|'ghost')`; color/radio de tokens, estados de foco y hover con transición `--motion-*`, anulada bajo `prefers-reduced-motion: reduce`; sin marca hardcodeada
    - _Requirements: 4.2, 4.5, 6.1, 6.2, 6.3, 8.2_
  - [ ] 4.3 Crear `template/src/design-system/ui/Card.astro`
    - Radio, sombra y padding de tokens; contenido por slots/props conservando estilos; apariencia solo por variables CSS
    - _Requirements: 4.3, 4.4, 4.5_
  - [ ] 4.4 Crear `template/src/design-system/ui/Grid.astro` responsive
    - Props `min?`, `gap?`; columnas que se ajustan según los Breakpoint de tokens (mobile-first), sin desbordamiento horizontal y con imágenes escaladas dentro de su contenedor
    - _Requirements: 5.3, 5.4, 5.5_
  - [ ]* 4.5 Pruebas de snapshot de los UI_Component
    - Render de `Container`/`Section`/`Button`/`Card`/`Grid`; hover/foco y Reduced_Motion sobre elementos interactivos
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 6.1, 6.2, 6.3_

- [ ] 5. Landing_Module: resolución y composición de la portada (DD-3)
  - [ ] 5.1 Implementar `template/src/design-system/landing/resolve.ts` (`resolveLanding`)
    - Función pura: aplica la precedencia de hero (landing gana sobre el heredado; hero heredado se sintetiza cuando no hay `landing`), filtra `enabled=true` + tipo del catálogo + contenido esencial no vacío, y ordena por `order` ascendente; omite con gracia tipos no soportados y secciones sin contenido
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 12.5, 13.5, 16.1, 16.4_
  - [ ] 5.2 Crear `template/src/design-system/landing/registry.ts` e integrar en `template/src/pages/index.astro`
    - `SECTION_REGISTRY: Record<LandingType, AstroComponent>` (`hero/features/cta/gallery/stats`); `index.astro` deja de renderizar el hero inline y compone `resolveLanding(siteConfig)` por encima de los Content_Module activos, omitiendo tipos ausentes del registro sin romper el render
    - _Requirements: 10.1, 10.4, 10.5_
  - [ ]* 5.3 Prueba de propiedad: la resolución devuelve solo secciones activas del catálogo, en orden (fast-check)
    - **Property 5: La resolución de la portada devuelve solo secciones activas del catálogo, en orden**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 12.5**
  - [ ]* 5.4 Prueba de propiedad: los tipos no soportados se omiten sin afectar al resto (fast-check)
    - **Property 6: Los tipos no soportados se omiten sin afectar al resto**
    - **Validates: Requirements 10.5, 12.5**
  - [ ]* 5.5 Prueba de propiedad: el Landing_Module se compone por encima de los Content_Module (fast-check)
    - **Property 7: El Landing_Module se compone por encima de los Content_Module**
    - **Validates: Requirements 10.1**
  - [ ]* 5.6 Prueba de propiedad: precedencia del Hero_Section entre `landing` y el `hero` heredado (fast-check)
    - **Property 8: Precedencia del Hero_Section entre `landing` y el `hero` heredado**
    - **Validates: Requirements 13.5, 16.4**
  - [ ]* 5.7 Prueba de propiedad: la portada se resuelve sin error ante configuración ausente/parcial/completa (fast-check)
    - **Property 9: La portada se resuelve sin error ante configuración ausente, parcial o completa**
    - **Validates: Requirements 16.1, 16.5**

- [ ] 6. Componentes de Landing_Section (catálogo)
  - [ ] 6.1 Crear `template/src/design-system/landing/Hero.astro`
    - Fondo imagen/video cuando está definido; titular/subtítulo sobre el fondo; `Button` CTA cuando hay etiqueta+destino; overlay que preserve contraste ≥ 4.5:1; fondo derivado de los colores de tokens cuando no hay recurso
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 8.5_
  - [ ] 6.2 Crear `template/src/design-system/landing/Features.astro`
    - `Grid` de `Card` con título + descripción por destacado; reutiliza UI_Component y tokens
    - _Requirements: 12.1_
  - [ ] 6.3 Crear `template/src/design-system/landing/Cta.astro`
    - Mensaje + `Button` que enlaza al destino configurado
    - _Requirements: 12.2_
  - [ ] 6.4 Crear `template/src/design-system/landing/Gallery.astro`
    - Galería responsive con texto alternativo por imagen; imágenes escaladas dentro del contenedor
    - _Requirements: 12.3, 8.4, 5.5_
  - [ ] 6.5 Crear `template/src/design-system/landing/Stats.astro`
    - Cada métrica renderiza su valor y su etiqueta
    - _Requirements: 12.4_
  - [ ]* 6.6 Prueba de propiedad: cada imagen informativa se renderiza con su texto alternativo (fast-check)
    - **Property 16: Cada imagen informativa se renderiza con su texto alternativo**
    - **Validates: Requirements 8.4, 12.3**
  - [ ]* 6.7 Prueba de propiedad: cada métrica de Stats se renderiza con su valor y su etiqueta (fast-check)
    - **Property 17: Cada métrica de Stats se renderiza con su valor y su etiqueta**
    - **Validates: Requirements 12.4**
  - [ ]* 6.8 Prueba de propiedad: el contraste texto/fondo derivado de tokens cumple WCAG (fast-check)
    - **Property 18: El contraste texto/fondo derivado de los tokens cumple el umbral WCAG**
    - **Validates: Requirements 8.3, 8.5, 11.4**
  - [ ]* 6.9 Pruebas de snapshot de las secciones con y sin contenido
    - Render de Hero/Features/CTA/Gallery/Stats en sus casos con contenido y con contenido esencial ausente (omisión con gracia)
    - _Requirements: 11.1, 11.2, 11.3, 11.5, 12.1, 12.2, 12.5_

- [ ] 7. Checkpoint - Asegurar que las pruebas pasan
  - Ejecutar el build de la Template y las pruebas de la lógica/render (tokens, UI, landing) y confirmar que todo compila y las propiedades pasan; consultar al usuario si surgen dudas.

- [ ] 8. Lado Python: materialización de tokens en `build_site` (Req 5.6, 13, 16)
  - [x] 8.1 Extender `agent/puriq/tools/build_site.py::_theme_to_css` con defaults + tokens ampliados
    - Replicar la tabla de `DESIGN_DEFAULTS` y la lógica de `resolveTokens` en Python para emitir el conjunto completo de variables (`--space-*`, `--fs-*`/`--lh-*`, `--shadow-*`, `--radius-*`, `--bp-*`, `--motion-*`, `--container-*` además de `--color-*`/`--font-*`), aplicando defaults cuando faltan
    - _Requirements: 5.6, 16.2, 16.5_
  - [ ] 8.2 Asegurar la validación previa a escritura/build en `_write_contract` para `landing` y tokens ampliados
    - `_write_contract` valida los 3 documentos contra `schemas/` antes de escribir/construir; un `site.config.json` con `landing` inválida o un `theme.tokens.json` con token inválido detiene el build nombrando el campo
    - _Requirements: 13.3, 13.4, 16.5_
  - [ ]* 8.3 Prueba de propiedad: validación estricta antes de toda escritura del Contrato (Hypothesis)
    - **Property 4: Validación estricta antes de toda escritura del Contrato**
    - **Validates: Requirements 1.5, 1.6, 13.3, 13.4, 14.4, 15.5**
  - [ ]* 8.4 Prueba de integración: `build_site` materializa `theme.css` con defaults ante un theme parcial
    - Theme_Tokens sin tokens ampliados ⇒ `src/data/theme.css` contiene el conjunto completo de variables con defaults; el build no falla
    - _Requirements: 16.2, 16.5_

- [ ] 9. Lado Python: redacción del copy de portada con el LLM (DD-5, Req 15)
  - [x] 9.1 Implementar `agent/puriq/tools/generate_content.py::enrich_landing`
    - Recorrer `site_config.landing`; para cada sección **activa** con un campo de copy vacío generar el texto con `get_provider()` a partir de `Tourism_Data` y el `type`; incluir `voice.tone` en el prompt vía `_voice_directives`; preservar el copy no vacío; tolerar fallo por sección con `_safe_complete`; devolver un `site_config` conforme a `site-config.schema.json`
    - Campos de copy por tipo: `hero.{headline,subheadline}`, `features.items[].{title,description}`, `cta.message`, `stats.metrics[].label`, `gallery.images[].alt`
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_
  - [ ] 9.2 Cablear `enrich_landing` en el pipeline del core junto al `enrich` de contenido
    - Invocar `enrich_landing` durante `build`/`collect` detrás del mismo *translate gate* conceptual (solo cuando hay copy que redactar), sin reimplementar la selección de proveedor ni la tolerancia a fallos
    - _Requirements: 15.1_
  - [ ]* 9.3 Prueba de propiedad: el copy vacío de secciones activas se completa; el no vacío se conserva (Hypothesis)
    - **Property 12: El copy vacío de secciones activas se completa; el copy no vacío se conserva**
    - **Validates: Requirements 15.1, 15.2**
  - [ ]* 9.4 Prueba de propiedad: el prompt del copy refleja la voz de marca (Hypothesis)
    - **Property 13: El prompt del copy refleja la voz de marca**
    - **Validates: Requirements 15.3**
  - [ ]* 9.5 Prueba de propiedad: robustez ante fallo del LLM por sección (Hypothesis)
    - **Property 14: Robustez ante fallo del LLM por sección**
    - **Validates: Requirements 15.4, 15.5**

- [ ] 10. Checkpoint - Asegurar que las pruebas pasan
  - Ejecutar la suite Python de la fase de datos (`build_site`, `generate_content`) y confirmar que las propiedades y la integración con mocks pasan; consultar al usuario si surgen dudas.

- [ ] 11. Wizard: construcción y persistencia de la portada (DD-6, Req 14)
  - [ ] 11.1 Implementar `agent/puriq/wizard/landing.py::build_landing` (constructor puro)
    - Construir `Site_Config.landing` desde la selección ordenada del Wizard: asignar `order = posición+1` estrictamente creciente, restringir `type` al catálogo (rechazar fuera del catálogo con `LandingCatalogError`), conservar `content`
    - _Requirements: 14.2, 10.4_
  - [ ] 11.2 Extender `PUT /api/site-config` en `agent/puriq/wizard/server.py` para aceptar `landing`
    - Recibir la lista ordenada de secciones con `enabled` y `content`, construir `landing` con `build_landing`, persistir vía `contracts.merge_document` + `save_contract` (validate-before-write); inválido → `422` redactado que nombra el campo
    - _Requirements: 14.3, 14.4, 14.6_
  - [ ] 11.3 Prellenar `landing` existente desde `GET /api/state` en `agent/puriq/wizard/server.py`
    - Cargar las Landing_Section existentes de `Site_Config.landing` para que el Wizard_UI prellene los campos al iniciar el paso
    - _Requirements: 14.5_
  - [ ]* 11.4 Prueba de propiedad: `build_landing` asigna un orden coherente con la posición (Hypothesis)
    - **Property 11: `build_landing` asigna un orden coherente con la posición**
    - **Validates: Requirements 14.2, 10.4**
  - [ ]* 11.5 Prueba de integración de persistencia y prellenado de `landing`
    - `PUT /api/site-config` persiste `landing` tras validar; `GET /api/state` devuelve `landing` para prellenar; sección inválida → `422` con campo
    - _Requirements: 13.2, 14.4, 14.5_

- [ ] 12. Wizard: paso de portada en la UI
  - [ ] 12.1 Añadir el paso "Portada" en `agent/puriq/wizard/static/`
    - Listar las Landing_Section del catálogo con controles de activar/desactivar y reordenar; campos de copy editable (titular, subtítulo, destacados, mensaje, etiqueta de CTA); persistir vía `fetch` al endpoint; prellenar desde `GET /api/state`; el Wizard solo compone secciones pre-construidas, sin generar código
    - _Requirements: 14.1, 14.2, 14.3, 14.5, 14.6_

- [ ] 13. Uplift visual de los Content_Module (DD-7, Req 9)
  - [ ] 13.1 Uplift de `template/src/modules/places/Places.astro`
    - Envolver el contenido en `Container`/`Section`/`Card`/`Grid` y referenciar variables CSS en lugar de estilos inline; grid responsive por breakpoints; conservar intactos los datos de entrada (`places`) y el comportamiento
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 16.3_
  - [ ] 13.2 Uplift de `template/src/modules/events/Events.astro`
    - Igual patrón; conservar `events` y su orden funcional; grid responsive
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 16.3_
  - [ ] 13.3 Uplift de `template/src/modules/map/Map.astro`
    - Reemplazar solo la presentación (contenedor/estilos por tokens); **no** tocar la lógica de Leaflet ni sus datos
    - _Requirements: 9.1, 9.2, 9.4, 16.3_
  - [ ] 13.4 Uplift de `template/src/modules/blog/Blog.astro`
    - Usar UI_Component y tokens; conservar la lectura de la Content Collection y el orden por fecha
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 16.3_
  - [ ] 13.5 Uplift de `template/src/modules/chatweb/Chat.astro`
    - Uplift visual con tokens/UI_Component; conservar la recuperación client-side y el `faq` de entrada
    - _Requirements: 9.1, 9.2, 9.4, 16.3_
  - [ ]* 13.6 Prueba de propiedad: el uplift preserva los datos y el comportamiento de los Content_Module
    - **Property 15: El uplift preserva los datos y el comportamiento de los Content_Module**
    - **Validates: Requirements 9.4, 16.3**
  - [ ]* 13.7 Pruebas de snapshot de los módulos con tokens y grid responsive
    - Módulos renderizando con variables CSS y cuadrícula responsive conforme al Req 5
    - _Requirements: 9.2, 9.3_

- [ ] 14. Revisión de código / lint de invariantes transversales
  - [ ] 14.1 Verificar ausencia de marca hardcodeada en la capa de presentación
    - Revisión/lint que confirme la ausencia de literales de marca (hex/px) inline en estilos globales, UI_Component, Content_Module y reglas de variante; todo deriva de variables CSS de Design_Tokens
    - _Requirements: 2.2, 2.3, 4.5, 7.4, 9.1_
  - [ ] 14.2 Verificar que el agente y el Wizard solo escriben datos, no generan código de secciones
    - Revisión que confirme que `enrich_landing`, `build_landing` y los endpoints solo producen datos del Contrato (`site.config.json`) y jamás código de secciones/build
    - _Requirements: 14.6_

- [ ] 15. Cableado end-to-end y retrocompatibilidad
  - [ ] 15.1 Verificar el build completo con Contrato parcial, ausente y completo
    - Confirmar que `index.astro` compone `landing` + Content_Module y que el build produce un sitio válido con `landing`/tokens ampliados ausentes, parciales o completos; un proyecto anterior (solo `hero` heredado) sintetiza el Hero_Section sin error
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 13.5_
  - [ ]* 15.2 Prueba de integración end-to-end del build con Contrato completo
    - Build con `landing` completa + tokens ampliados: portada compuesta, `theme.css` con todas las variables, módulos con uplift; sin errores
    - _Requirements: 16.5_

- [ ] 16. Checkpoint final - Asegurar que todas las pruebas pasan
  - Ejecutar la suite completa de la Template (fast-check + snapshots) y la de Python (Hypothesis + integración con mocks) y confirmar la cobertura de los 16 requisitos y las 18 propiedades; consultar al usuario si surgen dudas.

## Notas

- Las subtareas marcadas con `*` (tests) son opcionales y pueden omitirse para un MVP más rápido; las tareas de implementación no marcadas son obligatorias.
- Cada tarea referencia los requisitos que cubre para trazabilidad; en conjunto cubren los 16 requisitos del documento aprobado.
- Las 18 propiedades del diseño se validan con **fast-check** en la Template (`resolveTokens`/`tokensToCssVars`, `resolveLanding`, contraste WCAG, render de secciones) e **Hypothesis** en Python (`build_landing`, `enrich_landing`, validación-antes-de-escribir), mínimo 100 iteraciones, etiquetadas con `// Feature: landing-and-design-system, Property {n}: {texto}` (o `#`). La capa de render/visual (variantes, motion, responsive, foco, semántica) se cubre con ejemplos/snapshots; las invariantes "sin marca hardcodeada" y "el agente no genera código de secciones" con revisión de código/lint (Tarea 14).
- Todas las extensiones de esquema son **aditivas y opcionales** (Tarea 1) para preservar la retrocompatibilidad: los documentos existentes siguen validando y el build nunca falla por ausencia de `landing` o de tokens ampliados (defaults del Design_System replicados en `defaults.ts` y en `build_site._theme_to_css`).
- El orden es incremental y verificable: esquema → capa pura de tokens → Base_Layout/variantes → UI_Component → Landing_Module y secciones → lado Python (`build_site`, `enrich_landing`) → Wizard (`build_landing` + endpoint + UI) → uplift de módulos → cableado end-to-end. Cada paso construye sobre el anterior sin dejar código huérfano: `index.astro` (5.2) integra secciones y módulos, y la Tarea 15 cierra el cableado completo.
- Invariantes respetadas en todas las tareas: el agente/Wizard componen secciones pre-construidas sin generar su código (Req 14.6); el LLM solo redacta copy dentro del Contrato (Req 15); toda escritura del Contrato valida contra `schemas/` antes de persistir (Req 1.5, 13.3, 15.5); y la marca fluye exclusivamente vía variables CSS de tokens (Req 2).

## Task Dependency Graph

Las tareas de una misma onda son independientes y pueden ejecutarse en paralelo; una onda N solo arranca cuando terminan las ondas 0..N-1. Las tareas que escriben el mismo archivo (`defaults.ts` en 2.1/2.2, `Base.astro` en 3.1/3.2, `server.py` en 11.2/11.3, `build_site.py` en 8.1/8.2) se reparten en ondas distintas para evitar conflictos de escritura.

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "8.1", "9.1", "11.1"] },
    { "id": 1, "tasks": ["1.3", "2.2", "8.2", "9.2", "9.3", "9.4", "9.5", "11.2", "11.4"] },
    { "id": 2, "tasks": ["2.3", "2.4", "2.5", "3.1", "4.1", "4.2", "4.3", "4.4", "5.1", "8.3", "8.4", "11.3", "11.5"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "4.5", "6.1", "6.2", "6.3", "6.4", "6.5", "12.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "5.4", "5.6", "6.6", "6.7", "6.8", "6.9", "13.1", "13.2", "13.3", "13.4", "13.5"] },
    { "id": 5, "tasks": ["5.5", "5.7", "13.6", "13.7", "14.1", "14.2"] },
    { "id": 6, "tasks": ["15.1", "15.2"] }
  ]
}
```
