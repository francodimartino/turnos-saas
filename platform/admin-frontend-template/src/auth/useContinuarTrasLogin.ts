import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { RespuestaLogin } from "../api/auth";
import { useSesion } from "./SesionContext";

/** Solo rutas internas: empieza con "/" y no con "//" ni "/\\" (URLs protocol-relative que sacan al usuario del sitio). */
function esRutaInterna(ruta: string | undefined): ruta is string {
  return typeof ruta === "string" && ruta.startsWith("/") && !ruta.startsWith("//") && !ruta.startsWith("/\\");
}

/**
 * Decide a dónde va el usuario según el `siguiente_paso` que devolvió el backend (login o invitación):
 * panel, verificación de MFA existente o enrollment de MFA (administradores sin factor, §3.6).
 */
export function useContinuarTrasLogin() {
  const navegar = useNavigate();
  const ubicacion = useLocation();
  const { definirPasoMfa, recargar } = useSesion();

  return useCallback(
    async (respuesta: RespuestaLogin) => {
      if (respuesta.siguiente_paso === "enrolar_mfa") {
        definirPasoMfa({ paso: "enrolar" });
        navegar("/mfa/enrolar", { replace: true });
        return;
      }
      if (respuesta.siguiente_paso === "verificar_mfa" && respuesta.factor_id) {
        definirPasoMfa({ paso: "verificar", factorId: respuesta.factor_id });
        navegar("/mfa/verificar", { replace: true });
        return;
      }
      definirPasoMfa(null);
      await recargar();
      const desde = (ubicacion.state as { desde?: string } | null)?.desde;
      navegar(esRutaInterna(desde) ? desde : "/", { replace: true });
    },
    [definirPasoMfa, navegar, recargar, ubicacion.state],
  );
}
