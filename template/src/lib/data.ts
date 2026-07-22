// Carga del contrato en build. El agente inyecta estos JSON antes de `astro build`.
// Durante el desarrollo de la plantilla se usan los ejemplos de /examples.
import tourism from "../data/tourism-data.json";
import config from "../data/site.config.json";
import theme from "../data/theme.tokens.json";
// Base de conocimiento Q&A del chatweb. El agente la inyecta en build desde
// `content/` (build_site._inject_faq). Se versiona un `faq.json` con `[]` en la
// Template para que `astro build` de la plantilla desnuda no falle al resolver
// este import (el agente lo sobrescribe con el Q&A real del proyecto).
import faqData from "../data/faq.json";

export type Coords = { lat: number; lng: number; zoom?: number };
export type Place = {
  id: string; name: string; category: string; coords: Coords;
  address?: string; shortDescription?: string; description?: string;
  images?: string[]; hours?: string; tags?: string[]; source?: string;
};
export type EventItem = {
  id: string; name: string; startDate: string; endDate?: string;
  placeId?: string; description?: string; images?: string[]; recurring?: string;
};

export const site = (tourism as any).site;
export const places: Place[] = (tourism as any).places ?? [];
export const events: EventItem[] = (tourism as any).events ?? [];
export const categories = (tourism as any).categories ?? [];
export const siteConfig = config as any;
export const themeTokens = theme as any;

export type FaqEntry = { question: string; answer: string };
/** Q&A que alimenta la recuperacion client-side del asistente (modulo chatweb). */
export const faq: FaqEntry[] = (faqData as any) ?? [];

/** Modulos activos ordenados por `order`, respetando site.config. */
export function activeModules(): { key: string; label: string }[] {
  const mods = siteConfig.modules ?? {};
  return Object.entries(mods)
    .filter(([, m]: any) => m.enabled)
    .sort((a: any, b: any) => a[1].order - b[1].order)
    .map(([key, m]: any) => ({ key, label: m.label ?? key }));
}
