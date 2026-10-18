import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { registrarAlPerderSesion } from "../api/cliente";
import { cerrarSesion, obtenerSesion, type Sesion } from "../api/auth";

export type PasoMfa = { paso: "enrolar" } | { paso: "verificar"; factorId: string };

export type EstadoSesion = { estado: "cargando" } | { estado: "anonima" } | { estado: "activa"; sesion: Sesion };

interface ValorSesion {
  estado: EstadoSesion;
  /** Paso de MFA pendiente tras el login (solo en memoria: al recargar se vuelve a iniciar sesión). */
  pasoMfa: PasoMfa | null;
  definirPasoMfa: (paso: PasoMfa | null) => void;
  /** Vuelve a consultar al backend quién es el usuario. Devuelve la sesión o null si no hay una válida. */
  recargar: () => Promise<Sesion | null>;
  salir: () => Promise<void>;
}

const Contexto = createContext<ValorSesion | null>(null);

export function ProveedorSesion({ children }: { children: ReactNode }) {
  const [estado, setEstado] = useState<EstadoSesion>({ estado: "cargando" });
  const [pasoMfa, definirPasoMfa] = useState<PasoMfa | null>(null);

  const recargar = useCallback(async (): Promise<Sesion | null> => {
    try {
      const sesion = await obtenerSesion();
      setEstado({ estado: "activa", sesion });
      return sesion;
    } catch {
      // 401 (sin sesión o refresh vencido), 403 `mfa_requerida` (sesión aal1 de un owner) o red caída:
      // en todos los casos no hay sesión de dashboard utilizable.
      setEstado({ estado: "anonima" });
      return null;
    }
  }, []);

  useEffect(() => {
    // Carga inicial: el estado se actualiza cuando responde el backend (en el callback, no dentro del efecto).
    let vigente = true;
    obtenerSesion().then(
      (sesion) => vigente && setEstado({ estado: "activa", sesion }),
      () => vigente && setEstado({ estado: "anonima" }),
    );
    return () => {
      vigente = false;
    };
  }, []);

  useEffect(
    // Si el refresh falla en cualquier petición, la sesión se da por perdida y RutaProtegida lleva al login.
    () => registrarAlPerderSesion(() => setEstado({ estado: "anonima" })),
    [],
  );

  const salir = useCallback(async () => {
    try {
      await cerrarSesion();
    } finally {
      definirPasoMfa(null);
      setEstado({ estado: "anonima" });
    }
  }, []);

  const valor = useMemo(
    () => ({ estado, pasoMfa, definirPasoMfa, recargar, salir }),
    [estado, pasoMfa, recargar, salir],
  );
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

export function useSesion(): ValorSesion {
  const v = useContext(Contexto);
  if (!v) throw new Error("useSesion fuera de ProveedorSesion");
  return v;
}
