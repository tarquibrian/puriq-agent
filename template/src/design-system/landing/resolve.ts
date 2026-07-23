// Landing_Module: resolucion y composicion de la portada (DD-3).
//
// `resolveLanding` es una funcion PURA que toma el `site.config.json` y devuelve
// la lista ORDENADA de secciones a renderizar (`ResolvedSection[]`). Aplica la
// precedencia del Hero_Section entre `landing` y el `hero` heredado, filtra las
// secciones activas cuyo `type` pertenece al catalogo soportado y las ordena por
// `order` ascendente.
//
// Invariantes (Req 16.1, 16.5): nunca lanza una excepcion ante una configuracion
// ausente, parcial o completa; sin I/O ni estado externo.

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

/** Catalogo soportado de tipos de Landing_Section (Req 10.4). */
export type LandingType =
  | "hero"
  | "features"
  | "cta"
  | "gallery"
  | "stats"
  | "testimonials"
  | "faq";

/** Conjunto de tipos del catalogo, usado para filtrar (Req 10.4, 10.5). */
export const LANDING_CATALOG: readonly LandingType[] = [
  "hero",
  "features",
  "cta",
  "gallery",
  "stats",
  "testimonials",
  "faq",
];

/**
 * Una Landing_Section tal como vive en `Site_Config.landing`. El `content` es un
 * objeto abierto (el esquema no acopla su forma interna); cada componente de
 * seccion lee sus campos de forma defensiva.
 */
export interface LandingSection {
  type: string;
  enabled: boolean;
  order: number;
  content?: Record<string, unknown>;
}

/** Forma del `hero` heredado (`Site_Config.hero`), retrocompatible. */
export interface LegacyHero {
  type?: string;
  asset?: string;
  headline?: string;
  subheadline?: string;
}

/** Subconjunto del `Site_Config` que consume `resolveLanding`. */
export interface SiteConfig {
  landing?: LandingSection[];
  hero?: LegacyHero;
  [extra: string]: unknown;
}

/** Seccion ya resuelta y lista para renderizar via `SECTION_REGISTRY`. */
export interface ResolvedSection {
  type: LandingType;
  content: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** True si `type` pertenece al catalogo soportado. */
function isLandingType(type: unknown): type is LandingType {
  return (
    typeof type === "string" &&
    (LANDING_CATALOG as readonly string[]).includes(type)
  );
}

/**
 * Sintetiza un Hero_Section a partir del `hero` heredado (Req 16.4). Mapea
 * `hero.type`/`hero.asset` al `background` del Hero_Section y conserva el
 * titular/subtitulo. Devuelve `null` si el hero heredado no aporta contenido.
 */
function heroFromLegacy(hero: LegacyHero | undefined): ResolvedSection | null {
  if (!hero || typeof hero !== "object") return null;

  const content: Record<string, unknown> = {};
  if (typeof hero.headline === "string") content.headline = hero.headline;
  if (typeof hero.subheadline === "string") content.subheadline = hero.subheadline;
  // El recurso de fondo solo se mapea cuando hay `asset`; el tipo por defecto es
  // imagen (coherente con el render defensivo del Hero_Section).
  if (typeof hero.asset === "string" && hero.asset.trim()) {
    content.background = { type: hero.type ?? "image", asset: hero.asset };
  }

  // Sin ningun campo util no vale la pena sintetizar una seccion.
  if (Object.keys(content).length === 0) return null;
  return { type: "hero", content };
}

// ---------------------------------------------------------------------------
// resolveLanding
// ---------------------------------------------------------------------------

/**
 * Resuelve la lista ordenada de secciones de portada a renderizar.
 *
 * Precedencia del Hero_Section (DD-3):
 * - `landing` ausente + `hero` heredado presente  => se sintetiza un Hero_Section
 *   a partir del `hero` heredado (Req 16.4).
 * - `landing` presente (aunque contenga un hero) + `hero` heredado presente
 *   => gana `landing`; el `hero` heredado se ignora (Req 13.5).
 * - `landing` presente sin hero => se usa tal cual (no se sintetiza; respeta la
 *   intencion explicita del usuario).
 *
 * Filtrado (Req 10.1-10.4): se conservan solo las secciones con `enabled === true`
 * cuyo `type` pertenece al catalogo soportado; se ordenan por `order` ascendente.
 * Los tipos no soportados se omiten sin afectar al resto (Req 10.5). El render
 * defensivo de cada componente omite las secciones con contenido esencial vacio
 * (Req 12.5), por lo que aqui basta con filtrar `enabled` + catalogo.
 *
 * Nunca lanza: ante una config ausente, parcial o invalida devuelve el mejor
 * resultado posible (posiblemente `[]`) sin error (Req 16.1, 16.5).
 */
export function resolveLanding(config: SiteConfig | null | undefined): ResolvedSection[] {
  const cfg = config ?? {};

  // Caso retrocompatible: sin `landing`, se sintetiza el Hero_Section desde el
  // `hero` heredado si aporta contenido; si no, portada vacia (Req 16.1, 16.4).
  if (!Array.isArray(cfg.landing)) {
    const legacyHero = heroFromLegacy(cfg.hero);
    return legacyHero ? [legacyHero] : [];
  }

  // Caso `landing` presente: gana sobre el hero heredado (Req 13.5). Filtramos
  // secciones activas del catalogo y ordenamos por `order` ascendente.
  return cfg.landing
    .filter(
      (section): section is LandingSection & { type: LandingType } =>
        Boolean(section) &&
        section.enabled === true &&
        isLandingType(section.type)
    )
    .slice() // copia defensiva: no mutar el array de entrada al ordenar
    .sort((a, b) => a.order - b.order)
    .map((section) => ({
      type: section.type,
      content: (section.content ?? {}) as Record<string, unknown>,
    }));
}
