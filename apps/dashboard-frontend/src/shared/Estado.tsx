import type { ReactNode } from "react";
import type { Carga } from "./useCarga";

interface Props<T> {
  carga: Carga<T>;
  reintentar: () => void;
  children: (datos: T) => ReactNode;
}

/** Estados de carga y error comunes a las pantallas que leen del backend. */
export function Estado<T>({ carga, reintentar, children }: Props<T>) {
  if (carga.estado === "cargando") return <p role="status">Cargando…</p>;
  if (carga.estado === "error") {
    return (
      <div>
        <p role="alert">{carga.mensaje}</p>
        <button type="button" onClick={reintentar}>
          Reintentar
        </button>
      </div>
    );
  }
  return <>{children(carga.datos)}</>;
}
