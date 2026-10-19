import { useSesion } from "../auth/SesionContext";
import { tienePermiso } from "../layout/permisos";

/**
 * Si la sesión activa tiene el permiso (el owner los tiene todos). Es solo experiencia de usuario: el backend
 * vuelve a chequear en cada endpoint. Sin sesión cargada devuelve false (no se muestran acciones).
 */
export function usePermiso(permiso: string): boolean {
  const { estado } = useSesion();
  return estado.estado === "activa" && tienePermiso(estado.sesion, permiso);
}

/**
 * Si la sesión activa es la del owner. Hay acciones que el backend reserva al owner y no se delegan con un permiso
 * (p. ej. vincular una cuenta a un profesional). Es solo experiencia de usuario: el backend responde 403 `no_es_owner`.
 */
export function useEsOwner(): boolean {
  const { estado } = useSesion();
  return estado.estado === "activa" && estado.sesion.isOwner;
}
