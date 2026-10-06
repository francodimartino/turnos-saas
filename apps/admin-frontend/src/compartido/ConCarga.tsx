import type { ReactNode } from "react";
import type { Carga } from "./useCarga";

/** Muestra "Cargando…", el error o los datos de una `Carga`. */
export function ConCarga<T>({ carga, hijos }: { carga: Carga<T>; hijos: (datos: T) => ReactNode }) {
  if (carga.tipo === "cargando") return <p role="status">Cargando…</p>;
  if (carga.tipo === "error") return <p role="alert">{carga.mensaje}</p>;
  return <>{hijos(carga.datos)}</>;
}
