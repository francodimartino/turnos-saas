import { useCallback, useEffect, useState } from "react";
import { mensajeDeError } from "./errores";

export type Carga<T> = { estado: "cargando" } | { estado: "error"; mensaje: string } | { estado: "lista"; datos: T };

/** Carga datos al montar (y al cambiar `cargar`, que el llamador debe memorizar) y permite recargar. */
export function useCarga<T>(cargar: () => Promise<T>): { carga: Carga<T>; recargar: () => void } {
  const [version, setVersion] = useState(0);
  const [carga, setCarga] = useState<Carga<T>>({ estado: "cargando" });

  useEffect(() => {
    let vigente = true;
    cargar().then(
      (datos) => vigente && setCarga({ estado: "lista", datos }),
      (e: unknown) => vigente && setCarga({ estado: "error", mensaje: mensajeDeError(e) }),
    );
    return () => {
      vigente = false;
    };
  }, [cargar, version]);

  const recargar = useCallback(() => setVersion((v) => v + 1), []);
  return { carga, recargar };
}
