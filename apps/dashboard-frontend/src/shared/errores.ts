import { ErrorApi } from "../api/cliente";

export interface ErroresFormulario {
  /** Mensajes por nombre de campo del cuerpo (se muestran junto al campo). */
  campos: Record<string, string>;
  /** Lo que no se puede atribuir a un campo (permisos, duplicados, red caída...). */
  general: string | null;
}

export interface OpcionesErrores {
  /** Para `valor_invalido` (los `check` de la base no nombran el campo): primera regla cuyo texto coincide. */
  reglas?: readonly (readonly [RegExp, string])[];
  /** Campo al que va un `valor_invalido` que ninguna regla reconoce. Sin valor: queda como error general. */
  porDefecto?: string;
}

export const MENSAJE_EN_USO =
  "No se puede borrar porque tiene datos asociados (por ejemplo, turnos): desactivalo en lugar de borrarlo.";

/** Traduce un error de la API a mensajes por campo + un mensaje general. */
export function erroresDeApi(e: unknown, { reglas = [], porDefecto }: OpcionesErrores = {}): ErroresFormulario {
  if (!(e instanceof ErrorApi)) return { campos: {}, general: "No se pudo completar la operación" };
  if (Object.keys(e.campos).length > 0) return { campos: { ...e.campos }, general: null };
  if (e.codigo === "valor_invalido") {
    const regla = reglas.find(([patron]) => patron.test(e.message));
    const campo = regla?.[1] ?? porDefecto;
    if (campo !== undefined) return { campos: { [campo]: e.message }, general: null };
  }
  if (e.codigo === "en_uso") return { campos: {}, general: MENSAJE_EN_USO };
  if (e.estado === 403) return { campos: {}, general: "No tenés permiso para hacer este cambio" };
  return { campos: {}, general: e.message };
}

export const SIN_ERRORES: ErroresFormulario = { campos: {}, general: null };

export function mensajeDeError(e: unknown): string {
  return e instanceof ErrorApi ? e.message : "No se pudo completar la operación";
}
