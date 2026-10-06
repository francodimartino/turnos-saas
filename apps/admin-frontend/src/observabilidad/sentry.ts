import * as Sentry from "@sentry/react";

/**
 * Sentry sin datos personales (ARCHITECTURE.md §6.1): los datos se guardan en EE.UU., así que nada de PII.
 * Equivale al `send_default_pii=False` + `before_send` de los backends.
 */

const EMAIL = /[^\s@]+@[^\s@]+\.[^\s@]+/g;
const TELEFONO = /\+?\d[\d\s().-]{7,}\d/g;

export function redactarTexto(texto: string): string {
  return texto.replace(EMAIL, "[email]").replace(TELEFONO, "[telefono]");
}

/** `before_send`: quita usuario, request (cookies, headers, cuerpo, query), breadcrumbs y datos extra. */
export function limpiarEvento<T extends Sentry.ErrorEvent>(evento: T): T {
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
