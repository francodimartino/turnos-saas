/**
 * Cliente HTTP hacia dashboard-backend: la única puerta del frontend al servidor.
 *
 * La sesión viaja en cookies HttpOnly que el backend pone y lee; acá solo se pide `credentials: "include"`.
 * El JavaScript nunca ve ni guarda un token.
 */

export class ErrorApi extends Error {
  readonly estado: number;
  readonly codigo: string;

  constructor(estado: number, codigo: string, mensaje: string) {
    super(mensaje);
    this.name = "ErrorApi";
    this.estado = estado;
    this.codigo = codigo;
  }
}

export const URL_API: string = (import.meta.env.VITE_API_URL ?? "").replace(/\/+$/, "");

interface Opciones {
  metodo?: "GET" | "POST";
  cuerpo?: unknown;
}

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

async function errorDeRespuesta(r: Response): Promise<ErrorApi> {
  let codigo = "error_desconocido";
  let mensaje = "No se pudo completar la operación";
  try {
    const cuerpo: unknown = await r.json();
    const detalle = esObjeto(cuerpo) ? cuerpo["detail"] : undefined;
    if (esObjeto(detalle)) {
      if (typeof detalle["codigo"] === "string") codigo = detalle["codigo"];
      if (typeof detalle["mensaje"] === "string") mensaje = detalle["mensaje"];
    } else if (r.status === 422) {
      codigo = "datos_invalidos";
      mensaje = "Revisá los datos ingresados";
    }
  } catch {
    // Cuerpo vacío o no JSON: se queda con el mensaje genérico.
  }
  return new ErrorApi(r.status, codigo, mensaje);
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
