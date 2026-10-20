import type { Permiso } from "../../api/generado/permisos";

export interface PermisoCatalogo {
  clave: Permiso;
  etiqueta: string;
  descripcion: string;
}

/** Catálogo de `permission_key` (ARCHITECTURE.md §2.4) con lo que habilita cada uno, para la pantalla del equipo. */
export const CATALOGO_PERMISOS: readonly PermisoCatalogo[] = [
  { clave: "view_bookings", etiqueta: "Ver turnos", descripcion: "Ver la agenda, los turnos y los clientes." },
  {
    clave: "approve_bookings",
    etiqueta: "Gestionar turnos",
    descripcion: "Aprobar, rechazar, mover y cancelar turnos.",
  },
  { clave: "edit_prices", etiqueta: "Editar precios", descripcion: "Cambiar los precios de servicios y packs." },
  {
    clave: "manage_staff",
    etiqueta: "Gestionar el equipo",
    descripcion: "Invitar y desactivar personas del equipo. No permite cambiar permisos ni dar el rol de owner.",
  },
  {
    clave: "manage_customers",
    etiqueta: "Gestionar clientes",
    descripcion: "Dar de alta clientes y corregir sus datos.",
  },
  {
    clave: "delete_customers",
    etiqueta: "Anonimizar clientes",
    descripcion: "Derecho al olvido: borrar de forma irreversible los datos de un cliente.",
  },
  {
    clave: "manage_schedule",
    etiqueta: "Gestionar horarios",
    descripcion: "Editar horarios, bloqueos de disponibilidad y datos del local.",
  },
];

export function esPermisoConocido(clave: string): clave is Permiso {
  return CATALOGO_PERMISOS.some((p) => p.clave === clave);
}

/** Etiqueta para la tabla: una clave que ya no está en el catálogo (permiso retirado) se marca como tal. */
export function etiquetaDePermiso(clave: string): string {
  return CATALOGO_PERMISOS.find((p) => p.clave === clave)?.etiqueta ?? `${clave} (retirado)`;
}
