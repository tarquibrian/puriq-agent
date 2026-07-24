# Fuentes del catálogo tipográfico

Archivos `.woff2` auto-hospedados que consume el Design_System. El catálogo de
familias, sus pesos y sus pilas de respaldo viven en
`template/src/design-system/fonts.ts`.

## Por qué auto-hospedadas

El sitio **nunca** pide las fuentes a un CDN de terceros (Google Fonts u otro).
Dos razones:

1. **Privacidad.** Un sitio de un gobierno local no debería filtrar la IP de sus
   visitantes a un tercero solo para pintar un titular.
2. **Disponibilidad.** Así el sitio se ve correcto en una intranet o con
   conectividad intermitente, que es el escenario real de muchas provincias.

Cada `@font-face` intenta primero `local(...)`: si el visitante ya tiene la
familia instalada, no se descarga nada.

## Convención de nombres

| Tipo de familia | Archivo | Ejemplo |
|---|---|---|
| Variable (un archivo cubre todo el rango de pesos) | `<slug>-var.woff2` | `playfair-display-var.woff2` |
| Estática (un archivo por peso) | `<slug>-<peso>.woff2` | `poppins-600.woff2` |

El `<slug>` es el nombre de la familia en minúsculas, sin acentos y con guiones
(`"Playfair Display"` → `playfair-display`).

Un archivo que no exista simplemente no genera su `@font-face`: el sitio usa la
pila de respaldo del catálogo y no dispara peticiones fallidas.

## Aportar una tipografía propia

Un proyecto puede traer su tipografía institucional dejando los `.woff2` en
`<proyecto>/fonts/` con esta misma convención. El build los copia y **pisan** a
los del catálogo. Al publicar, solo se conservan los archivos de las familias
declaradas en `theme.tokens.json`; el resto se poda.

## Licencias

Las fuentes incluidas están bajo la **SIL Open Font License 1.1**, que permite
usarlas, modificarlas y redistribuirlas —incluso auto-hospedadas— conservando el
aviso de copyright y la licencia. Texto completo: <https://openfontlicense.org>

| Familia | Copyright | Origen |
|---|---|---|
| Playfair Display | Copyright (c) Claus Eggers Sørensen | Google Fonts |
| Inter | Copyright (c) The Inter Project Authors | Google Fonts |
| Poppins | Copyright (c) Indian Type Foundry, Jonny Pinhorn | Google Fonts |

Los archivos son el subset **latino**, suficiente para castellano (incluye
`á é í ó ú ñ ü ¿ ¡`). Si un proyecto necesita otro alfabeto, hay que reemplazar
el archivo por uno con el subset correspondiente.
