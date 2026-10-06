/** Zona horaria en la que se muestran y cargan fechas del local. Argentina no tiene horario de verano (UTC-3). */
export const ZONA_HORARIA = "America/Argentina/Buenos_Aires";
const DESFASE_MS = 3 * 60 * 60 * 1000;

export function formatearDinero(valor: string | number): string {
  const n = Number(valor);
  if (Number.isNaN(n)) return String(valor);
  return new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" }).format(n);
}

export function formatearFechaHora(iso: string): string {
  return new Intl.DateTimeFormat("es-AR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: ZONA_HORARIA,
  }).format(
    new Date(iso),
  );
}

/** Valor de un `<input type="datetime-local">` ("AAAA-MM-DDTHH:mm", hora de Buenos Aires) a ISO con zona. */
export function localAIso(valor: string): string {
  return `${valor}:00-03:00`;
}

/** ISO con zona a valor de `<input type="datetime-local">` en hora de Buenos Aires. */
export function isoALocal(iso: string): string {
  return new Date(new Date(iso).getTime() - DESFASE_MS).toISOString().slice(0, 16);
}

export function horaCorta(hora: string): string {
  return hora.slice(0, 5);
}
