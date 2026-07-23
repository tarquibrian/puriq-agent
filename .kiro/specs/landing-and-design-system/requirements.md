# Documento de Requisitos

## Introducción

Puriq genera sitios turísticos estáticos (Astro) para gobiernos locales componiendo módulos pre-construidos y aplicando una marca definida por datos. Hoy el sitio funciona de extremo a extremo, pero su calidad visual es básica y genérica: `template/src/layouts/Base.astro` aplica solo colores y tipografías como variables CSS (sin escala de espaciado, escala tipográfica, componentes, comportamiento responsive real, estados de foco/hover ni motion), `template/src/pages/index.astro` apila los módulos activos tras un hero trivial (`<h1>` + `<p>` centrados, sin imagen de fondo, CTA ni overlay), no existe una capa de composición de portada (landing/home), y las variantes de layout `clasico` y `moderno` casi no se diferencian en el render.

Este spec cubre **dos objetivos acoplados** que se entregan como una sola funcionalidad:

1. **Sistema de diseño / calidad visual de la Template.** Introducir una escala de espaciado, una escala tipográfica, tokens de tema ampliados (sombras, radios, breakpoints, motion, anchos de contenedor), un conjunto de componentes UI reutilizables, comportamiento responsive mobile-first, micro-interacciones sobrias y dos variantes de layout (`clasico`/`moderno`) genuinamente distintas. Todo permanece **guiado por datos** desde `theme.tokens.json` (sin marca hardcodeada) y debe elevar también los módulos existentes (`map`, `places`, `events`, `blog`, `chatweb`), no solo la portada.

2. **Módulo Landing/Home configurable.** Un conjunto de secciones componibles de portada (Hero enriquecido con imagen de fondo/overlay/CTA, Features/Destacados, Call-to-Action, Galería y Stats) que el usuario puede activar/desactivar, ordenar y editar en su texto desde el Wizard, con el copy redactable por el LLM. La configuración de estas secciones se persiste en el Contrato (extendiendo `site.config.json`) y se valida contra su esquema.

Este spec respeta las reglas transversales de Puriq: el agente **compone y configura módulos pre-construidos y nunca genera el código de los módulos**; el LLM solo trabaja sobre contenido/configuración (redactar copy), no sobre código; toda nueva configuración vive en los documentos del Contrato y se **valida contra `schemas/` antes de escribirse**; y la marca se aplica exclusivamente vía tokens (variables CSS con `define:vars`, como ya hace `Base.astro`). El Wizard es la UX primaria de los usuarios no programadores mediante formularios guiados. Se preserva la **retrocompatibilidad**: las nuevas secciones y tokens son opcionales con valores por defecto sensatos, de modo que un build nunca falla cuando están ausentes.

**Fuera de alcance (no incluir):** editor libre de layout o CSS arbitrario, panel de administración, i18n avanzado, temas descargables/marketplace de plantillas, y la lógica interna del chatbot RAG (solo su uplift visual). La composición de portada se limita a las secciones enumeradas.

## Glosario

- **Puriq**: El agente completo en Python (core + tools + CLI + Wizard + MCP) que compone y configura módulos pre-construidos.
- **Contrato**: El conjunto de tres documentos JSON (`tourism-data.json`, `site.config.json`, `theme.tokens.json`) validados contra `schemas/`.
- **Tourism_Data**: Documento `tourism-data.json`; capa de contenido (site, places, events, categories).
- **Site_Config**: Documento `site.config.json`; capa de estructura (layout, módulos, hero, deploy y —con este spec— secciones de portada).
- **Theme_Tokens**: Documento `theme.tokens.json`; capa de marca (colores, tipografía, voz y —con este spec— tokens de diseño ampliados).
- **Template**: Plantilla Astro en `template/`, con `src/layouts/`, `src/pages/`, `src/modules/`, `src/lib/` y `src/data/`.
- **Base_Layout**: El layout base `template/src/layouts/Base.astro` que aplica los Design_Tokens como variables CSS y define los estilos globales, el header y el footer.
- **Home_Page**: La página de portada `template/src/pages/index.astro` que renderiza el Landing_Module y compone los Content_Module activos.
- **Design_System**: El conjunto de Design_Tokens, estilos globales y UI_Component reutilizables que rige la apariencia del sitio.
- **Design_Tokens**: Los valores de diseño definidos en Theme_Tokens: colores, tipografía, escala de espaciado, escala tipográfica, sombras, radios, breakpoints, motion y anchos de contenedor.
- **Spacing_Scale**: La escala de espaciado (conjunto de pasos de espaciado) definida en Design_Tokens y aplicada de forma consistente en el sitio.
- **Type_Scale**: La escala tipográfica (conjunto de tamaños de fuente y alturas de línea) definida en Design_Tokens.
- **Breakpoint**: Un umbral de ancho de viewport definido en Design_Tokens que gobierna el comportamiento responsive.
- **UI_Component**: Un componente Astro reutilizable del Design_System (por ejemplo Container, Section, Button, Card) que consume Design_Tokens vía variables CSS.
- **Content_Module**: Un módulo de contenido pre-construido del catálogo (`map`, `places`, `events`, `blog`, `chatweb`) en `template/src/modules/`.
- **Landing_Module**: La capa de composición de portada que renderiza un conjunto ordenado de Landing_Section por encima de los Content_Module.
- **Landing_Section**: Una sección componible de portada de un tipo soportado (`hero`, `features`, `cta`, `gallery`, `stats`).
- **Hero_Section**: La Landing_Section de tipo `hero`, con imagen o video de fondo, overlay, titular, subtítulo y llamada a la acción.
- **Layout_Variant**: La variante estética global del sitio, `clasico` o `moderno`, definida en `Site_Config.layout`.
- **Wizard**: La aplicación web local (FastAPI + UI de formularios) que es la interfaz primaria del usuario no programador.
- **Wizard_Server**: El backend FastAPI del Wizard que persiste los documentos del Contrato.
- **Wizard_UI**: La interfaz de formularios por pasos del Wizard.
- **Generate_Content**: La tool `agent/puriq/tools/generate_content.py` que usa el LLM_Provider para redactar contenido/copy.
- **LLM_Provider**: El proveedor de modelo de lenguaje (Amazon Bedrock por defecto, Ollama local opcional).
- **Build_Site**: La tool `agent/puriq/tools/build_site.py` que ensambla el sitio estático a partir del Contrato.
- **Schema_Validation**: La validación de un documento del Contrato contra su esquema en `schemas/`.
- **Reduced_Motion**: La preferencia del sistema del visitante `prefers-reduced-motion: reduce`.
- **Contrast_Ratio**: La relación de contraste de color entre texto y fondo según WCAG 2.1.
- **Slug**: Identificador en formato kebab-case ASCII que cumple el patrón `^[a-z0-9-]+$`.

## Requisitos

### Requisito 1: Tokens de diseño ampliados en Theme_Tokens

**Historia de usuario:** Como técnico de un gobierno local, quiero que la marca defina espaciado, escala tipográfica, sombras, radios, breakpoints y motion, para que el sitio tenga una apariencia profesional y coherente derivada de mis datos.

#### Criterios de aceptación

1. THE `theme-tokens.schema.json` SHALL definir una propiedad opcional `spacing` que describa la Spacing_Scale como un conjunto de pasos de espaciado nombrados.
2. THE `theme-tokens.schema.json` SHALL definir una propiedad opcional `typeScale` que describa la Type_Scale como un conjunto de tamaños de fuente con su altura de línea.
3. THE `theme-tokens.schema.json` SHALL definir propiedades opcionales `shadows`, `radii`, `breakpoints`, `motion` y `container` para sombras, radios, umbrales responsive, duraciones/curvas de animación y anchos máximos de contenido.
4. WHERE un documento Theme_Tokens omite una propiedad de Design_Tokens ampliada, THE Base_Layout SHALL aplicar el valor por defecto correspondiente del Design_System.
5. WHEN Puriq escribe o transforma un documento Theme_Tokens, THE Puriq SHALL aplicar Schema_Validation contra `theme-tokens.schema.json` antes de escribirlo.
6. IF un documento Theme_Tokens define un token de diseño ampliado con un tipo o formato que no cumple `theme-tokens.schema.json`, THEN THE Schema_Validation SHALL rechazar el documento y devolver un mensaje que identifique el token inválido.

### Requisito 2: Aplicación guiada por datos de los tokens como variables CSS

**Historia de usuario:** Como técnico de un gobierno local, quiero que todos los estilos del sitio provengan de mis tokens de marca, para que ningún color, tipografía o espaciado esté fijado en el código.

#### Criterios de aceptación

1. WHEN Base_Layout renderiza el documento, THE Base_Layout SHALL exponer los Design_Tokens de colores, tipografía, Spacing_Scale, Type_Scale, sombras, radios y motion como variables CSS mediante `define:vars`.
2. THE Base_Layout SHALL derivar los colores, las tipografías y el espaciado de los estilos globales exclusivamente de las variables CSS de Design_Tokens, sin valores de marca fijados en el código.
3. WHEN un UI_Component o un Content_Module aplica color, tipografía, espaciado, sombra o radio, THE UI_Component o Content_Module SHALL referenciar la variable CSS del Design_Token correspondiente.
4. WHERE Theme_Tokens define `colors.secondary` o `colors.accent`, THE Base_Layout SHALL exponer esos colores como variables CSS disponibles para todo el sitio.

### Requisito 3: Escala de espaciado y escala tipográfica consistentes

**Historia de usuario:** Como visitante del sitio, quiero una jerarquía visual y un ritmo vertical consistentes, para leer y navegar el contenido con comodidad.

#### Criterios de aceptación

1. THE Design_System SHALL aplicar la Type_Scale a los títulos y al texto de cuerpo de modo que cada nivel de encabezado tenga un tamaño distinto y ordenado de mayor a menor de `h1` a `h3`.
2. THE Design_System SHALL aplicar el espaciado vertical y horizontal entre secciones y elementos usando pasos de la Spacing_Scale.
3. WHEN Home_Page compone Landing_Section y Content_Module consecutivos, THE Home_Page SHALL separarlos con un espaciado tomado de la Spacing_Scale.
4. THE Base_Layout SHALL limitar el ancho del contenido principal al ancho de contenedor definido en Design_Tokens y centrarlo horizontalmente.

### Requisito 4: Biblioteca de componentes UI reutilizables

**Historia de usuario:** Como mantenedor de la Template, quiero componentes reutilizables (contenedor, sección, botón, tarjeta), para que la portada y los módulos compartan una apariencia coherente.

#### Criterios de aceptación

1. THE Design_System SHALL proveer un UI_Component de contenedor que aplique el ancho máximo y el relleno del Design_System.
2. THE Design_System SHALL proveer un UI_Component de botón que aplique los colores de marca, el radio y los estados de foco y hover del Design_System.
3. THE Design_System SHALL proveer un UI_Component de tarjeta que aplique el radio, la sombra y el relleno del Design_System.
4. WHEN un UI_Component recibe contenido mediante slots o props, THE UI_Component SHALL renderizar ese contenido conservando los estilos del Design_System.
5. THE UI_Component SHALL derivar toda su apariencia de las variables CSS de Design_Tokens, sin valores de marca fijados en el código.

### Requisito 5: Comportamiento responsive mobile-first

**Historia de usuario:** Como visitante en un teléfono, quiero que el sitio se vea y funcione bien en mi pantalla, para consultar la información turística desde cualquier dispositivo.

#### Criterios de aceptación

1. THE Design_System SHALL diseñar los estilos base para el ancho de viewport más pequeño y ampliarlos en los Breakpoint superiores (mobile-first).
2. WHEN el ancho del viewport es menor que el Breakpoint intermedio definido en Design_Tokens, THE header de Base_Layout SHALL presentar la navegación de forma accesible en pantalla estrecha sin desbordar el ancho del viewport.
3. WHEN un Content_Module o una Landing_Section muestra una cuadrícula de elementos, THE Content_Module o Landing_Section SHALL ajustar el número de columnas según los Breakpoint definidos en Design_Tokens.
4. THE Design_System SHALL evitar el desbordamiento horizontal del contenido en el ancho de viewport más pequeño soportado.
5. WHERE una imagen se muestra en un Content_Module o Landing_Section, THE Content_Module o Landing_Section SHALL escalar la imagen dentro de los límites de su contenedor.

### Requisito 6: Micro-interacciones y motion sobrio

**Historia de usuario:** Como visitante del sitio, quiero transiciones sutiles al interactuar con elementos, para percibir una experiencia pulida sin distracciones.

#### Criterios de aceptación

1. WHEN el usuario coloca el puntero sobre un elemento interactivo (enlace, botón, tarjeta) o le da foco, THE Design_System SHALL aplicar una transición visual usando las duraciones y curvas de motion de Design_Tokens.
2. IF el visitante tiene activada la preferencia Reduced_Motion, THEN THE Design_System SHALL reducir o anular las animaciones no esenciales.
3. THE Design_System SHALL limitar la duración de las transiciones a los valores de motion definidos en Design_Tokens.
4. WHERE Theme_Tokens omite los tokens de motion, THE Design_System SHALL usar las duraciones y curvas por defecto del Design_System.

### Requisito 7: Variantes de layout clásico y moderno diferenciadas

**Historia de usuario:** Como encargado de turismo, quiero elegir entre un estilo clásico y uno moderno, para que el sitio refleje la personalidad de mi provincia con dos estéticas realmente distintas.

#### Criterios de aceptación

1. WHERE `Site_Config.layout` es `clasico`, THE Base_Layout SHALL aplicar el conjunto de estilos de la Layout_Variant clásica.
2. WHERE `Site_Config.layout` es `moderno`, THE Base_Layout SHALL aplicar el conjunto de estilos de la Layout_Variant moderna.
3. THE Design_System SHALL diferenciar la Layout_Variant `clasico` de la `moderno` en al menos la composición del header, el tratamiento del Hero_Section y el estilo de las tarjetas.
4. THE Design_System SHALL derivar ambas Layout_Variant de los mismos Design_Tokens de marca, sin fijar colores ni tipografías en el código de ninguna variante.
5. WHERE `Site_Config` omite `layout`, THE Base_Layout SHALL aplicar la Layout_Variant por defecto del Design_System.

### Requisito 8: Accesibilidad como criterio de calidad

**Historia de usuario:** Como visitante que usa teclado o lector de pantalla, quiero un sitio accesible, para consultar la información turística sin barreras.

#### Criterios de aceptación

1. THE Design_System SHALL estructurar la portada y los módulos con elementos HTML semánticos (`header`, `nav`, `main`, `section`, `footer`, encabezados jerárquicos).
2. WHEN el usuario navega con el teclado, THE Design_System SHALL mostrar un indicador de foco visible en cada elemento interactivo.
3. THE Design_System SHALL mantener un Contrast_Ratio de al menos 4.5:1 entre el texto de cuerpo y su fondo usando los colores de Theme_Tokens.
4. WHEN una imagen aporta información en un Content_Module o Landing_Section, THE Content_Module o Landing_Section SHALL proveer un texto alternativo para esa imagen.
5. WHEN el Hero_Section superpone texto sobre una imagen de fondo, THE Hero_Section SHALL aplicar un overlay que preserve un Contrast_Ratio de al menos 4.5:1 entre el texto y el fondo.

### Requisito 9: Uplift visual de los módulos de contenido existentes

**Historia de usuario:** Como encargado de turismo, quiero que las secciones de lugares, mapa, eventos, blog y asistente luzcan tan cuidadas como la portada, para que todo el sitio se vea profesional.

#### Criterios de aceptación

1. WHEN un Content_Module renderiza fichas o listas, THE Content_Module SHALL usar los UI_Component del Design_System y las variables CSS de Design_Tokens en lugar de estilos fijados en el código.
2. THE Content_Module SHALL aplicar el espaciado de la Spacing_Scale y la Type_Scale a sus encabezados y contenido.
3. WHEN un Content_Module muestra una colección de elementos, THE Content_Module SHALL disponerlos en una cuadrícula responsive conforme al Requisito 5.
4. THE Content_Module SHALL conservar sus datos de entrada y su comportamiento funcional existentes tras aplicar el Design_System.

### Requisito 10: Composición ordenada de secciones de portada

**Historia de usuario:** Como encargado de turismo, quiero armar la portada con secciones que puedo activar y ordenar, para destacar lo más importante de mi destino.

#### Criterios de aceptación

1. WHEN Home_Page renderiza la portada, THE Landing_Module SHALL componer las Landing_Section marcadas como activas en el orden configurado, por encima de los Content_Module activos.
2. WHEN una Landing_Section está marcada como inactiva, THE Landing_Module SHALL omitir esa sección del render.
3. WHEN dos Landing_Section definen su posición, THE Landing_Module SHALL ordenarlas según el valor de orden configurado de menor a mayor.
4. THE Landing_Module SHALL restringir los tipos de Landing_Section al catálogo soportado (`hero`, `features`, `cta`, `gallery`, `stats`).
5. IF una Landing_Section referencia un tipo no soportado, THEN THE Landing_Module SHALL omitir esa sección sin interrumpir el render de las demás.

### Requisito 11: Hero de portada enriquecido

**Historia de usuario:** Como encargado de turismo, quiero un hero atractivo con imagen de fondo, titular y un botón de acción, para captar al visitante apenas entra al sitio.

#### Criterios de aceptación

1. WHEN el Hero_Section define una imagen o video de fondo, THE Hero_Section SHALL mostrar ese recurso como fondo de la sección.
2. WHEN el Hero_Section define un titular y un subtítulo, THE Hero_Section SHALL mostrar ambos textos sobre el fondo.
3. WHERE el Hero_Section define una llamada a la acción con etiqueta y destino, THE Hero_Section SHALL mostrar un UI_Component de botón que enlace a ese destino.
4. THE Hero_Section SHALL aplicar un overlay sobre el fondo conforme al criterio de contraste del Requisito 8.
5. WHERE el Hero_Section omite la imagen o el video de fondo, THE Hero_Section SHALL mostrar un fondo derivado de los colores de Theme_Tokens.

### Requisito 12: Catálogo de secciones de portada (Features, CTA, Galería, Stats)

**Historia de usuario:** Como encargado de turismo, quiero secciones de destacados, llamada a la acción, galería y estadísticas, para contar mi destino de forma completa y visual.

#### Criterios de aceptación

1. WHEN una Landing_Section de tipo `features` define una lista de destacados con título y descripción, THE Landing_Module SHALL renderizarlos en una cuadrícula responsive de UI_Component de tarjeta.
2. WHEN una Landing_Section de tipo `cta` define un mensaje y una llamada a la acción, THE Landing_Module SHALL renderizar el mensaje y un UI_Component de botón que enlace al destino configurado.
3. WHEN una Landing_Section de tipo `gallery` define una lista de imágenes, THE Landing_Module SHALL renderizarlas en una galería responsive con texto alternativo por imagen.
4. WHEN una Landing_Section de tipo `stats` define una lista de métricas con valor y etiqueta, THE Landing_Module SHALL renderizar cada métrica con su valor y su etiqueta.
5. WHERE una Landing_Section del catálogo omite sus elementos de contenido, THE Landing_Module SHALL omitir esa sección del render sin producir un error.

### Requisito 13: Persistencia y validación de la configuración de portada en Site_Config

**Historia de usuario:** Como encargado de turismo, quiero que mi configuración de portada quede guardada de forma confiable, para que el sitio se reconstruya siempre igual y sin errores.

#### Criterios de aceptación

1. THE `site-config.schema.json` SHALL definir una propiedad opcional `landing` que describa la lista de Landing_Section con su tipo, estado de activación, orden y campos de contenido por tipo.
2. WHEN Puriq o el Wizard_Server escribe la configuración de portada, THE sistema SHALL persistir las Landing_Section en `Site_Config.landing`.
3. WHEN Puriq o el Wizard_Server produce o transforma un Site_Config, THE sistema SHALL aplicar Schema_Validation contra `site-config.schema.json` antes de escribirlo o usarlo en el build.
4. IF la configuración de portada produce un Site_Config que no cumple `site-config.schema.json`, THEN THE Schema_Validation SHALL rechazar el guardado y devolver un mensaje que identifique el campo inválido.
5. WHERE `Site_Config` define tanto `hero` como una Landing_Section de tipo `hero` en `landing`, THE Build_Site SHALL usar la configuración de `landing` como fuente del Hero_Section.

### Requisito 14: Configuración de secciones de portada desde el Wizard

**Historia de usuario:** Como encargado de turismo no programador, quiero activar, ordenar y editar el texto de las secciones de portada desde formularios, para armar mi home sin tocar JSON ni código.

#### Criterios de aceptación

1. THE Wizard_UI SHALL presentar un paso de configuración de portada que liste las Landing_Section del catálogo soportado con controles para activarlas o desactivarlas.
2. WHEN el usuario reordena las Landing_Section en el Wizard_UI, THE Wizard_Server SHALL asignar a cada Landing_Section un valor de orden que refleje ese orden.
3. WHEN el usuario edita el texto de una Landing_Section (titular, subtítulo, destacados, mensaje, etiqueta de CTA), THE Wizard_Server SHALL escribir ese texto en la Landing_Section correspondiente de `Site_Config.landing`.
4. WHEN el usuario guarda la configuración de portada, THE Wizard_Server SHALL aplicar Schema_Validation al Site_Config contra `site-config.schema.json` antes de escribirlo.
5. WHERE existe un Site_Config previo con `landing`, THE Wizard_Server SHALL cargar las Landing_Section existentes en los campos del Wizard_UI al iniciar el paso.
6. THE Wizard_Server SHALL configurar las Landing_Section componiendo secciones pre-construidas del Landing_Module, sin generar ni modificar código de secciones.

### Requisito 15: Redacción del copy de las secciones con el LLM

**Historia de usuario:** Como encargado de turismo, quiero que Puriq redacte el texto de mis secciones de portada con el tono de mi marca, para tener una home persuasiva sin escribirla desde cero.

#### Criterios de aceptación

1. WHEN Generate_Content procesa una Landing_Section activa cuyo campo de copy está vacío, THE Generate_Content SHALL generar el texto usando el LLM_Provider a partir de Tourism_Data y del tipo de sección.
2. WHERE una Landing_Section ya tiene un copy no vacío, THE Generate_Content SHALL conservar el texto existente sin modificarlo.
3. WHEN Generate_Content construye el prompt de una Landing_Section, THE Generate_Content SHALL incluir el tono definido en `Theme_Tokens.voice.tone`.
4. IF la invocación al LLM_Provider falla para una Landing_Section, THEN THE Generate_Content SHALL conservar el valor existente de esa sección y registrar el fallo sin interrumpir el procesamiento de las demás secciones.
5. THE Generate_Content SHALL producir un Site_Config que cumpla el esquema `site-config.schema.json`.

### Requisito 16: Retrocompatibilidad y valores por defecto

**Historia de usuario:** Como encargado de turismo con un proyecto ya existente, quiero que mi sitio siga construyéndose sin errores tras la actualización, para no perder lo que ya tenía configurado.

#### Criterios de aceptación

1. WHERE un Site_Config existente omite la propiedad `landing`, THE Build_Site SHALL construir el sitio usando la portada por defecto sin producir un error.
2. WHERE un Theme_Tokens existente omite los Design_Tokens ampliados, THE Build_Site SHALL construir el sitio aplicando los valores por defecto del Design_System sin producir un error.
3. WHEN Build_Site procesa un Contrato de un proyecto anterior a este spec, THE Build_Site SHALL preservar el comportamiento funcional de los Content_Module existentes.
4. WHERE `Site_Config` define únicamente el `hero` heredado sin una lista `landing`, THE Landing_Module SHALL renderizar el Hero_Section a partir de la configuración `hero` heredada.
5. THE Build_Site SHALL producir un sitio válido cuando las Landing_Section y los Design_Tokens ampliados están ausentes, presentes de forma parcial o presentes en su totalidad.
