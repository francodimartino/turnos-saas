import { useCallback, useEffect, useState } from "react";
import { ErrorApi } from "../api/cliente";

export type Carga<T> = { tipo: "cargando" } | { tipo: "error"; mensaje: string } | { tipo: "lista"; datos: T };

export function mensajeDeError(e: unknown, porDefecto = "No se pudo completar la operación"): string {
  return e instanceof ErrorApi ? e.message : porDefecto;
}

/**
 * Carga datos del backend. `cargar` tiene que ser estable (useCallback): cuando cambia, vuelve a pedir.
 * `recargar()` repite el pedido (por ejemplo tras una acción que cambió el estado).
 */
export function useCarga<T>(cargar: () => Promise<T>): { carga: Carga<T>; recargar: () => void } {
  const [carga, setCarga] = useState<Carga<T>>({ tipo: "cargando" });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let vigente = true;
    cargar().then(
      (datos) => vigente && setCarga({ tipo: "lista", datos }),
      (e: unknown) =>
        vigente && setCarga({ tipo: "error", mensaje: mensajeDeError(e, "No se pudieron cargar los datos") }),
    );
    return () => {
      vigente = false;
    };
  }, [cargar, version]);

  const recargar = useCallback(() => setVersion((v) => v + 1), []);
  return { carga, recargar };
}
