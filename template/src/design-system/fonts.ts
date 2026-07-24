// Capa de tipografia del Design_System: pilas de respaldo y auto-hospedaje.
//
// PROBLEMA QUE RESUELVE
// `theme.tokens.json` declara la marca tipografica con un nombre de familia
// (`"Playfair Display"`, `"Poppins"`, ...), pero un nombre por si solo no pinta
// nada: si la familia no esta instalada ni cargada, el navegador cae a la
// siguiente de la pila. Con la pila anterior (`var(--font-heading), Georgia,
// serif`) una marca que pedia Playfair terminaba renderizando el serif por
// defecto, y Potosi, Oaxaca y Jujuy se veian tipograficamente IGUALES pese a
// declarar familias distintas.
//
// ESTRATEGIA (en orden de preferencia, sin depender de red externa)
//   1. `local(...)`  -> si el visitante tiene la familia instalada, se usa sin
//      descargar nada.
//   2. archivo auto-hospedado en `/fonts/<archivo>.woff2` -> lo sirve el propio
//      sitio. Nunca se pide a un CDN de terceros: un sitio de gobierno no
//      deberia filtrar la IP de sus visitantes a un tercero, y ademas asi el
//      sitio funciona en una intranet sin salida a internet.
//   3. PILA DE RESPALDO por familia -> si no hay archivo, se cae a fuentes del
//      sistema elegidas para PARECERSE a la familia pedida (una display serif
//      cae a Palatino/Georgia, una geometrica cae a Avenir/Century Gothic), no
//      al generico `serif`/`sans-serif`. Es lo que hace que el sitio siga
//      viendose deliberado aunque el archivo falte.
//
// El agente NO genera esta tabla: es parte del catalogo pre-construido. El
// usuario solo elige un nombre de familia en su `theme.tokens.json`.

/** Descriptor de una familia del catalogo. */
export interface FontEntry {
  /**
   * Fuentes de respaldo (SIN la familia principal, que se antepone sola).
   * Elegidas por parecido de forma y metrica con la familia principal.
   */
  fallbacks: string[];
  /**
   * Pesos que se auto-hospedan, UNO POR ARCHIVO: `<slug>-<peso>.woff2`
   * (p. ej. `poppins-600.woff2`). Se ignora en familias `variable`.
   */
  weights: number[];
  /**
   * Rango `[min, max]` si la familia es VARIABLE. Una fuente variable cubre todo
   * el rango con UN SOLO archivo (`<slug>-var.woff2`), asi que declarar pesos
   * sueltos descargaria el mismo archivo varias veces: Playfair pesaba 113 KB en
   * tres copias identicas y ahora pesa 37 KB en una. El `@font-face` declara
   * `font-weight: min max` y el navegador interpola el peso que pida el CSS.
   */
  variable?: [number, number];
  /** Categoria generica, ultimo recurso de la pila. */
  generic: "serif" | "sans-serif";
}

/**
 * Catalogo de familias soportadas. Cubre las combinaciones habituales de una
 * identidad institucional (una display para titulares + una neutra para texto).
 * Una familia fuera del catalogo sigue funcionando: `fontStack` le arma una pila
 * por defecto (ver `DEFAULT_*_FALLBACKS`).
 */
export const FONT_CATALOG: Record<string, FontEntry> = {
  // --- Serif de titulares (alto contraste, aire editorial) ---
  "Playfair Display": {
    fallbacks: ["Iowan Old Style", "Palatino Linotype", "Palatino", "Georgia"],
    weights: [400, 700, 800],
    variable: [400, 900],
    generic: "serif",
  },
  Lora: {
    fallbacks: ["Iowan Old Style", "Constantia", "Georgia"],
    weights: [400, 700],
    generic: "serif",
  },
  Merriweather: {
    fallbacks: ["Georgia", "Cambria", "Times New Roman"],
    weights: [400, 700],
    generic: "serif",
  },
  "Source Serif 4": {
    fallbacks: ["Charter", "Cambria", "Georgia"],
    weights: [400, 700],
    generic: "serif",
  },

  // --- Sans neutras de texto ---
  Inter: {
    fallbacks: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial"],
    weights: [400, 500, 600, 700],
    variable: [100, 900],
    generic: "sans-serif",
  },
  "Work Sans": {
    fallbacks: ["system-ui", "Segoe UI", "Roboto", "Helvetica Neue", "Arial"],
    weights: [400, 600, 700],
    generic: "sans-serif",
  },
  "Source Sans 3": {
    fallbacks: ["system-ui", "Segoe UI", "Roboto", "Helvetica Neue", "Arial"],
    weights: [400, 600, 700],
    generic: "sans-serif",
  },

  // --- Sans geometricas (titulares modernos) ---
  Poppins: {
    fallbacks: ["Avenir Next", "Avenir", "Century Gothic", "Futura", "system-ui"],
    weights: [400, 600, 700],
    generic: "sans-serif",
  },
  Montserrat: {
    fallbacks: ["Avenir Next", "Avenir", "Century Gothic", "system-ui"],
    weights: [400, 600, 700],
    generic: "sans-serif",
  },
  "DM Sans": {
    fallbacks: ["Avenir Next", "system-ui", "Segoe UI", "Roboto"],
    weights: [400, 500, 700],
    generic: "sans-serif",
  },
};

/** Pila por defecto para una familia serif desconocida. */
const DEFAULT_SERIF_FALLBACKS = ["Iowan Old Style", "Palatino", "Georgia", "Cambria"];
/** Pila por defecto para una familia sans desconocida. */
const DEFAULT_SANS_FALLBACKS = [
  "system-ui",
  "-apple-system",
  "Segoe UI",
  "Roboto",
  "Helvetica Neue",
  "Arial",
];

/**
 * Heuristica para una familia fuera del catalogo: se asume serif solo si el
 * nombre lo sugiere. Es deliberadamente conservadora — ante la duda, sans, que
 * es la eleccion segura para texto corrido en pantalla.
 */
function guessGeneric(name: string): "serif" | "sans-serif" {
  return /serif|playfair|georgia|garamond|times|roman|book|slab/i.test(name) &&
    !/sans/i.test(name)
    ? "serif"
    : "sans-serif";
}

/** Envuelve en comillas los nombres de familia con espacios. */
function quote(name: string): string {
  return /\s/.test(name) ? `"${name}"` : name;
}

/**
 * Construye la pila CSS completa de una familia: la familia pedida primero, sus
 * respaldos por parecido despues y el generico al final.
 *
 * Es una funcion pura y tolerante: un `name` vacio devuelve solo la pila
 * generica, de modo que un `theme.tokens.json` incompleto nunca produzca un
 * `font-family` invalido.
 */
export function fontStack(name: string | undefined | null): string {
  const family = (name ?? "").trim();
  if (!family) return DEFAULT_SANS_FALLBACKS.map(quote).join(", ") + ", sans-serif";

  const entry = FONT_CATALOG[family];
  const fallbacks = entry?.fallbacks ?? (
    guessGeneric(family) === "serif" ? DEFAULT_SERIF_FALLBACKS : DEFAULT_SANS_FALLBACKS
  );
  const generic = entry?.generic ?? guessGeneric(family);

  return [quote(family), ...fallbacks.map(quote), generic].join(", ");
}

/** Convierte un nombre de familia al slug con el que se nombran sus archivos. */
export function fontSlug(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Genera las reglas `@font-face` de las familias indicadas.
 *
 * Cada regla intenta primero `local(...)` (familia ya instalada en el equipo del
 * visitante, cero descarga) y despues el archivo auto-hospedado.
 * `font-display: swap` evita el texto invisible mientras carga: se pinta ya con
 * el respaldo y se cambia al llegar la fuente.
 *
 * Solo se emite la regla de un peso si su archivo EXISTE (`available`). Declarar
 * una fuente cuyo `.woff2` falta no rompe el render —el navegador baja por la
 * pila de `fontStack`—, pero dispara una peticion 404 por peso en cada visita.
 * Como la mayoria de los proyectos no traera archivos, el caso normal debe ser
 * cero peticiones fallidas.
 *
 * @param names     familias referenciadas por el tema (se deduplican).
 * @param available nombres de archivo presentes en `public/fonts/`
 *                  (p. ej. `"playfair-display-700.woff2"`). Vacio => sin reglas.
 */
export function fontFaces(
  names: (string | undefined | null)[],
  available: Set<string> = new Set()
): string {
  if (available.size === 0) return "";

  const unicas = Array.from(
    new Set(names.map((n) => (n ?? "").trim()).filter(Boolean))
  );

  const reglas: string[] = [];
  for (const family of unicas) {
    const entry = FONT_CATALOG[family];
    // Una familia fuera del catalogo no se auto-hospeda (no sabemos que pesos
    // ni que archivos esperar); vive de su pila de respaldo.
    if (!entry) continue;

    const slug = fontSlug(family);

    // Familia VARIABLE: un unico archivo cubre todo el rango de pesos.
    if (entry.variable) {
      const archivo = `${slug}-var.woff2`;
      if (available.has(archivo)) {
        const [min, max] = entry.variable;
        reglas.push(
          `@font-face{` +
            `font-family:${quote(family)};` +
            `font-style:normal;` +
            `font-weight:${min} ${max};` +
            `font-display:swap;` +
            `src:local(${quote(family)}),url("/fonts/${archivo}") format("woff2");` +
            `}`
        );
        continue;
      }
      // Sin el archivo variable se intentan los pesos sueltos (permite que un
      // proyecto aporte instancias estaticas de una familia variable).
    }

    for (const weight of entry.weights) {
      const archivo = `${slug}-${weight}.woff2`;
      if (!available.has(archivo)) continue;
      reglas.push(
        `@font-face{` +
          `font-family:${quote(family)};` +
          `font-style:normal;` +
          `font-weight:${weight};` +
          `font-display:swap;` +
          `src:local(${quote(family)}),url("/fonts/${archivo}") format("woff2");` +
          `}`
      );
    }
  }
  return reglas.join("\n");
}
