import { ErrorApi } from "../../api/cliente";

export type AccionAvisos = "guardar" | "volver_al_default";

const PREFIJO: Record<AccionAvisos, string> = {
  guardar: "No se guardaron los cambios de avisos.",
  volver_al_default: "No se pudo volver a esa persona a la configuración por defecto.",
};

const SIN_PERMISO = "Tu usuario no tiene permiso para configurar los avisos.";

/** El motivo según el código del error (nunca el texto del backend). `null` si el código no tiene uno propio. */
function motivoDe(e: ErrorApi): string | null {
  switch (e.codigo) {
    case "sin_conexion":
      return "No se pudo conectar con el servidor.";
    case "cambio_invalido":
      return "Alguno de los cambios no es válido.";
    case "staff_repetido":
      return "Una misma persona aparece más de una vez en el pedido.";
    case "staff_inexistente":
      return "Alguna de las personas ya no pertenece al negocio: recargá la página.";
    case "canal_no_disponible":
      return "WhatsApp todavía no está disponible.";
    case "staff_inactivo":
      return "Alguna de las personas está desactivada.";
    case "sin_destinatario_de_avisos":
      return "El negocio tiene que quedar con al menos una persona que reciba los avisos por email.";
    case "conflicto_de_concurrencia":
      return "Otro cambio se cruzó con este. Probá de nuevo.";
    case "permiso_denegado":
      return SIN_PERMISO;
    case "datos_invalidos":
      return "El servidor no aceptó el pedido.";
  }
  return e.estado === 403 ? SIN_PERMISO : null;
}

export function mensajeDeAvisos(e: unknown, accion: AccionAvisos): string {
  const motivo = e instanceof ErrorApi ? motivoDe(e) : null;
  return `${PREFIJO[accion]} ${motivo ?? "Probá de nuevo."}`;
}
