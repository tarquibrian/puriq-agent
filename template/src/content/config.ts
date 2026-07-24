import { defineCollection, z } from "astro:content";

// Coleccion `blog`: articulos del sitio.
//
// Se versiona en la plantilla (antes solo la creaba el agente en su build) para
// que `astro build` de la plantilla desnuda no emita el warning "The collection
// blog does not exist" y para que el CI la construya limpia. El agente sigue
// sobrescribiendo este archivo y llenando `src/content/blog/` con los `.md` del
// proyecto (build_site._inject_articles); esta version es la de la plantilla sin
// contenido, con la coleccion vacia.
//
// El schema refleja el frontmatter de un Article; `date` se coacciona a Date
// para poder ordenar por fecha descendente. DEBE coincidir con el que emite
// `build_site._CONTENT_COLLECTION_CONFIG`.
const blog = defineCollection({
  type: "content",
  schema: z.object({
    id: z.string().optional(),
    title: z.string(),
    date: z.coerce.date(),
    tags: z.array(z.string()).optional(),
    category: z.string().optional(),
    summary: z.string().optional(),
  }),
});

export const collections = { blog };
