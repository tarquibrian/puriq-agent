// robots.txt del sitio construido.
//
// Se permite el rastreo completo (es un sitio publico de promocion turistica:
// que lo indexen es justamente el objetivo) y se apunta al sitemap, que es como
// un buscador descubre las paginas de detalle de lugares y articulos.
import type { APIRoute } from "astro";

export const GET: APIRoute = ({ site }) => {
  const base = site ?? new URL("https://example.invalid");
  const cuerpo = `User-agent: *
Allow: /

Sitemap: ${new URL("/sitemap.xml", base).href}
`;
  return new Response(cuerpo, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
