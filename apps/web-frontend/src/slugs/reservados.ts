/**
 * Nombres que no pueden ser el slug de un negocio: son rutas de la propia web o nombres técnicos.
 * Misma lista que `slug_reservado()` de la base y que `SLUGS_RESERVADOS` de `platform_admin`
 * (ARCHITECTURE.md §2.12): un test de pytest compara las tres y falla si difieren.
 */
export const SLUGS_RESERVADOS: readonly string[] = [
  "admin",
  "admin-api",
  "api",
  "app",
  "assets",
  "ayuda",
  "blog",
  "bot",
  "contacto",
  "cuenta",
  "dashboard",
  "docs",
  "health",
  "login",
  "logout",
  "panel",
  "precios",
  "privacidad",
  "registro",
  "reservas",
  "reservas-api",
  "soporte",
  "static",
  "status",
  "terminos",
  "webhook",
  "webhooks",
  "www",
];

/** Formato de un slug de negocio: minúsculas, números y guiones sueltos (igual que la base). */
const FORMATO_SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export type TipoDeRuta = "reservado" | "invalido" | "negocio";

/** Qué es el primer tramo de la ruta: un nombre reservado, algo que no puede ser un slug, o un posible negocio. */
export function clasificarSlug(slug: string): TipoDeRuta {
  if (SLUGS_RESERVADOS.includes(slug)) return "reservado";
  if (!FORMATO_SLUG.test(slug)) return "invalido";
  return "negocio";
}
