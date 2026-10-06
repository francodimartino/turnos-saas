import { Navigate, Outlet, useLocation } from "react-router-dom";
import { AccesoDenegado } from "../paginas/AccesoDenegado";
import { useSesion } from "./SesionContext";

/**
 * Guarda de rutas privadas. El panel exige MFA a todo administrador (§3.6):
 *  - Sin sesión: a /login (o al paso de MFA pendiente, si el login ya dijo cuál es).
 *  - Cuenta que no es administradora: pantalla de acceso denegado, nunca el panel.
 *  - Sesión con `aal1`: nunca ve el panel; se la manda al enrollment/verificación de MFA.
 * El backend es el que decide de verdad (el gate rechaza todo sin `aal2`); esto es la experiencia de usuario.
 */
export function RutaProtegida() {
  const { estado, pasoMfa } = useSesion();
  const ubicacion = useLocation();

  if (estado.estado === "cargando") return <p role="status">Cargando…</p>;
  if (estado.estado === "sin_acceso") return <AccesoDenegado />;

  const destinoMfa = pasoMfa ? (pasoMfa.paso === "enrolar" ? "/mfa/enrolar" : "/mfa/verificar") : null;

  if (estado.estado === "anonima") {
    if (destinoMfa) return <Navigate to={destinoMfa} replace />;
    return <Navigate to="/login" replace state={{ desde: ubicacion.pathname }} />;
  }
  if (estado.sesion.aal !== "aal2") return <Navigate to={destinoMfa ?? "/login"} replace />;
  return <Outlet />;
}
