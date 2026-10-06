import { ErrorApi } from "../../api/cliente";
import { erroresDeApi } from "../../shared/errores";

export const MENSAJE_HORARIO_OCUPADO =
  "Ese horario está ocupado: el profesional ya tiene otro turno que se superpone. Elegí otro horario.";

/** Texto único para mostrar un error de una acción sobre turnos o packs. */
export function textoError(e: unknown): string {
  if (e instanceof ErrorApi && e.codigo === "horario_ocupado") return MENSAJE_HORARIO_OCUPADO;
  const { general, campos } = erroresDeApi(e);
  return general ?? (Object.values(campos).join(" ") || "No se pudo completar la operación");
}

/** Aviso (no error) cuando el cambio se guardó pero Calendar no se pudo sincronizar. */
export function avisoDeResultado(r: { calendario?: { estado: string } } | undefined): string | null {
  return r?.calendario?.estado === "error"
    ? "El cambio se guardó, pero no se pudo actualizar Google Calendar. Revisá la conexión del calendario."
    : null;
}
