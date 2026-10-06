/**
 * Cliente HTTP hacia admin-backend: la única puerta del frontend al servidor.
 *
 * La sesión viaja en cookies HttpOnly que el backend pone y lee; acá solo se pide `credentials: "include"`.
 * El JavaScript nunca ve ni guarda un token.
 */

export class ErrorApi extends Error {
  readonly estado: number;
  readonly codigo: string;
  /** Errores de validación de forma (422 de FastAPI) por nombre de campo del cuerpo. Vacío si no aplica. */
  readonly campos: Readonly<Record<string, string>>;

  constructor(estado: number, codigo: string, mensaje: string, campos: Record<string, string> = {}) {
    super(mensaje);
    this.name = "ErrorApi";
    this.estado = estado;
    this.codigo = codigo;
    this.campos = campos;
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
  try {
    const cuerpo: unknown = await r.json();
    const detalle = esObjeto(cuerpo) ? cuerpo["detail"] : undefined;
    if (esObjeto(detalle) && !Array.isArray(detalle)) {
      if (typeof detalle["codigo"] === "string") codigo = detalle["codigo"];
      if (typeof detalle["mensaje"] === "string") mensaje = detalle["mensaje"];
    } else if (r.status === 422) {
      codigo = "datos_invalidos";
      mensaje = "Revisá los datos ingresados";
      campos = camposDeValidacion(detalle);
    }
  } catch {
    // Cuerpo vacío o no JSON: se queda con el mensaje genérico.
  }
  return new ErrorApi(r.status, codigo, mensaje, campos);
}

export async function solicitar<T>(ruta: string, { metodo = "GET", cuerpo }: Opciones = {}): Promise<T> {
  let respuesta: Response;
  try {
    respuesta = await fetch(`${URL_API}${ruta}`, {
      method: metodo,
      credentials: "include",
      headers: cuerpo === undefined ? undefined : { "Content-Type": "application/json" },
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
    });
  } catch {
    throw new ErrorApi(0, "sin_conexion", "No se pudo conectar con el servidor");
  }
  if (!respuesta.ok) throw await errorDeRespuesta(respuesta);
  if (respuesta.status === 204) return undefined as T;
  return (await respuesta.json()) as T;
}
