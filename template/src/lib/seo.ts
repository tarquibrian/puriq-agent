// Datos estructurados (schema.org) del sitio turistico.
//
// Describen el destino y su contenido en el vocabulario que los buscadores
// entienden. Para un sitio de turismo el rendimiento es concreto: un
// `TouristAttraction` con coordenadas y horarios puede aparecer en el panel de
// resultados y en los mapas, y un `Event` con fechas puede listarse como evento.
// Sin esto, un buscador solo ve texto suelto.
//
// Todo sale del contrato ya cargado; no se inventa ningun dato. Los campos
// ausentes se OMITEN en vez de emitirse vacios: un `address: ""` es peor que no
// declarar direccion, porque afirma algo falso.

import type { Place, EventItem } from "./data";

/** Serializa a JSON-LD listo para inyectar en un `<script type="application/ld+json">`. */
function serialize(node: Record<string, unknown>): string {
  return JSON.stringify(node);
}

/** Quita las claves con valor vacio, nulo o indefinido. */
function compact<T extends Record<string, unknown>>(obj: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v) && v.length === 0) continue;
    out[k] = v;
  }
  return out as T;
}

/** Convierte una ruta del contrato en URL absoluta; deja pasar las que ya lo son. */
function absolute(ref: string | undefined, base: URL | undefined): string | undefined {
  const ruta = (ref ?? "").trim();
  if (!ruta || !base) return undefined;
  return new URL(ruta, base).href;
}

/** Datos estructurados del destino (portada). */
export function siteJsonLd(
  site: any,
  base: URL | undefined,
  opts: { places?: Place[] } = {},
): string {
  const destino = compact({
    "@context": "https://schema.org",
    "@type": "TouristDestination",
    name: site?.name,
    description: site?.description,
    url: base?.href,
    address: site?.region
      ? compact({ "@type": "PostalAddress", addressRegion: site.region })
      : undefined,
    geo: site?.center
      ? compact({
          "@type": "GeoCoordinates",
          latitude: site.center.lat,
          longitude: site.center.lng,
        })
      : undefined,
    // Los lugares se enlazan por URL: repetir su ficha entera aqui duplicaria
    // los datos que ya publica cada pagina de detalle.
    containsPlace: (opts.places ?? []).map((p) =>
      compact({
        "@type": "TouristAttraction",
        name: p.name,
        url: absolute(`/lugares/${p.id}/`, base),
      }),
    ),
  });
  return serialize(destino);
}

/** Datos estructurados de un lugar (pagina de detalle). */
export function placeJsonLd(place: Place, base: URL | undefined): string {
  return serialize(
    compact({
      "@context": "https://schema.org",
      "@type": "TouristAttraction",
      name: place.name,
      description: place.description || place.shortDescription,
      url: absolute(`/lugares/${place.id}/`, base),
      image: (place.images ?? [])
        .map((img) => absolute(img, base))
        .filter(Boolean),
      address: place.address
        ? compact({ "@type": "PostalAddress", streetAddress: place.address })
        : undefined,
      geo: place.coords
        ? compact({
            "@type": "GeoCoordinates",
            latitude: place.coords.lat,
            longitude: place.coords.lng,
          })
        : undefined,
      openingHours: place.hours,
    }),
  );
}

/** Datos estructurados de un evento. */
export function eventJsonLd(
  event: EventItem,
  place: Place | undefined,
  base: URL | undefined,
): string {
  return serialize(
    compact({
      "@context": "https://schema.org",
      "@type": "Event",
      name: event.name,
      description: event.description,
      startDate: event.startDate,
      endDate: event.endDate,
      image: (event.images ?? []).map((img) => absolute(img, base)).filter(Boolean),
      location: place
        ? compact({
            "@type": "Place",
            name: place.name,
            address: place.address,
            geo: place.coords
              ? compact({
                  "@type": "GeoCoordinates",
                  latitude: place.coords.lat,
                  longitude: place.coords.lng,
                })
              : undefined,
          })
        : undefined,
    }),
  );
}
