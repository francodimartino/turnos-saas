import { solicitar } from "./cliente";

import type { Esquemas } from "./tipos";

/** El contrato declara `status` como texto; estos son los valores que el backend usa. */
export type EstadoTurno = "pending" | "confirmed" | "cancelled" | "completed";
export const ESTADOS_TURNO: readonly EstadoTurno[] = ["pending", "confirmed", "cancelled", "completed"];

export type Turno = Esquemas["TurnoListadoOut"];
/** Lo que devuelven las acciones: el turno actualizado más lo que pasó con Calendar y con el aviso al cliente. */
export type ResultadoTurno = Esquemas["TurnoCambiadoOut"];
export type Cliente = Esquemas["ClienteListadoOut"];
export type Pack = Esquemas["PackOut"];

export interface FiltroTurnos {
  desde: string;
  hasta: string;
  estados?: readonly EstadoTurno[];
  profesionalId?: string;
}

/** Tope de la API por página: si llega a este número puede haber turnos sin mostrar. */
export const LIMITE_TURNOS = 200;

export async function listarTurnos(f: FiltroTurnos): Promise<Turno[]> {
  const q = new URLSearchParams({ desde: f.desde, hasta: f.hasta, limite: String(LIMITE_TURNOS) });
  for (const e of f.estados ?? []) q.append("estado", e);
  if (f.profesionalId) q.set("professional_id", f.profesionalId);
  return (await solicitar<Esquemas["ListaTurnosOut"]>(`/turnos?${q.toString()}`)).turnos;
}

export type DatosTurno = Esquemas["AltaTurno"];

export const crearTurno = (d: DatosTurno) => solicitar<ResultadoTurno>("/turnos", { metodo: "POST", cuerpo: d });
export const aprobarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/aprobar`, { metodo: "POST" });
export const rechazarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/rechazar`, { metodo: "POST" });
export const cancelarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/cancelar`, { metodo: "POST" });
export const completarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/completar`, { metodo: "POST" });
export const moverTurno = (id: string, startAt: string) =>
  solicitar<ResultadoTurno>(`/turnos/${id}/mover`, { metodo: "POST", cuerpo: { start_at: startAt } });

export const listarClientes = async () =>
  (await solicitar<Esquemas["ListaClientesOut"]>(`/clientes?limite=${LIMITE_TURNOS}`)).clientes;

export const listarPacks = async () => (await solicitar<Esquemas["ListaPacksOut"]>(`/packs?limite=${LIMITE_TURNOS}`)).packs;

export type DatosPack = Esquemas["AltaPack"];

export const asignarPack = (d: DatosPack) => solicitar<Pack>("/packs", { metodo: "POST", cuerpo: d });
