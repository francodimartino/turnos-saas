import { solicitar, solicitarSinContenido } from "./cliente";

import type { Esquemas } from "./tipos";

export type RespuestaLogin = Esquemas["SiguientePasoOut"];
export type SiguientePaso = RespuestaLogin["siguiente_paso"];
export type EnrolamientoMfa = Esquemas["EnrolarMFAOut"];
export type Aal = "aal1" | "aal2";

export interface Sesion {
  userId: string;
  tenantId: string;
  isOwner: boolean;
  aal: Aal;
  /** `permission_key`s del usuario. El owner no los necesita: los tiene todos (§2.4). */
  permisos: string[];
}


/** `turnstileToken` solo se manda si hay widget activo (sin site key el cuerpo queda como siempre). */
export function login(email: string, password: string, turnstileToken?: string): Promise<RespuestaLogin> {
  return solicitar("/auth/login", {
    metodo: "POST",
    cuerpo: { email, password, ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) },
  });
}

export function enrolarMfa(): Promise<EnrolamientoMfa> {
  return solicitar("/auth/mfa/enrolar", { metodo: "POST" });
}

export function verificarMfa(
  factorId: string,
  codigo: string,
): Promise<Esquemas["MFAVerificadaOut"]> {
  return solicitar("/auth/mfa/verificar", { metodo: "POST", cuerpo: { factor_id: factorId, codigo } });
}

export function aceptarInvitacion(tokenHash: string, password: string, turnstileToken?: string): Promise<RespuestaLogin> {
  return solicitar("/auth/invitacion/aceptar", {
    metodo: "POST",
    cuerpo: { token_hash: tokenHash, password, ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) },
  });
}

export function cerrarSesion(): Promise<void> {
  return solicitarSinContenido("/auth/logout", { metodo: "POST" });
}

function desdeRespuesta(r: Esquemas["SesionOut"]): Sesion {
  return {
    userId: r.user_id,
    tenantId: r.tenant_id ?? "",
    isOwner: r.is_owner ?? false,
    aal: r.aal === "aal2" ? "aal2" : "aal1",
    permisos: r.permisos ?? [],
  };
}

/** Quién soy. Si el access token venció, `solicitar` renueva con el refresh token (cookie) y reintenta una vez. */
export async function obtenerSesion(): Promise<Sesion> {
  return desdeRespuesta(await solicitar<Esquemas["SesionOut"]>("/auth/sesion"));
}
