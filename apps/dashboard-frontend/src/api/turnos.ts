import { solicitar } from "./cliente";

export type EstadoTurno = "pending" | "confirmed" | "cancelled" | "completed";
export const ESTADOS_TURNO: readonly EstadoTurno[] = ["pending", "confirmed", "cancelled", "completed"];

export interface Turno {
  id: string;
  end_customer_id: string;
  service_id: string;
  professional_id: string;
  /** ISO con zona. */
  start_at: string;
  end_at: string;
  status: EstadoTurno;
  pending_expires_at: string | null;
  /** null si el cliente fue anonimizado. */
  customer_name: string | null;
  service_name: string;
  professional_name: string;
  sin_cliente_identificable: boolean;
}

/** Lo que devuelven las acciones: el turno actualizado más lo que pasó con Calendar y con el aviso al cliente. */
export interface ResultadoTurno extends Turno {
  calendario?: { estado: string };
  aviso?: string;
}

export interface Cliente {
  id: string;
  name: string | null;
  phone: string | null;
  sin_cliente_identificable: boolean;
}

export interface Pack {
  id: string;
  end_customer_id: string;
  service_id: string;
  total_sessions: number;
  used_sessions: number;
  origin: string;
  expires_at: string | null;
}

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
  return (await solicitar<{ turnos: Turno[] }>(`/turnos?${q.toString()}`)).turnos;
}

export interface DatosTurno {
  end_customer_id: string;
  service_id: string;
  professional_id: string;
  start_at: string;
}

export const crearTurno = (d: DatosTurno) => solicitar<ResultadoTurno>("/turnos", { metodo: "POST", cuerpo: d });
export const aprobarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/aprobar`, { metodo: "POST" });
export const rechazarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/rechazar`, { metodo: "POST" });
export const cancelarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/cancelar`, { metodo: "POST" });
export const completarTurno = (id: string) => solicitar<ResultadoTurno>(`/turnos/${id}/completar`, { metodo: "POST" });
export const moverTurno = (id: string, startAt: string) =>
  solicitar<ResultadoTurno>(`/turnos/${id}/mover`, { metodo: "POST", cuerpo: { start_at: startAt } });

export const listarClientes = async () =>
  (await solicitar<{ clientes: Cliente[] }>(`/clientes?limite=${LIMITE_TURNOS}`)).clientes;

export const listarPacks = async () => (await solicitar<{ packs: Pack[] }>(`/packs?limite=${LIMITE_TURNOS}`)).packs;

export interface DatosPack {
  end_customer_id: string;
  service_id: string;
  total_sessions: number;
  expires_at?: string;
}

export const asignarPack = (d: DatosPack) => solicitar<Pack>("/packs", { metodo: "POST", cuerpo: d });
