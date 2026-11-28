import { useParams } from "react-router-dom";
import { clasificarSlug } from "../slugs/reservados";
import { NoDisponible } from "./NoDisponible";
import { NoEncontrada } from "./NoEncontrada";

/**
 * Ruta `/<slug>`. Un nombre reservado o con formato inválido nunca se trata como negocio. Por ahora no hay a quién
 * consultar por un negocio, así que cualquier posible negocio muestra la página "no disponible".
 */
export function PaginaNegocio() {
  const { slug = "" } = useParams();
  return clasificarSlug(slug) === "negocio" ? <NoDisponible /> : <NoEncontrada />;
}
