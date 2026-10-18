import { vi } from "vitest";

export interface RespuestaFalsa {
  status?: number;
  cuerpo?: unknown;
  /** Headers extra de la respuesta (por ejemplo `Retry-After`). */
  headers?: Record<string, string>;
}

export type ManejadorFalso = (cuerpo: unknown, url: string) => RespuestaFalsa;

export interface Llamada {
  clave: string;
  /** Ruta con query string, para verificar filtros. */
  url: string;
  cuerpo: unknown;
  credentials: RequestCredentials | undefined;
}

/**
 * Reemplaza `fetch` por un dashboard-backend simulado. Las claves son "METODO /ruta". Un manejador puede ser una
 * función o una lista de respuestas que se consumen en orden (la última se repite).
 */
export function instalarBackend(rutas: Record<string, ManejadorFalso | RespuestaFalsa[]>) {
  const llamadas: Llamada[] = [];
  const contadores: Record<string, number> = {};

  const falso = vi.fn(async (entrada: RequestInfo | URL, init?: RequestInit) => {
    const ruta = new URL(String(entrada), "http://localhost").pathname;
    const clave = `${init?.method ?? "GET"} ${ruta}`;
    const cuerpo: unknown = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    llamadas.push({ clave, url: String(entrada), cuerpo, credentials: init?.credentials });

    const manejador = rutas[clave];
    if (!manejador) return new Response(JSON.stringify({ detail: "no existe" }), { status: 404 });
    let r: RespuestaFalsa;
    if (typeof manejador === "function") {
      r = manejador(cuerpo, String(entrada));
    } else {
      const i = contadores[clave] ?? 0;
      contadores[clave] = i + 1;
      r = manejador[Math.min(i, manejador.length - 1)] ?? {};
    }
    const status = r.status ?? 200;
    if (status === 204) return new Response(null, { status });
    return new Response(JSON.stringify(r.cuerpo ?? {}), { status, headers: { "Content-Type": "application/json", ...r.headers } });
  });
  vi.stubGlobal("fetch", falso);
  return { llamadas, cuantas: (clave: string) => llamadas.filter((l) => l.clave === clave).length };
}

export const SIN_SESION: RespuestaFalsa = {
  status: 401,
  cuerpo: { detail: { codigo: "no_autenticado", mensaje: "Sesión inválida o expirada" } },
};

export function sesionDe(datos: { is_owner: boolean; aal: "aal1" | "aal2"; permisos?: string[] }): RespuestaFalsa {
  return { cuerpo: { user_id: "u-1", tenant_id: "t-1", ...datos } };
}
