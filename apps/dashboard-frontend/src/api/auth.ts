import { solicitar } from "./cliente";

export type SiguientePaso = "listo" | "verificar_mfa" | "enrolar_mfa";
export type Aal = "aal1" | "aal2";

export interface RespuestaLogin {
  siguiente_paso: SiguientePaso;
  /** Solo viene con `verificar_mfa`. */
  factor_id?: string;
  expira_en: number;
}

export interface EnrolamientoMfa {
  factor_id: string;
  /** Data URI SVG del QR (se muestra en un <img>). */
  qr_code: string;
  /** Clave en texto para cargarla a mano en la app autenticadora. */
  secret: string;
  uri: string;
}

export interface Sesion {
  userId: string;
  tenantId: string;
  isOwner: boolean;
  aal: Aal;
  /** `permission_key`s del usuario. El owner no los necesita: los tiene todos (§2.4). */
  permisos: string[];
}

interface RespuestaSesion {
  user_id: string;
  tenant_id: string;
  is_owner: boolean;
  aal: Aal;
  permisos?: string[];
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
): Promise<{ siguiente_paso: "listo"; expira_en: number }> {
  return solicitar("/auth/mfa/verificar", { metodo: "POST", cuerpo: { factor_id: factorId, codigo } });
}

export function aceptarInvitacion(tokenHash: string, password: string, turnstileToken?: string): Promise<RespuestaLogin> {
  return solicitar("/auth/invitacion/aceptar", {
    metodo: "POST",
    cuerpo: { token_hash: tokenHash, password, ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) },
  });
}

export function cerrarSesion(): Promise<void> {
  return solicitar("/auth/logout", { metodo: "POST" });
}

function desdeRespuesta(r: RespuestaSesion): Sesion {
  return { userId: r.user_id, tenantId: r.tenant_id, isOwner: r.is_owner, aal: r.aal, permisos: r.permisos ?? [] };
}

/** Quién soy. Si el access token venció, `solicitar` renueva con el refresh token (cookie) y reintenta una vez. */
export async function obtenerSesion(): Promise<Sesion> {
  return desdeRespuesta(await solicitar<RespuestaSesion>("/auth/sesion"));
}
