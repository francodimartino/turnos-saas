import * as Sentry from "@sentry/react";

/**
 * Sentry sin datos personales (ARCHITECTURE.md §6.1): los datos se guardan en EE.UU., así que nada de PII.
 * Equivale al `send_default_pii=False` + `before_send` de los backends.
 */

/**
 * Claves de PII que se quitan en cualquier nivel del evento. Misma lista que `PII_CAMPOS` de
 * `apps/shared/observabilidad/pii.py`: un test de Python compara las dos y falla si difieren.
 */
export const CAMPOS_PII: readonly string[] = [
  "body",
  "caption",
  "channel_user_id",
  "content",
  "contrasena",
  "email",
  "message_body",
  "message_text",
  "name",
  "nombre",
  "password",
  "phone",
  "telefono",
  "text",
];

const EMAIL = /[^\s@]+@[^\s@]+\.[^\s@]+/g;
const TELEFONO = /\+?\d[\d\s().-]{7,}\d/g;

export function redactarTexto(texto: string): string {
  return texto.replace(EMAIL, "[email]").replace(TELEFONO, "[telefono]");
}

/** Copia de `valor` sin claves de `CAMPOS_PII` (sin distinguir mayúsculas), en cualquier nivel. */
export function quitarCamposPii(valor: unknown, nivel = 0): unknown {
  if (nivel > 32) return "[filtrado]";
  if (Array.isArray(valor)) return valor.map((v) => quitarCamposPii(v, nivel + 1));
  if (valor !== null && typeof valor === "object") {
    const salida: Record<string, unknown> = {};
    for (const [clave, v] of Object.entries(valor)) {
      if (!CAMPOS_PII.includes(clave.toLowerCase())) salida[clave] = quitarCamposPii(v, nivel + 1);
    }
    return salida;
  }
  return valor;
}

/** `before_send`: quita usuario, request (cookies, headers, cuerpo, query), breadcrumbs y datos extra. */
export function limpiarEvento<T extends Sentry.ErrorEvent>(evento: T): T {
  Object.assign(evento, quitarCamposPii({ ...evento }) as object);
  delete evento.user;
  delete evento.request;
  delete evento.breadcrumbs;
  delete evento.extra;
  if (evento.message) evento.message = redactarTexto(evento.message);
  for (const excepcion of evento.exception?.values ?? []) {
    if (excepcion.value) excepcion.value = redactarTexto(excepcion.value);
  }
  return evento;
}

export function iniciarSentry(dsn: string | undefined): boolean {
  if (!dsn) return false;
  Sentry.init({
    dsn,
    sendDefaultPii: false,
    // Sin trazas ni replays: capturan URLs y DOM con datos de personas.
    tracesSampleRate: 0,
    beforeSend: limpiarEvento,
  });
  return true;
}
