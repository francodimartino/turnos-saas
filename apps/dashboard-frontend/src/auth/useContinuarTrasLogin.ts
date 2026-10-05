import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { RespuestaLogin } from "../api/auth";
import { useSesion } from "./SesionContext";

/**
 * Decide a dónde va el usuario según el `siguiente_paso` que devolvió el backend (login o invitación):
 * panel, verificación de MFA existente o enrollment de MFA (owners sin factor, §3.6).
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
      navegar(desde && desde.startsWith("/") ? desde : "/", { replace: true });
    },
    [definirPasoMfa, navegar, recargar, ubicacion.state],
  );
}
