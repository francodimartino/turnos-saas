import { solicitar } from "./cliente";

export interface Cliente {
  id: string;
  channel: string | null;
  phone: string | null;
  name: string | null;
  /** El listado no trae `extra_data`; el detalle sí. */
  extra_data?: Record<string, unknown>;
  deleted_at: string | null;
  created_at: string | null;
  sin_cliente_identificable: boolean;
}

export interface ListadoClientes {
  clientes: Cliente[];
  limite: number;
  offset: number;
}

export interface DatosCliente {
  phone: string;
  name: string;
  extra_data: Record<string, string>;
}

export interface ResultadoAnonimizar {
  id: string;
  sin_cliente_identificable: boolean;
  calendario: { estado: "ok" | "error"; eventos: number | null; no_limpiados: number | null };
}

export const LIMITE_CLIENTES = 50;

export function listarClientes(busqueda: string, offset: number): Promise<ListadoClientes> {
  const consulta = new URLSearchParams({ limite: String(LIMITE_CLIENTES), offset: String(offset) });
  if (busqueda.trim() !== "") consulta.set("busqueda", busqueda.trim());
  return solicitar(`/clientes?${consulta.toString()}`);
}

export const detalleCliente = (id: string) => solicitar<Cliente>(`/clientes/${encodeURIComponent(id)}`);
export const crearCliente = (d: DatosCliente) => solicitar<Cliente>("/clientes", { metodo: "POST", cuerpo: d });
export const modificarCliente = (id: string, d: Partial<DatosCliente>) =>
  solicitar<Cliente>(`/clientes/${encodeURIComponent(id)}`, { metodo: "PATCH", cuerpo: d });
export const anonimizarCliente = (id: string) =>
  solicitar<ResultadoAnonimizar>(`/clientes/${encodeURIComponent(id)}/anonimizar`, { metodo: "POST" });
