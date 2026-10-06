import type { Seccion } from "./permisos";

/**
 * Secciones del panel. Cada pantalla de negocio (turnos, clientes, equipo...) agrega acá su entrada junto con su
 * ruta, con el `permission_key` que la habilita; no se listan secciones cuyas pantallas todavía no existen.
 */
export const SECCIONES: readonly Seccion[] = [
  { ruta: "/", etiqueta: "Inicio" },
  { ruta: "/turnos", etiqueta: "Turnos", permiso: "view_bookings" },
  // La lectura del catálogo, la disponibilidad y los datos del local es de cualquier staff: lo que se oculta por
  // permiso son las acciones de cada pantalla (el backend decide igual).
  { ruta: "/servicios", etiqueta: "Servicios" },
  { ruta: "/profesionales", etiqueta: "Profesionales" },
  { ruta: "/disponibilidad", etiqueta: "Disponibilidad" },
  { ruta: "/local", etiqueta: "Datos del local" },
];
