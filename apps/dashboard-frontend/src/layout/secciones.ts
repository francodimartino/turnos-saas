import type { Seccion } from "./permisos";

/**
 * Secciones del panel. Cada pantalla de negocio (turnos, clientes, equipo...) agrega acá su entrada junto con su
 * ruta, con el `permission_key` que la habilita; no se listan secciones cuyas pantallas todavía no existen.
 */
export const SECCIONES: readonly Seccion[] = [{ ruta: "/", etiqueta: "Inicio" }];
