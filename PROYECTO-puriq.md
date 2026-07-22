# Puriq — Agente especializado para construir sitios turísticos de gobiernos locales

> Documento técnico de proyecto · Hackathon IA Masivo Código Facilito × AWS (Kiro + AWS) · Reto 3: Agentes especializados · Entrega: 27 de julio de 2026 (00:00 del 20 al 23:59 del 27, UTC-6)
> *"Puriq" es un nombre de trabajo (significa la tierra natal / lo local). Se puede cambiar. CLI de ejemplo: `puriq` / paquete `tourism-builder`.*

---

## 1. Resumen ejecutivo (entregables del hackathon)

**Título:** Puriq — Agente que convierte los recursos turísticos dispersos de un gobierno local en un sitio web profesional y mantenible.

**Descripción breve:** Puriq es un agente especializado que instala/ejecuta un asistente local. A partir de los recursos que un encargado de turismo ya tiene (fotos, lista de lugares, festivales, logo), y opcionalmente enriqueciendo con datos abiertos (OpenStreetMap, Wikidata, Wikimedia Commons), el agente redacta contenido con un LLM, ensambla módulos pre-construidos y probados (mapa, eventos, blog, chatbot), aplica la identidad visual de la provincia y genera un sitio turístico impecable, listo para previsualizar y publicar.

**Reto que resuelve:** Provincias rurales y gobiernos locales con bajo presupuesto tienen un patrimonio turístico valioso pero **ninguna presencia web profesional**, porque no pueden pagar una agencia ni mantener un CMS. Puriq elimina esa barrera: un encargado no programador genera y mantiene un sitio de calidad en minutos.

---

## 2. El problema (afilado, no genérico)

El tip del reto es explícito: *"Sé muy puntual con el problema… enfócate en un problema real."* Por eso el problema NO es "hacer webs turísticas" (genérico). Es:

> **Los gobiernos locales y provincias rurales tienen recursos turísticos valiosos y dispersos (fotos, lugares, festivales, historias) pero cero o pobre presencia web, porque contratar una agencia es caro y mantener un CMS requiere un perfil técnico que no tienen. Resultado: pierden visibilidad y turismo frente a destinos mejor posicionados digitalmente.**

La especialización del agente está en el **dominio (turismo gubernamental)** y en un **trabajo único y claro**: transformar recursos dispersos en un sitio profesional y mantenible. Aunque el resultado tenga varios módulos, la misión del agente es puntual.

**Casos ancla (datasets reales en `examples/`, mismo agente, distinta identidad):**

- **Potosí, Bolivia** (`examples/potosi-bo`) — **demo principal recomendado**. Autenticidad (el autor es boliviano → "problema real que tengas tú"), íconos mundiales (Salar de Uyuni, Cerro Rico UNESCO) y municipios subexplotados digitalmente.
- **Oaxaca, México** (`examples/oaxaca-mx`) — resuena con la sede del hackathon. Gancho: el programa federal **"Pueblos Mágicos"**; Puriq es la herramienta ideal para un municipio de ese programa. Bonus: la Guelaguetza cae en las fechas del evento (20–27 jul).
- **Jujuy, Argentina** (`examples/jujuy-ar`) — tercer ejemplo (Quebrada de Humahuaca UNESCO); refuerza el uso cross-país.

Narrativa para el video: *"el mismo agente, tres países, tres identidades"* — demuestra reutilización y escalabilidad (criterio Innovación).

---

## 3. La persona / usuario final

**Usuario primario: el Encargado de Turismo / representante provincial (o su admin de sistemas).**

- Tiene los recursos a mano (fotos, videos, artículos, lista de lugares, calendario de eventos) pero **dispersos y sin estructura**.
- Competente con tecnología (Excel, subir archivos, redes sociales) pero **no es programador** y no quiere serlo.
- **No lo usa una sola vez:** vuelve periódicamente para agregar un evento, cambiar el banner de temporada, subir fotos del último festival.

**Implicación de diseño clave:** el producto no es un generador de un solo tiro, es una herramienta a la que **se vuelve**. Por eso la edición segura y la actualización sin pisar cambios (sección 6) son el corazón de la experiencia, no un extra.

---

## 4. La solución y su propuesta de valor

Puriq se apoya en tres decisiones de diseño que lo hacen sólido, reutilizable y escalable:

1. **Módulos pre-construidos y probados** (no generación de código en caliente). El agente **compone y configura**, no improvisa infraestructura. Un bug se arregla una vez para todos.
2. **Arquitectura en capas con edición segura.** El usuario edita contenido, marca y estructura acotada; nunca toca el core. Las actualizaciones no pisan sus personalizaciones.
3. **El LLM trabaja sobre contenido y configuración, no sobre código.** Redacta descripciones, SEO y traducciones; decide qué módulos activar; nunca escribe el framework.

Valor para el gobierno local: de cero a un sitio profesional en internet en una sesión, mantenible por un no programador, sin costo de agencia.

---

## 5. Cómo cumple el Reto 3

| Requisito del reto | Cómo lo cumple Puriq |
|---|---|
| Ser un agente especializado | Especializado en turismo gubernamental; misión única y puntual. |
| Modelos locales, nube o combo | LLM en la nube vía **Amazon Bedrock** (Claude) para redacción/traducción; modo local opcional (Ollama) para el "combo". |
| Publicable para cualquiera | Distribuido vía `npx` / `pipx`; repo público; también expuesto como MCP. |
| Correr en terminal o UI | CLI (lanzador/headless) **y** wizard web local amigable. |
| Uso creativo de RAG, MCPs, datos externos | Chatbot web con **RAG gestionado por Amazon Bedrock Knowledge Bases** sobre Q&A del gobierno; **MCP** `tourism-builder`; **datos externos** desde OSM/Wikidata/Wikimedia Commons. |

### 5.1 Alineación con AWS y Kiro (aunque no son obligatorios)

El reglamento indica que Kiro y AWS son *recomendados, no obligatorios*, pero **el jurado de la Fase 2 es de AWS** y evalúa innovación, funcionalidad, calidad de documentación e impacto. Por eso apoyarnos en AWS suma puntos sin complicar el diseño:

- **Motor LLM:** Amazon Bedrock (Claude) — nube nativa AWS.
- **RAG del chatbot:** Amazon Bedrock Knowledge Bases (RAG gestionado) — el mayor golpe de alineación.
- **Hosting/deploy:** AWS Amplify Hosting o S3 + CloudFront.
- **Mapas/geocoding:** Amazon Location Service (opcional, junto a Leaflet+OSM).
- **Almacenamiento de assets:** Amazon S3.
- **IDE de desarrollo:** construir el proyecto **en Kiro** (spec-driven) y mostrarlo en el video — meta-relato potente: usar un agente para construir un agente especializado.

Sigue habiendo un modo "sin AWS" (Ollama local + deploy estático) para no atarnos, pero el camino por defecto es AWS.

### 5.2 Criterios de evaluación y cómo los atacamos

Ponderación oficial (fuente: bases del reto). **AWS/Kiro pesa solo 10%**; el 90% es problema, innovación y software. No sobre-invertir en AWS: basta ≥1 servicio (Bedrock + S3) para cubrir ese 10%.

| Criterio | Peso | Cómo lo atacamos |
|---|---|---|
| **Impacto tecnológico** | 30% | Problema real y específico (gobiernos locales rurales sin presencia web); alto valor social/económico; anclado a un caso real (Jujuy). |
| **Innovación** (eficiencia, escalabilidad, mantenibilidad vs alternativas) | 30% | Arquitectura en capas con edición segura + actualización sin pisar cambios; módulos componibles; el LLM configura, no genera infra. Ventaja clara frente a "web a medida" o CMS pesado. |
| **Software funcional + entregables** | 30% | Repo público + README + demo desplegado (sitio de Jujuy) + video ≤5 min. Diagramas de arquitectura y casos de uso (valorados, opcionales) incluidos en este doc. |
| **Uso de AWS y Kiro** | 10% | Bedrock (LLM), Bedrock Knowledge Bases (RAG), S3, Amplify/CloudFront, Location Service; desarrollado en Kiro. |

**Implicación estratégica:** el esfuerzo se concentra en (1) contar el problema como algo real y puntual, (2) demostrar la ventaja de la arquitectura (innovación), y (3) que el software funcione de verdad en el video. AWS es un complemento que suma, no el centro.

Criterios de evaluación (resolución del problema, innovación/impacto, calidad): el ancla real + la arquitectura sólida atacan los tres.

---

## 6. Arquitectura

### 6.1 Capas (de arriba = edita libremente, a abajo = bloqueado)

```
┌──────────────────────────────────────────────────────────────┐
│ CONTENIDO     tourism-data.json + /content (markdown) + assets │ ← el usuario edita siempre
│ MARCA / TEMA  theme.tokens.json (colores, tipografías, tono)   │ ← el usuario edita siempre
│ ESTRUCTURA    site.config.json (módulos on/off, orden, labels, │ ← edita, opciones acotadas
│               variante de layout: "clasico" | "moderno")       │
├──────────────────────────────────────────────────────────────┤
│ MÓDULOS CORE  /modules (map, events, blog, chatweb, ...)       │ ← NO se toca (bloqueado, versionado)
│ MOTOR / CORE  librería del agente (Python) + plantilla (Astro) │ ← NO se toca
└──────────────────────────────────────────────────────────────┘
```

**Regla de oro:** las ediciones del usuario viven en las capas de arriba (sus archivos). Los módulos core viven en `/modules`, versionados. Así `puriq update` actualiza el core **sin pisar** los colores, contenido y overrides del usuario. Esto es lo que convierte a Puriq en algo mantenible por años y no en un demo de un día.

**Edición de estructura/layout:** libertad **acotada, no infinita**. Se ofrecen *variantes* de layout y *slots* configurables (qué sección va primero, mostrar/ocultar), no un editor libre que rompa el diseño. **Escotilla de escape:** el sitio generado es un proyecto Astro normal que el usuario posee; un admin técnico puede editar el código directamente si necesita ir más allá.

### 6.2 Separación de responsabilidades (agente Python ↔ sitio web)

```
   Recursos del usuario ─┐
   + Datos abiertos      │        ┌───────────────────────┐        ┌──────────────────┐
   (OSM/Wikidata/Wiki)   ├──►  Agente (Python)  ──►  tourism-data.json   ──►  Plantilla (Astro)  ──►  Sitio estático
                         │     · escaneo/validación     site.config.json        lee datos+config         (preview y deploy)
   Q&A del gobierno ─────┘     · LLM: textos/SEO/i18n    theme.tokens.json       activa módulos
                              · geocoding, indexado RAG   (+ /assets, /content)   aplica tema
```

**El contrato son los tres JSON + assets.** El agente nunca genera HTML a mano: produce **datos limpios y validados**. La plantilla los consume. Cambiar la plantilla no toca al agente, y viceversa.

### 6.3 El core y sus tres interfaces

```
tourism-builder-core   ← toda la lógica (Python)
      ├── CLI            ← npx/pipx, wizard web local (interfaz primaria)
      ├── MCP server     ← mismas funciones como tools para Claude (envoltorio fino)
      └── modo headless  ← config por archivo, para CI o admins técnicos
```

Tools expuestas por el core (y por el MCP): `scan_resources`, `import_open_data`, `add_place`, `generate_content`, `build_site`, `preview`, `deploy`.

---

## 7. Catálogo de módulos

### MVP (debe funcionar en el video del 27)

| Módulo | Qué hace | Tecnología | Prioridad |
|---|---|---|---|
| **map** | Mapa interactivo de lugares turísticos con fichas | Leaflet + OpenStreetMap (sin API key) | Estrella |
| **places** | Directorio/fichas: foto, descripción, categoría, horario | Datos + plantilla | Base (alimenta map) |
| **events** | Calendario turístico con fechas clave | Datos JSON | Alta |
| **chatweb** | Chatbot que responde dudas del visitante (RAG sobre Q&A) | Embeddings + LLM | Alta (wow + encaje reto) |
| **blog** | Noticias/artículos desde markdown (sin panel admin aún) | Markdown | Media |

### Roadmap (mencionar como v2, no construir para el 27)

- **admin**: panel con login y roles editor/admin (CRUD de contenido).
- **i18n avanzado**: multi-idioma con auto-traducción por LLM (parcial ya en MVP para textos).
- **itineraries**: rutas turísticas sugeridas por IA.
- **reviews**: reseñas de visitantes.
- **bookings**: reservas/tickets con pasarela de pago.
- **analytics**: métricas de visitas para el gobierno.

---

## 8. Schemas (el contrato)

> Fuente de verdad. Validar con JSON Schema en `build`. Ejemplos con datos ilustrativos de Jujuy.

### 8.1 `tourism-data.json` — el contenido

```jsonc
{
  "$schema": "https://puriq.dev/schema/tourism-data.v1.json",
  "site": {
    "name": "Turismo Jujuy",
    "region": "Provincia de Jujuy, Argentina",
    "description": "Descubrí la Quebrada, la Puna y los Valles.",
    "defaultLocale": "es",
    "locales": ["es", "en"],
    "center": { "lat": -23.20, "lng": -65.35, "zoom": 8 }
  },
  "places": [
    {
      "id": "purmamarca-cerro-7-colores",
      "name": "Cerro de los Siete Colores",
      "category": "naturaleza",
      "coords": { "lat": -23.745, "lng": -65.500 },
      "address": "Purmamarca, Jujuy",
      "shortDescription": "Formación geológica icónica de la Quebrada.",
      "description": "",                // generado por LLM si está vacío
      "images": ["assets/purmamarca-01.jpg"],
      "hours": "Todo el día",
      "tags": ["quebrada", "fotografía", "senderismo"],
      "source": "user"                  // "user" | "osm" | "wikidata"
    }
  ],
  "events": [
    {
      "id": "carnaval-humahuaca-2026",
      "name": "Carnaval de la Quebrada",
      "startDate": "2026-02-14",
      "endDate": "2026-02-25",
      "placeId": "humahuaca-centro",
      "description": "",
      "images": [],
      "recurring": "yearly"
    }
  ],
  "categories": [
    { "id": "naturaleza", "label": "Naturaleza", "icon": "mountain" },
    { "id": "cultura", "label": "Cultura", "icon": "landmark" },
    { "id": "gastronomia", "label": "Gastronomía", "icon": "utensils" }
  ]
}
```

### 8.2 `site.config.json` — la estructura

```jsonc
{
  "$schema": "https://puriq.dev/schema/site-config.v1.json",
  "layout": "moderno",                 // "clasico" | "moderno"
  "modules": {
    "map":     { "enabled": true,  "order": 1, "label": "Mapa" },
    "places":  { "enabled": true,  "order": 2, "label": "Qué visitar" },
    "events":  { "enabled": true,  "order": 3, "label": "Eventos" },
    "chatweb": { "enabled": true,  "order": 4, "label": "Asistente",
                 "persona": "cálido y cercano",
                 "knowledgeSource": "content/faq/" },
    "blog":    { "enabled": false, "order": 5, "label": "Noticias" }
  },
  "hero": {
    "type": "image",                   // "image" | "video"
    "asset": "assets/hero-quebrada.jpg",
    "headline": "Jujuy te espera",
    "subheadline": "Colores, cultura y altura."
  },
  "contact": { "email": "turismo@jujuy.gob.ar", "phone": "" },
  "deploy": { "target": "vercel", "domain": "" }   // "vercel"|"netlify"|"static-export"
}
```

### 8.3 `theme.tokens.json` — la marca

```jsonc
{
  "$schema": "https://puriq.dev/schema/theme-tokens.v1.json",
  "colors": {
    "primary": "#C1440E",              // detectable desde el logo
    "secondary": "#F2A900",
    "background": "#FBF7F0",
    "text": "#2B2119",
    "accent": "#5B8C5A"
  },
  "typography": {
    "headingFont": "Playfair Display",
    "bodyFont": "Inter",
    "baseSize": "16px"
  },
  "voice": {
    "tone": "cálido y cercano",        // guía el LLM al redactar
    "formality": "informal"
  },
  "logo": "assets/logo-jujuy.svg",
  "radius": "12px"
}
```

---

## 9. Flujo del usuario (paso a paso, proyecto terminado)

1. **Preparar recursos.** El README/wizard da una checklist: carpeta de fotos, planilla/CSV de lugares (nombre, dirección, categoría), eventos con fechas, logo, y opcional un documento de preguntas frecuentes. *Reducir fricción aquí es el 80% de la UX.*
2. **Arrancar.** `npx tourism-builder init`. Se abre el navegador en el asistente local (`localhost:4321`). El terminal queda como lanzador y log.
3. **Onboarding guiado.** Preguntas esenciales sin jerga: nombre de la provincia, idiomas, objetivo.
4. **Elegir módulos.** Toggles claros (Mapa ✅, Eventos ✅, Blog ⬜, Chatbot ✅), cada uno con una frase de "para qué sirve".
5. **Cargar recursos.** Drag & drop de fotos, CSV de lugares/eventos, logo, y documento de Q&A. El wizard valida y sugiere qué falta para mejor resultado. Opción "enriquecer con datos abiertos" (OSM/Wikidata).
6. **Definir la marca.** Colores (detectados del logo o elegidos), tipografía y tono de voz, con preview instantáneo.
7. **Construir.** "Generar": el agente analiza, el LLM redacta descripciones/SEO y traduce, geocodifica direcciones, indexa el chatbot y ensambla los módulos con la marca. Progreso en vivo.
8. **Previsualizar.** El sitio real abre en `localhost`. Recorre el mapa, el calendario y prueba el chatbot con una pregunta real.
9. **Ajustar y reconstruir.** "Cambia este color / falta un lugar / edita este texto": ajusta en el wizard o edita el contenido, "Reconstruir" en segundos, sin tocar código.
10. **Publicar.** "Desplegar" (Vercel/Netlify) o "Exportar estático" para su TI. El sitio queda en internet.
11. **Volver a mantenerlo** (semanas después). Reabre, agrega el próximo festival o fotos nuevas, reconstruye y re-despliega. **Sus personalizaciones siguen intactas** porque viven en sus capas, no en el core.

El paso 11 es la prueba de que el problema se resolvió de verdad y no es un demo de un día.

---

## 10. Datos externos (enriquecimiento automático)

Fuentes abiertas que reducen la fricción y aportan el "uso de datos externos" del reto:

- **OpenStreetMap** (vía Overpass API): puntos de interés turístico de una región → pre-carga de `places` con coordenadas.
- **Wikidata**: metadatos de lugares (tipo, descripción, imagen destacada).
- **Wikimedia Commons**: fotos de libre uso de los lugares.
- **Geocoding** (Nominatim): direcciones del usuario → coordenadas para el mapa.

El usuario siempre revisa y aprueba lo importado (`source: "osm"`), manteniendo el control.

---

## 11. Stack técnico

- **Agente / core:** Python (orquestación de tools). LLM por defecto **Amazon Bedrock (Claude)**; modo local opcional (Ollama).
- **IDE de desarrollo:** **Kiro** (spec-driven), recomendado por el hackathon y mostrable en el video.
- **Wizard local:** servidor ligero (FastAPI) sirviendo una UI simple + WebSocket para progreso en vivo.
- **Sitio generado:** **Astro** (estático, SEO, poco JS, deploy trivial). Alternativa: Next.js si más adelante se necesita backend/admin dinámico (v2).
- **Mapa:** Leaflet + OpenStreetMap (sin API key ni costo); **Amazon Location Service** opcional para tiles/geocoding y sumar servicio AWS.
- **Chatbot RAG:** **Amazon Bedrock Knowledge Bases** (RAG gestionado) sobre los Q&A del gobierno; fallback local con almacén vectorial ligero (sqlite-vec/FAISS).
- **Almacenamiento:** **Amazon S3** para assets del sitio.
- **MCP:** servidor `tourism-builder` que expone las tools del core.
- **Deploy:** adaptadores `aws-amplify` | `s3-cloudfront` | `static-export` (y `vercel`/`netlify` como alternativas no-AWS).

---

## 12. Roadmap de 7 días (entrega 27 de julio)

| Día | Foco | Entregable |
|---|---|---|
| 1 (20 jul) | Setup + schemas | Cuenta/credenciales AWS + Bedrock habilitado + Kiro configurado; `tourism-data`, `site.config`, `theme.tokens` validables |
| 2 | Plantilla Astro con datos mock | Home + map (Leaflet) + places + events se ven impecables |
| 3 | Core Python: escaneo + validación | CSV/carpeta → `tourism-data.json` validado |
| 4 | LLM (Bedrock) + geocoding + RAG (Knowledge Bases) | Descripciones/SEO/i18n; direcciones→coords; chatweb responde |
| 5 | CLI + wizard local + build + deploy (Amplify/S3) | Sitio real de Jujuy end-to-end, publicado en AWS |
| 6 | Pulido visual, README, MCP (si alcanza) | Demo público desplegado; wrapper MCP |
| 7 | Video de 5 min + margen | Video final del equipo; buffer para imprevistos |

**Regla de scope:** el agente + un sitio de ejemplo funcionando en el video valen más que muchos módulos a medias.

---

## 13. Guía de puesta en producción (post-generación)

Aunque el deploy completo es fase 2, el wizard muestra al final "Tu sitio está listo. ¿Qué sigue?":

- **Opción A (recomendada, AWS):** AWS Amplify Hosting o S3 + CloudFront — HTTPS, CDN global, dominio propio; alineado con el jurado AWS.
- **Opción B:** **Export estático** → carpeta lista para que el área de TI la suba a su servidor provincial.
- **Opción C (alternativa no-AWS):** Vercel / Netlify — 1 clic, gratis, para quien no use AWS.
- **Dominio propio** (`turismo.provincia.gob`): pasos de Route 53 / DNS explicados en simple.

Patrón por adaptadores: agregar un destino nuevo es sumar un adaptador, no rehacer nada.

---

## 14. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Sonar "genérico" ante el jurado | Anclar a Jujuy con datos reales; misión puntual bien contada. |
| Alcance excesivo en 7 días | MVP acotado (5 módulos), deploy solo estático, admin a v2. |
| Generación de código inestable | Módulos pre-construidos; el LLM solo toca contenido/config. |
| Update pisa cambios del usuario | Separar capas de usuario vs core; `update` solo toca `/modules`. |
| Demo en vivo falla | Sitio pre-generado desplegado como respaldo del video. |
| Costos/privacidad de LLM | Amazon Bedrock bajo demanda + créditos AWS del evento; modo local (Ollama) opcional. |
| Curva de AWS/Kiro en 7 días | Empezar día 1 con credenciales AWS y Bedrock listos; usar servicios gestionados (Amplify, Knowledge Bases) para evitar infra manual. |

---

## 15. Checklist de entregables del 27

- [ ] Título y descripción breve (secciones 1–2 de este doc).
- [ ] Reto que resuelve (sección 2).
- [ ] Repo público en GitHub + README (basado en este doc).
- [ ] Enlace del demo en línea (sitio de Jujuy generado y desplegado).
- [ ] Video ≤ 5 min: objetivos, componentes principales, demo funcional. Un video por equipo.

---

## 16. Roadmap post-27

Panel admin con roles, multi-idioma completo por LLM, itinerarios IA, reseñas, reservas/pagos, analytics para el gobierno, más adaptadores de deploy (AWS/Azure/self-host asistido), y catálogo de plantillas/temas ampliable.

---

*Documento vivo. Próximo paso sugerido: convertir la sección 8 (schemas) en archivos reales y montar el esqueleto del repo.*
