# Puriq

**Agente especializado que convierte los recursos turísticos dispersos de un gobierno local en un sitio web profesional y mantenible.**

> Hackathon IA Masivo Código Facilito × AWS (Kiro + AWS) · Reto 3: Agentes especializados
> Nombre de trabajo — editable. CLI: `puriq` · MCP: `tourism-builder`

## El problema

Las provincias rurales y gobiernos locales con bajo presupuesto tienen un patrimonio turístico valioso (lugares, festivales, fotos, historias) pero **ninguna presencia web profesional**: contratar una agencia es caro y mantener un CMS exige un perfil técnico que no tienen. Resultado: pierden visibilidad y turismo frente a destinos mejor posicionados digitalmente.

## La solución

Puriq instala/ejecuta un asistente local. A partir de los recursos que el encargado de turismo ya tiene —y opcionalmente enriqueciendo con datos abiertos (OpenStreetMap, Wikidata, Wikimedia Commons)— el agente:

1. **Recopila y estructura** los recursos (fotos, lugares, eventos, logo, Q&A).
2. **Redacta contenido** con un LLM (Amazon Bedrock): descripciones, SEO, traducciones.
3. **Ensambla módulos** pre-construidos y probados (mapa, lugares, eventos, blog, chatbot).
4. **Aplica la identidad visual** de la provincia (colores, tipografías, tono).
5. **Previsualiza y publica** el sitio (AWS Amplify / S3+CloudFront / export estático).

El agente **no escribe el código de los módulos**: compone y configura bloques probados. El LLM trabaja sobre contenido y configuración, nunca sobre infraestructura. Eso lo hace sólido, escalable y mantenible.

## Cómo se usa

```bash
# Agente (Python)
cd agent && pip install -e ".[local,mcp]"
puriq init                                   # wizard web local amigable (no-técnicos)
puriq build   --project ../examples/potosi-bo # build headless (mismo agente, cualquier región)
puriq preview --project ../examples/potosi-bo # previsualizar
puriq deploy  --project ../examples/potosi-bo --target aws-amplify
```

El usuario final (encargado de turismo) usa el **wizard web**; un admin técnico puede usar el modo headless o el **MCP** desde Claude.

## Arquitectura en capas (edición segura + actualizable)

```
CONTENIDO   tourism-data.json + /content + assets   <- el usuario edita siempre
MARCA       theme.tokens.json                        <- el usuario edita siempre
ESTRUCTURA  site.config.json (módulos, orden, hero)  <- opciones acotadas
---------------------------------------------------
MÓDULOS     /modules (map, places, events, blog, chatweb)  <- core, no se toca
```

Las ediciones del usuario viven en las capas de arriba; `puriq update` actualiza el core **sin pisar** sus personalizaciones. El contrato entre agente y sitio son los tres JSON (ver `/schemas`, validados en cada build).

## Estructura del repo

```
agent/          Core del agente (Python): CLI, wizard, MCP, tools
schemas/        Contrato: JSON Schema de los 3 documentos
examples/       Datasets de ejemplo multi-región: potosi-bo, oaxaca-mx, jujuy-ar (+ Q&A)
template/       Plantilla Astro que consume el contrato y compone módulos
docs/           Documentación adicional
PROYECTO-puriq.md   Documento técnico completo (diseño y roadmap)
```

## Uso de AWS y Kiro

- **Amazon Bedrock** — motor LLM (redacción, SEO, traducción).
- **Amazon Bedrock Knowledge Bases** — RAG del chatbot sobre los Q&A del gobierno.
- **AWS Amplify Hosting / S3 + CloudFront** — publicación del sitio.
- **Amazon S3** — almacenamiento de assets.
- **Amazon Location Service** — geocoding/tiles (opcional).
- **Kiro** — IDE spec-driven usado para construir el propio agente.

Existe un modo sin AWS (LLM local con Ollama + export estático) para no crear dependencia dura.

## Estado

Scaffolding (esqueleto) con contrato, ejemplos validados y plantilla componible. Ver el roadmap de 7 días en `PROYECTO-puriq.md`.

## Licencia

MIT (propuesta).
