import { solicitar } from "./cliente";

import type { Esquemas } from "./tipos";

/** Fila del listado (sin `extra_data`) o detalle (con `extra_data`). */
export type ClienteListado = Esquemas["ClienteListadoOut"];
export type Cliente = Esquemas["ClienteOut"];
export type ListadoClientes = Esquemas["ListaClientesOut"];
export type ResultadoAnonimizar = Esquemas["AnonimizacionOut"];

export interface DatosCliente {
  phone: string;
  name: string;
  extra_data: Record<string, string>;
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
