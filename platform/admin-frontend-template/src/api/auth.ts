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

/** `turnstileToken` solo se manda si el captcha está configurado (hay site key); si no, el body queda igual que siempre. */
export function login(email: string, password: string, turnstileToken?: string): Promise<RespuestaLogin> {
  const cuerpo =
    turnstileToken === undefined ? { email, password } : { email, password, turnstile_token: turnstileToken };
  return solicitar("/auth/login", { metodo: "POST", cuerpo });
}

function textoEspera(segundos: number): string {
  if (segundos < 60) return `${segundos} ${segundos === 1 ? "segundo" : "segundos"}`;
  const minutos = Math.ceil(segundos / 60);
  return `${minutos} ${minutos === 1 ? "minuto" : "minutos"}`;
}

/** Mensaje en español para un error de `POST /auth/login`, incluidos los del captcha y el límite de intentos. */
export function mensajeDeErrorLogin(e: unknown): string {
  if (!(e instanceof ErrorApi)) return "No se pudo iniciar sesión";
  switch (e.codigo) {
    case "turnstile_invalido":
      return "No pudimos verificar que sos una persona. Probá de nuevo.";
    case "turnstile_no_disponible":
      return "La verificación anti-bots no está disponible en este momento. Probá de nuevo en unos segundos.";
    case "demasiados_intentos":
      return e.reintentarEnSeg === null
        ? "Demasiados intentos. Esperá un rato antes de volver a probar."
        : `Demasiados intentos. Probá de nuevo en ${textoEspera(e.reintentarEnSeg)}.`;
    default:
      // El mensaje del backend ya distingue credenciales incorrectas de una cuenta que no es administradora.
      return e.message;
  }
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

function desdeRespuesta(r: RespuestaSesion): Sesion {
  return { userId: r.user_id, aal: r.aal };
}

/** Quién soy. Si el access token venció, `solicitar` renueva con el refresh token (cookie) y reintenta una vez. */
export async function obtenerSesion(): Promise<Sesion> {
  return desdeRespuesta(await solicitar<RespuestaSesion>("/auth/sesion"));
}

/** La cuenta existe pero no es administradora de la plataforma (403 `sin_acceso_admin`). */
export function esSinAcceso(e: unknown): boolean {
  return e instanceof ErrorApi && e.estado === 403 && e.codigo === CODIGO_SIN_ACCESO;
}
