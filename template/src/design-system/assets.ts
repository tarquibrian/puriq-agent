// Verificacion de recursos referenciados por el contrato.
//
// PROBLEMA QUE RESUELVE
// El contrato referencia imagenes por RUTA (`assets/hero-oaxaca.jpg`), pero nada
// garantiza que el archivo exista: el usuario puede declarar una foto en el
// wizard y no subirla nunca, o borrarla despues. Los componentes ya degradan con
// elegancia cuando la referencia FALTA (el Hero cae a degradado, la ficha de un
// lugar muestra su marcador de posicion), pero no cuando la referencia existe y
// apunta a la nada: ahi el navegador pinta una imagen rota y el sitio se ve
// averiado. En el ejemplo de Oaxaca eso dejaba el hero como un rectangulo gris.
//
// Estas funciones corren EN TIEMPO DE BUILD (el frontmatter de Astro se ejecuta
// en Node), asi que una referencia colgante se puede tratar como ausente y
// disparar la misma degradacion elegante que ya existe.

import { existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

/** Raiz `public/` de la Template, que Astro publica en la raiz del sitio. */
const PUBLIC_DIR = fileURLToPath(new URL("../../public", import.meta.url));

/**
 * Conjunto de rutas relativas presentes bajo `public/`, calculado una sola vez.
 * Se cachea porque cada seccion consulta varias referencias y el build no deberia
 * golpear el disco una vez por imagen.
 */
let cache: Set<string> | null = null;

function publicFiles(): Set<string> {
  if (cache) return cache;
  const found = new Set<string>();

  function walk(dir: string, prefix: string) {
    let entries: import("node:fs").Dirent[];
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      return; // `public/` puede no existir; no es un error.
    }
    for (const entry of entries) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) walk(join(dir, entry.name), rel);
      else found.add(rel);
    }
  }

  walk(PUBLIC_DIR, "");
  cache = found;
  return found;
}

/**
 * True si la referencia se puede resolver a un recurso real.
 *
 * Reglas:
 *  - Vacia o ausente -> false (no hay nada que mostrar).
 *  - URL absoluta (`http://`, `https://`, `//`) o `data:` -> true. Vive fuera del
 *    sitio y no se puede comprobar en build; se confia en el autor.
 *  - Ruta relativa -> se busca bajo `public/`, con o sin barra inicial.
 */
export function assetExists(ref: string | undefined | null): boolean {
  const ruta = (ref ?? "").trim();
  if (!ruta) return false;
  if (/^(https?:)?\/\//i.test(ruta) || ruta.startsWith("data:")) return true;

  const limpia = ruta.replace(/^\/+/, "");
  if (publicFiles().has(limpia)) return true;

  // Comprobacion directa como red de seguridad: cubre el caso de que el recurso
  // se haya copiado a `public/` despues de que se calculara la cache.
  return existsSync(join(PUBLIC_DIR, limpia));
}

/** Filtra una lista de recursos dejando solo los que existen. */
export function existingOnly<T extends { src?: string }>(items: T[]): T[] {
  return items.filter((item) => assetExists(item?.src));
}
