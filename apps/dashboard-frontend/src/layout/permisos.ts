import type { Sesion } from "../api/auth";
import type { Permiso } from "../api/generado/permisos";

export interface Seccion {
  ruta: string;
  etiqueta: string;
  /** `permission_key` (§2.4) que habilita la sección. Sin valor: visible para todo usuario con sesión. */
  permiso?: Permiso;
}

/** El owner implica todos los permisos (§2.4); el resto, solo los que tiene asignados. */
export function tienePermiso(sesion: Sesion, permiso: Permiso | undefined): boolean {
  if (permiso === undefined) return true;
  return sesion.isOwner || sesion.permisos.includes(permiso);
}

/** Es solo experiencia de usuario: el backend vuelve a chequear el permiso en cada endpoint. */
export function seccionesVisibles(secciones: readonly Seccion[], sesion: Sesion): Seccion[] {
  return secciones.filter((s) => tienePermiso(sesion, s.permiso));
}
