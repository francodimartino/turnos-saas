/** Formato de fechas del panel: siempre hora de Buenos Aires (Argentina no tiene horario de verano: UTC-3). */

export const ZONA = "America/Argentina/Buenos_Aires";

const FECHA_HORA = new Intl.DateTimeFormat("es-AR", {
  timeZone: ZONA,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** Instante ISO -> `dd/mm/aaaa, HH:MM` en hora de Buenos Aires. Vacío o inválido -> "—". */
export function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : FECHA_HORA.format(d);
}

/** Fecha de calendario `AAAA-MM-DD` (sin zona: no se desplaza) -> `dd/mm/aaaa`. */
export function fechaCalendario(valor: string | null | undefined): string {
  if (!valor) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(valor);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

/** Valor de un `<input type="datetime-local">` (`AAAA-MM-DDTHH:MM`, hora de Buenos Aires) -> ISO con offset. */
export function localABuenosAires(valor: string): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(valor)) return undefined;
  return `${valor}:00-03:00`;
}

/** Fecha `AAAA-MM-DD` de hoy en Buenos Aires. */
export function hoyBuenosAires(ahora: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ZONA, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    ahora,
  );
}

/** Suma días a una fecha `AAAA-MM-DD`. */
export function sumarDias(fecha: string, dias: number): string {
  const d = new Date(`${fecha}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + dias);
  return d.toISOString().slice(0, 10);
}
