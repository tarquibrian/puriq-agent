import { defineConfig } from "astro/config";
import { readFileSync } from "node:fs";

// URL publica del sitio, tomada del contrato (`Site_Config.deploy.domain`).
//
// Astro la necesita para resolver las URL ABSOLUTAS que exigen el canonical, las
// etiquetas Open Graph y el sitemap: una URL relativa no sirve en ninguno de los
// tres (ni un buscador ni una red social pueden resolverla). Se lee del mismo
// `site.config.json` que el agente inyecta antes del build, para no tener la
// direccion del sitio duplicada en dos lugares.
//
// Si el contrato no declara dominio —lo normal mientras el usuario todavia esta
// probando en local— se usa un marcador. El sitio se construye igual; solo las
// URL absolutas apuntan a ese marcador hasta que se configure el dominio real.
const FALLBACK_SITE = "https://example.invalid";

function resolveSite() {
  try {
    const cfg = JSON.parse(
      readFileSync(new URL("./src/data/site.config.json", import.meta.url), "utf-8"),
    );
    const dominio = (cfg?.deploy?.domain ?? "").trim();
    if (!dominio) return FALLBACK_SITE;
    // Se acepta tanto "turismo.potosi.gob.bo" como una URL completa.
    const url = /^https?:\/\//i.test(dominio) ? dominio : `https://${dominio}`;
    return url.replace(/\/+$/, "");
  } catch {
    return FALLBACK_SITE;
  }
}

// Sitio estatico: ideal para hosting en AWS Amplify / S3 + CloudFront.
export default defineConfig({
  site: resolveSite(),
  output: "static",
  build: { assets: "_assets" },
});
