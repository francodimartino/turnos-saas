/**
 * Cliente HTTP hacia dashboard-backend: la única puerta del frontend al servidor.
 *
 * La sesión viaja en cookies HttpOnly que el backend pone y lee; acá solo se pide `credentials: "include"`.
 * El JavaScript nunca ve ni guarda un token.
 */

export class ErrorApi extends Error {
  readonly estado: number;
  readonly codigo: string;
  /** Errores de validación de forma (422 de FastAPI) por nombre de campo del cuerpo. Vacío si no aplica. */
  readonly campos: Readonly<Record<string, string>>;
  /** Campos extra del `detail` del backend (por ejemplo `cliente_id` de un 409 `cliente_duplicado`). */
  readonly detalle: Readonly<Record<string, unknown>>;
  /** Segundos que indicó el header `Retry-After` (429). `null` si no vino o no es un número. */
  readonly reintentarEnSeg: number | null;

  constructor(
    estado: number,
    codigo: string,
    mensaje: string,
    campos: Record<string, string> = {},
    detalle: Record<string, unknown> = {},
    reintentarEnSeg: number | null = null,
  ) {
    super(mensaje);
    this.name = "ErrorApi";
    this.estado = estado;
    this.codigo = codigo;
    this.campos = campos;
    this.detalle = detalle;
    this.reintentarEnSeg = reintentarEnSeg;
  }
}

export const URL_API: string = (import.meta.env.VITE_API_URL ?? "").replace(/\/+$/, "");

interface Opciones {
  metodo?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  cuerpo?: unknown;
}

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

/** `detail` de un 422 de FastAPI: lista de `{loc: ["body", ..., campo], msg}`. Se indexa por el último nombre. */
function camposDeValidacion(detalle: unknown): Record<string, string> {
  const campos: Record<string, string> = {};
  if (!Array.isArray(detalle)) return campos;
  for (const item of detalle) {
    if (!esObjeto(item) || !Array.isArray(item["loc"]) || typeof item["msg"] !== "string") continue;
    const nombres = item["loc"].filter((x): x is string => typeof x === "string" && x !== "body");
    const campo = nombres[nombres.length - 1];
    if (campo !== undefined && !(campo in campos)) campos[campo] = item["msg"];
  }
  return campos;
}

async function errorDeRespuesta(r: Response): Promise<ErrorApi> {
  let codigo = "error_desconocido";
  let mensaje = "No se pudo completar la operación";
  let campos: Record<string, string> = {};
  let detalleExtra: Record<string, unknown> = {};
  try {
    const cuerpo: unknown = await r.json();
    const detalle = esObjeto(cuerpo) ? cuerpo["detail"] : undefined;
    if (esObjeto(detalle) && !Array.isArray(detalle)) {
      if (typeof detalle["codigo"] === "string") codigo = detalle["codigo"];
      if (typeof detalle["mensaje"] === "string") mensaje = detalle["mensaje"];
      detalleExtra = { ...detalle };
    } else if (r.status === 422) {
      codigo = "datos_invalidos";
      mensaje = "Revisá los datos ingresados";
      campos = camposDeValidacion(detalle);
    }
  } catch {
    // Cuerpo vacío o no JSON: se queda con el mensaje genérico.
  }
  const cabecera = r.headers.get("Retry-After");
  const espera = Number(cabecera);
  const reintentarEnSeg = cabecera !== null && Number.isFinite(espera) && espera > 0 ? espera : null;
  return new ErrorApi(r.status, codigo, mensaje, campos, detalleExtra, reintentarEnSeg);
}

/** Rutas de `/auth/` que sí renuevan ante un 401: el resto (login, MFA, invitación, logout, refrescar) no. */
const RUTAS_AUTH_CON_REFRESH = new Set(["/auth/sesion"]);

function dispararRefresh(ruta: string): boolean {
  return !ruta.startsWith("/auth/") || RUTAS_AUTH_CON_REFRESH.has(ruta);
}

let refreshEnCurso: Promise<boolean> | null = null;
/** Tras un refresh fallido no se vuelve a intentar hasta que alguna petición salga bien (evita ráfagas contra el rate limit). */
let sesionPerdida = false;
let alPerderSesion: (() => void) | null = null;

/** La app registra acá qué hacer cuando el refresh falla (llevar al login). Devuelve cómo desregistrarse. */
export function registrarAlPerderSesion(callback: () => void): () => void {
  alPerderSesion = callback;
  return () => {
    if (alPerderSesion === callback) alPerderSesion = null;
  };
}

/** Un único `POST /auth/refrescar` compartido por todas las peticiones que reciben 401 a la vez. */
function renovarSesion(): Promise<boolean> {
  if (refreshEnCurso) return refreshEnCurso;
  refreshEnCurso = (async () => {
    try {
      const r = await fetch(`${URL_API}/auth/refrescar`, { method: "POST", credentials: "include" });
      if (r.ok) return true;
    } catch {
      // Sin conexión: se trata como refresh fallido, sin reintentar.
    }
    sesionPerdida = true;
    alPerderSesion?.();
    return false;
  })().finally(() => {
    refreshEnCurso = null;
  });
  return refreshEnCurso;
}

async function enviar(ruta: string, metodo: string, cuerpo: unknown): Promise<Response> {
  try {
    return await fetch(`${URL_API}${ruta}`, {
      method: metodo,
      credentials: "include",
      headers: cuerpo === undefined ? undefined : { "Content-Type": "application/json" },
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
    });
  } catch {
    throw new ErrorApi(0, "sin_conexion", "No se pudo conectar con el servidor");
  }
}

export async function solicitar<T>(ruta: string, { metodo = "GET", cuerpo }: Opciones = {}): Promise<T> {
  let respuesta = await enviar(ruta, metodo, cuerpo);
  // El 401 llega antes de ejecutar nada, así que reintentar una vez cualquier método es seguro.
  if (respuesta.status === 401 && dispararRefresh(ruta) && !sesionPerdida) {
    if (await renovarSesion()) respuesta = await enviar(ruta, metodo, cuerpo);
  }
  if (respuesta.ok) sesionPerdida = false;
  else throw await errorDeRespuesta(respuesta);
  if (respuesta.status === 204) return undefined as T;
  return (await respuesta.json()) as T;
}

/** Solo para tests: deja el módulo como recién cargado. */
export function reiniciarEstadoDeRefresh(): void {
  refreshEnCurso = null;
  sesionPerdida = false;
  alPerderSesion = null;
}
