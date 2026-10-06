/** Resultados posibles de la vuelta de Google (`google_calendar=<resultado>`), con su mensaje para el negocio. */
export interface AvisoCalendar {
  tipo: "exito" | "error";
  texto: string;
}

const AVISOS: Record<string, AvisoCalendar> = {
  ok: { tipo: "exito", texto: "Google Calendar quedó conectado. Los turnos de este profesional se van a reflejar en su calendario." },
  state_invalido: {
    tipo: "error",
    texto: "El enlace de conexión no es válido o venció. Volvé a iniciar la conexión.",
  },
  cancelado: { tipo: "error", texto: "No se concedió el acceso a Google Calendar, así que no se conectó nada." },
  sin_permiso: { tipo: "error", texto: "No tenés permiso para conectar el calendario de ese profesional." },
  profesional_invalido: { tipo: "error", texto: "Ese profesional no existe o está inactivo." },
  google_rechazo: { tipo: "error", texto: "Google no aceptó la conexión. Probá de nuevo." },
  google_no_disponible: {
    tipo: "error",
    texto: "No pudimos comunicarnos con Google. Probá de nuevo en un rato.",
  },
  calendar_no_configurado: {
    tipo: "error",
    texto: "La conexión con Google Calendar todavía no está disponible. Avisá a soporte.",
  },
};

const GENERICO: AvisoCalendar = {
  tipo: "error",
  texto: "No se pudo completar la conexión con Google Calendar. Probá de nuevo.",
};

export function esResultadoConocido(resultado: string): boolean {
  return Object.hasOwn(AVISOS, resultado);
}

export function avisoDeResultado(resultado: string): AvisoCalendar {
  return Object.hasOwn(AVISOS, resultado) ? (AVISOS[resultado] ?? GENERICO) : GENERICO;
}
