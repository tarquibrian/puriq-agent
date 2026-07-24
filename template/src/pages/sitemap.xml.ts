// Sitemap del sitio construido.
//
// Se genera a mano en vez de sumar una integracion: las URL salen del propio
// contrato (portada + una por lugar + una por articulo) y no hace falta rastrear
// el sitio para descubrirlas. Un buscador que llega al dominio necesita esta
// lista para indexar las paginas de detalle, que no estan enlazadas desde
// ningun sitio externo.
import type { APIRoute } from "astro";
import { getCollection } from "astro:content";
import { places } from "../lib/data";

/** Escapa los caracteres que no pueden ir crudos dentro de un XML. */
function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export const GET: APIRoute = async ({ site }) => {
  // Sin `site` configurado no se pueden emitir URL absolutas, que es lo unico
  // que admite el formato; se responde un sitemap vacio pero valido.
  const base = site ?? new URL("https://example.invalid");

  const articulos = await getCollection("blog");
  const rutas = [
    "/",
    ...places.map((p) => `/lugares/${p.id}/`),
    ...articulos.map((a) => `/blog/${a.slug}/`),
  ];

  const urls = rutas
    .map((ruta) => `  <url><loc>${escapeXml(new URL(ruta, base).href)}</loc></url>`)
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>
`;

  return new Response(xml, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
