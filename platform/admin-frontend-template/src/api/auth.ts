import { ErrorApi, solicitar } from "./cliente";

export type SiguientePaso = "listo" | "verificar_mfa" | "enrolar_mfa";
export type Aal = "aal1" | "aal2";

export const CODIGO_SIN_ACCESO = "sin_acceso_admin";

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
  aal: Aal;
}

interface RespuestaSesion {
  user_id: string;
  aal: Aal;
}

export function login(email: string, password: string): Promise<RespuestaLogin> {
  return solicitar("/auth/login", { metodo: "POST", cuerpo: { email, password } });
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

export function cerrarSesion(): Promise<void> {
  return solicitar("/auth/logout", { metodo: "POST" });
}

function refrescar(): Promise<{ expira_en: number }> {
  return solicitar("/auth/refrescar", { metodo: "POST" });
}

function desdeRespuesta(r: RespuestaSesion): Sesion {
  return { userId: r.user_id, aal: r.aal };
}

/** Quién soy. Si el access token venció, renueva con el refresh token (cookie) y reintenta una vez. */
export async function obtenerSesion(): Promise<Sesion> {
  try {
    return desdeRespuesta(await solicitar<RespuestaSesion>("/auth/sesion"));
  } catch (e) {
    if (!(e instanceof ErrorApi) || e.estado !== 401) throw e;
  }
  await refrescar();
  return desdeRespuesta(await solicitar<RespuestaSesion>("/auth/sesion"));
}

/** La cuenta existe pero no es administradora de la plataforma (403 `sin_acceso_admin`). */
export function esSinAcceso(e: unknown): boolean {
  return e instanceof ErrorApi && e.estado === 403 && e.codigo === CODIGO_SIN_ACCESO;
}
