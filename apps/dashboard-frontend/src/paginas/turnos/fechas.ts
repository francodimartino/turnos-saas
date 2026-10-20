import { isoALocal, localAIso } from "../../compartido/formato";

/** Los "días" son textos AAAA-MM-DD del calendario de Buenos Aires; la aritmética se hace en UTC para no depender del equipo. */
export type Vista = "dia" | "semana";

export function hoy(): string {
  return isoALocal(new Date().toISOString()).slice(0, 10);
}

export function sumarDias(dia: string, n: number): string {
  const d = new Date(`${dia}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Lunes de la semana del día dado. */
export function inicioSemana(dia: string): string {
  const diaSemana = (new Date(`${dia}T00:00:00Z`).getUTCDay() + 6) % 7;
  return sumarDias(dia, -diaSemana);
}

/** Rango consultado: de las 00:00 del primer día a las 00:00 del día siguiente al último (hora de Buenos Aires). */
export function rango(dia: string, vista: Vista): { desde: string; hasta: string; dias: string[] } {
  const primero = vista === "dia" ? dia : inicioSemana(dia);
  const cantidad = vista === "dia" ? 1 : 7;
  const dias = Array.from({ length: cantidad }, (_, i) => sumarDias(primero, i));
  return { desde: localAIso(`${primero}T00:00`), hasta: localAIso(`${sumarDias(primero, cantidad)}T00:00`), dias };
}

export function diaDe(iso: string): string {
  return isoALocal(iso).slice(0, 10);
}

export function horaDe(iso: string): string {
  return isoALocal(iso).slice(11, 16);
}

export function etiquetaDia(dia: string): string {
  return new Intl.DateTimeFormat("es-AR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  }).format(new Date(`${dia}T00:00:00Z`));
}
