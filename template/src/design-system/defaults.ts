// Capa de tokens y defaults del Design_System (DD-1).
//
// Este modulo es la unica fuente de verdad (lado Template) de los valores por
// defecto del sistema de diseno. `resolveTokens` fusiona esos defaults con el
// `theme.tokens.json` del usuario sin pisar lo que el usuario definio, de modo
// que un token ausente siempre toma su valor por defecto (Req 1.4, 6.4, 16.2).
// La misma tabla de defaults se replica en Python (`build_site._theme_to_css`).

// ---------------------------------------------------------------------------
// Tipos: tokens parciales (entrada del usuario) y tokens resueltos (completos)
// ---------------------------------------------------------------------------

/** Un paso de la Type_Scale: tamano de fuente y su altura de linea. */
export interface TypeScaleEntry {
  size: string;
  lineHeight: string;
}

/** Colores de marca resueltos: todos presentes. */
export interface ResolvedColors {
  primary: string;
  secondary: string;
  background: string;
  text: string;
  accent: string;
}

/** Tipografia resuelta: todas las familias y el tamano base presentes. */
export interface ResolvedTypography {
  headingFont: string;
  bodyFont: string;
  baseSize: string;
}

/** Escala de espaciado resuelta (pasos nombrados). */
export interface ResolvedSpacing {
  xs: string;
  sm: string;
  md: string;
  lg: string;
  xl: string;
  "2xl": string;
  [step: string]: string;
}

/** Escala tipografica resuelta (h1..h3, body, small). */
export interface ResolvedTypeScale {
  h1: TypeScaleEntry;
  h2: TypeScaleEntry;
  h3: TypeScaleEntry;
  body: TypeScaleEntry;
  small: TypeScaleEntry;
  [level: string]: TypeScaleEntry;
}

/** Sombras resueltas. */
export interface ResolvedShadows {
  sm: string;
  md: string;
  lg: string;
  [name: string]: string;
}

/** Radios resueltos. */
export interface ResolvedRadii {
  sm: string;
  md: string;
  lg: string;
  pill: string;
  [name: string]: string;
}

/** Breakpoints resueltos. */
export interface ResolvedBreakpoints {
  sm: string;
  md: string;
  lg: string;
  [name: string]: string;
}

/** Tokens de motion resueltos: duraciones y curva. */
export interface ResolvedMotion {
  durationFast: string;
  durationBase: string;
  easing: string;
}

/** Anchos maximos de contenedor resueltos. */
export interface ResolvedContainer {
  sm: string;
  md: string;
  lg: string;
  xl: string;
  [name: string]: string;
}

/**
 * Conjunto completo de Design_Tokens tras aplicar los defaults. Es el insumo de
 * `tokensToCssVars` y del `theme.css` materializado; garantiza que el conjunto
 * de variables CSS emitidas sea estable independientemente de cuantos tokens
 * definio el usuario (Req 16.5).
 */
export interface ResolvedTokens {
  colors: ResolvedColors;
  typography: ResolvedTypography;
  spacing: ResolvedSpacing;
  typeScale: ResolvedTypeScale;
  shadows: ResolvedShadows;
  radii: ResolvedRadii;
  breakpoints: ResolvedBreakpoints;
  motion: ResolvedMotion;
  container: ResolvedContainer;
  /** Radio unico heredado (`theme.tokens.json.radius`), retrocompatible. */
  radius: string;
}

/** Un paso de Type_Scale tal como puede venir parcial del usuario. */
export interface PartialTypeScaleEntry {
  size?: string;
  lineHeight?: string;
}

/**
 * Forma del `theme.tokens.json` del usuario: todos los tokens ampliados son
 * opcionales (Req 1.1-1.3) y los objetos pueden venir parciales. Refleja el
 * esquema `theme-tokens.schema.json` extendido de forma aditiva (DD-2).
 */
export interface ThemeTokens {
  colors?: Partial<ResolvedColors>;
  typography?: Partial<ResolvedTypography>;
  spacing?: Record<string, string>;
  typeScale?: Record<string, PartialTypeScaleEntry>;
  shadows?: Record<string, string>;
  radii?: Record<string, string>;
  breakpoints?: Record<string, string>;
  motion?: Partial<ResolvedMotion>;
  container?: Record<string, string>;
  radius?: string;
  // Propiedades de marca no relacionadas con el render (voz, logo) se conservan
  // pero no participan en la resolucion de tokens visuales.
  [extra: string]: unknown;
}

// ---------------------------------------------------------------------------
// Tabla de defaults del Design_System
// ---------------------------------------------------------------------------

/**
 * Tabla completa de tokens del sistema. Los valores de las escalas ampliadas
 * (spacing, typeScale, shadows, radii, breakpoints, motion, container) coinciden
 * exactamente con la seccion Data Models del diseno. Los colores, la tipografia
 * y el `radius` son defaults neutros y sensatos, coherentes con las variables
 * que `Base.astro` ya expone; en un contrato valido siempre se sobreescriben con
 * la marca del usuario, pero garantizan un `ResolvedTokens` completo.
 */
export const DESIGN_DEFAULTS: ResolvedTokens = {
  colors: {
    primary: "#1f2933",
    secondary: "#52606d",
    background: "#ffffff",
    text: "#1f2933",
    accent: "#2563eb",
  },
  typography: {
    headingFont: "Georgia",
    bodyFont: "system-ui",
    baseSize: "16px",
  },
  spacing: {
    xs: "0.25rem",
    sm: "0.5rem",
    md: "1rem",
    lg: "2rem",
    xl: "4rem",
    "2xl": "8rem",
  },
  typeScale: {
    h1: { size: "2.5rem", lineHeight: "1.15" },
    h2: { size: "2rem", lineHeight: "1.25" },
    h3: { size: "1.5rem", lineHeight: "1.3" },
    body: { size: "1rem", lineHeight: "1.6" },
    small: { size: "0.875rem", lineHeight: "1.5" },
  },
  shadows: {
    sm: "0 1px 2px rgba(0,0,0,.08)",
    md: "0 4px 12px rgba(0,0,0,.12)",
    lg: "0 12px 32px rgba(0,0,0,.18)",
  },
  radii: {
    sm: "4px",
    md: "8px",
    lg: "16px",
    pill: "999px",
  },
  breakpoints: {
    sm: "640px",
    md: "768px",
    lg: "1024px",
  },
  motion: {
    durationFast: "120ms",
    durationBase: "240ms",
    easing: "cubic-bezier(.4,0,.2,1)",
  },
  container: {
    sm: "640px",
    md: "768px",
    lg: "1080px",
    xl: "1280px",
  },
  radius: "12px",
};

// ---------------------------------------------------------------------------
// resolveTokens: merge puro e idempotente de defaults + tokens del usuario
// ---------------------------------------------------------------------------

type PlainObject = Record<string, unknown>;

function isPlainObject(value: unknown): value is PlainObject {
  return (
    typeof value === "object" && value !== null && !Array.isArray(value)
  );
}

/**
 * Fusion profunda pura: devuelve un objeto nuevo donde cada valor definido en
 * `override` gana sobre `base`, y los ausentes conservan `base`. No muta ninguno
 * de los argumentos. Los valores `undefined` de `override` se ignoran (se trata
 * como "ausente" para preservar el default correspondiente).
 */
function deepMerge<T>(base: T, override: unknown): T {
  if (override === undefined) return base;
  if (isPlainObject(base) && isPlainObject(override)) {
    const result: PlainObject = { ...base };
    for (const key of Object.keys(override)) {
      const overrideValue = override[key];
      if (overrideValue === undefined) continue;
      result[key] =
        key in base ? deepMerge((base as PlainObject)[key], overrideValue) : overrideValue;
    }
    return result as T;
  }
  return override as T;
}

/**
 * Fusiona `DESIGN_DEFAULTS` con el `theme` del usuario sin pisar lo que el
 * usuario definio. Todo token ausente (incluido motion) toma su default.
 *
 * Propiedades garantizadas:
 * - Pura: no muta `theme` ni `DESIGN_DEFAULTS`.
 * - Completa: el resultado tiene todos los tokens del sistema (Req 1.4, 6.4, 16.2).
 * - Idempotente: `resolveTokens(resolveTokens(t))` es igual a `resolveTokens(t)`
 *   (Req 1.4, 16.5), porque los valores ya presentes nunca se sobreescriben.
 */
export function resolveTokens(theme: Partial<ThemeTokens> = {}): ResolvedTokens {
  return deepMerge(DESIGN_DEFAULTS, theme ?? {});
}

// ---------------------------------------------------------------------------
// tokensToCssVars: aplanado de los tokens resueltos a variables CSS
// ---------------------------------------------------------------------------

/**
 * Nombres de las variables CSS que emite cada token de motion. El tipo
 * `ResolvedMotion` fija las tres claves (`durationFast`/`durationBase`/`easing`),
 * por lo que el mapeo es explicito en vez de derivarse de la clave. Coincide con
 * el `_MOTION_VAR` del emisor Python (`build_site._theme_to_css`).
 */
const MOTION_VAR: Record<keyof ResolvedMotion, string> = {
  durationFast: "--motion-duration-fast",
  durationBase: "--motion-duration-base",
  easing: "--motion-easing",
};

/**
 * Aplana un conjunto de `ResolvedTokens` a un mapa `Record<string,string>` de
 * variables CSS, listo para `define:vars` en `Base.astro`. Los NOMBRES de las
 * variables coinciden exactamente con los que emite el lado Python
 * (`build_site._theme_to_css`), de modo que `define:vars` y el `theme.css`
 * materializado exponen el mismo conjunto de variables (unica fuente de verdad
 * de nombres, documentada en el diseno).
 *
 * Variables emitidas:
 * - colores    -> `--color-primary`, `--color-secondary`, `--color-background`,
 *   `--color-text`, `--color-accent` (incluidos `secondary` y `accent`).
 * - tipografia -> `--font-heading`, `--font-body`, `--font-base-size`.
 * - spacing    -> `--space-<paso>` (xs, sm, md, lg, xl, 2xl, ...).
 * - typeScale  -> `--fs-<nivel>` (size) y `--lh-<nivel>` (lineHeight).
 * - shadows    -> `--shadow-<nombre>`.
 * - radii      -> `--radius-<nombre>`.
 * - breakpoints-> `--bp-<nombre>`.
 * - motion     -> `--motion-duration-fast`, `--motion-duration-base`,
 *   `--motion-easing`.
 * - container  -> `--container-<nombre>`.
 * - `--radius` para el token heredado unico (`theme.tokens.json.radius`).
 *
 * Es una funcion pura: no muta `tokens` ni depende de estado externo (Req 2.1,
 * 2.4). Como opera sobre `ResolvedTokens` (todos los tokens presentes), el
 * conjunto de variables emitidas es estable sin importar cuantos tokens definio
 * el usuario.
 */
export function tokensToCssVars(tokens: ResolvedTokens): Record<string, string> {
  const vars: Record<string, string> = {};

  // Colores de marca -> --color-<clave> (incluidos secondary y accent).
  for (const key of ["primary", "secondary", "background", "text", "accent"] as const) {
    vars[`--color-${key}`] = tokens.colors[key];
  }

  // Tipografia -> --font-heading/-body y --font-base-size.
  vars["--font-heading"] = tokens.typography.headingFont;
  vars["--font-body"] = tokens.typography.bodyFont;
  vars["--font-base-size"] = tokens.typography.baseSize;

  // Spacing_Scale -> --space-<paso>.
  for (const [step, value] of Object.entries(tokens.spacing)) {
    vars[`--space-${step}`] = value;
  }

  // Type_Scale -> --fs-<nivel> (size) y --lh-<nivel> (lineHeight).
  for (const [level, entry] of Object.entries(tokens.typeScale)) {
    vars[`--fs-${level}`] = entry.size;
    vars[`--lh-${level}`] = entry.lineHeight;
  }

  // Sombras -> --shadow-<nombre>.
  for (const [name, value] of Object.entries(tokens.shadows)) {
    vars[`--shadow-${name}`] = value;
  }

  // Radios -> --radius-<nombre>.
  for (const [name, value] of Object.entries(tokens.radii)) {
    vars[`--radius-${name}`] = value;
  }

  // Breakpoints -> --bp-<nombre>.
  for (const [name, value] of Object.entries(tokens.breakpoints)) {
    vars[`--bp-${name}`] = value;
  }

  // Motion -> --motion-duration-fast/-base y --motion-easing.
  for (const key of Object.keys(MOTION_VAR) as (keyof ResolvedMotion)[]) {
    vars[MOTION_VAR[key]] = tokens.motion[key];
  }

  // Anchos de contenedor -> --container-<nombre>.
  for (const [name, value] of Object.entries(tokens.container)) {
    vars[`--container-${name}`] = value;
  }

  // Radio de esquinas heredado (token unico opcional).
  vars["--radius"] = tokens.radius;

  return vars;
}
