import { ErrorApi } from "../../api/cliente";
import { erroresDeApi } from "../../compartido/errores";

export const MENSAJE_HORARIO_OCUPADO =
  "Ese horario está ocupado: el profesional ya tiene otro turno que se superpone. Elegí otro horario.";

/** Texto único para mostrar un error de una acción sobre turnos o packs. */
export function textoError(e: unknown): string {
  if (e instanceof ErrorApi && e.codigo === "horario_ocupado") return MENSAJE_HORARIO_OCUPADO;
  if (e instanceof ErrorApi && e.codigo === "cambio_no_pendiente") return MENSAJE_CAMBIO_NO_PENDIENTE;
  if (e instanceof ErrorApi && e.codigo === "cambio_vencido") return MENSAJE_CAMBIO_VENCIDO;
  if (e instanceof ErrorApi && e.codigo === "operacion_concurrente") return MENSAJE_OPERACION_CONCURRENTE;
  const { general, campos } = erroresDeApi(e);
  return general ?? (Object.values(campos).join(" ") || "No se pudo completar la operación");
}

/** Textos para el estado del aviso al cliente que devuelve el servidor; los estados que no necesitan nada (enviado, no aplica) no figuran. */
const AVISO_AL_CLIENTE: Record<string, string> = {
  pendiente_de_envio: "El cliente tiene más de un canal: el aviso quedó en la cola y se le enviará en breve.",
  error: "El cambio se guardó, pero no se pudo avisar al cliente en este momento.",
  sin_integracion: "El cambio se guardó, pero no hay una integración configurada para avisarle al cliente.",
  cliente_anonimizado: "El cambio se guardó. El cliente fue anonimizado, así que no se le avisó.",
  sin_canal: "El cambio se guardó. El cliente no tiene un canal verificado, así que no se le avisó.",
};

/** Aviso (no error) cuando el cambio se guardó pero Calendar no se pudo sincronizar o el cliente no recibió el aviso al instante. */
export function avisoDeResultado(r: { calendario?: { estado: string }; aviso?: string } | undefined): string | null {
  const textos = [
    r?.calendario?.estado === "error"
      ? "El cambio se guardó, pero no se pudo actualizar Google Calendar. Revisá la conexión del calendario."
      : null,
    r?.aviso === undefined ? null : (AVISO_AL_CLIENTE[r.aviso] ?? null),
  ].filter((t) => t !== null);
  return textos.length > 0 ? textos.join(" ") : null;
}

/** Mensajes de los conflictos de un cambio de horario pendiente (cada uno con su código del servidor). */
export const MENSAJE_CAMBIO_NO_PENDIENTE =
  "Ese turno ya no tiene un cambio de horario pendiente: alguien lo resolvió antes. Se actualizó la lista.";
export const MENSAJE_CAMBIO_VENCIDO =
  "El cambio de horario venció o el turno ya empezó: no se puede aprobar. Podés rechazarlo para conservar el horario original.";
export const MENSAJE_OPERACION_CONCURRENTE = "Otro cambio sobre este turno está en curso. Esperá unos segundos y volvé a intentar.";
