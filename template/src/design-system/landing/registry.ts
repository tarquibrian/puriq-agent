// Landing_Module: section registry (DD-3, Req 10.1, 10.4, 10.5).
//
// Mapea cada `LandingType` del catalogo a su componente Astro pre-construido.
// `index.astro` itera las secciones resueltas y renderiza cada una via este
// registro; un `type` ausente del registro se omite con gracia (Req 10.5).
import type { LandingType } from "./resolve";

import Hero from "./Hero.astro";
import Features from "./Features.astro";
import Cta from "./Cta.astro";
import Gallery from "./Gallery.astro";
import Stats from "./Stats.astro";

/**
 * Registro `type -> componente Astro`. El valor es un componente Astro
 * (tipado de forma laxa porque Astro no exporta un tipo publico estable para
 * componentes importados). Cubre exactamente el catalogo soportado.
 */
export const SECTION_REGISTRY: Record<LandingType, unknown> = {
  hero: Hero,
  features: Features,
  cta: Cta,
  gallery: Gallery,
  stats: Stats,
};
