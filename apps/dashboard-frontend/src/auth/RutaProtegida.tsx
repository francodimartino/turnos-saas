import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useSesion } from "./SesionContext";

/**
 * Guarda de rutas privadas.
 *  - Sin sesión: a /login (o al paso de MFA pendiente, si el login ya dijo cuál es).
 *  - Owner con `aal1`: nunca ve el panel; se lo manda al enrollment/verificación de MFA (§3.6).
 * El backend es el que decide de verdad (rechaza `aal1` de owners); esto es la experiencia de usuario.
 */
export function RutaProtegida() {
  const { estado, pasoMfa } = useSesion();
  const ubicacion = useLocation();

  if (estado.estado === "cargando") return <p role="status">Cargando…</p>;

  const destinoMfa = pasoMfa ? (pasoMfa.paso === "enrolar" ? "/mfa/enrolar" : "/mfa/verificar") : null;

  if (estado.estado === "anonima") {
    if (destinoMfa) return <Navigate to={destinoMfa} replace />;
    return <Navigate to="/login" replace state={{ desde: ubicacion.pathname }} />;
  }
  if (estado.sesion.isOwner && estado.sesion.aal !== "aal2") {
    return <Navigate to={destinoMfa ?? "/login"} replace />;
  }
  return <Outlet />;
}
