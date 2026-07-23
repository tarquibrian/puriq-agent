// Barrel del Landing_Module: expone la resolucion pura y el section registry
// para que `index.astro` importe todo desde un unico punto.
export { resolveLanding, LANDING_CATALOG } from "./resolve";
export type {
  LandingType,
  LandingSection,
  LegacyHero,
  SiteConfig,
  ResolvedSection,
} from "./resolve";
export { SECTION_REGISTRY } from "./registry";
