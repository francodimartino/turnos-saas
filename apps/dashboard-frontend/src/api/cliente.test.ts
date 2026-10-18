import { describe, expect, it, vi } from "vitest";
import { ErrorApi, registrarAlPerderSesion, solicitar } from "./cliente";
import { instalarBackend, SIN_SESION } from "../test/falsoBackend";

const OK = { cuerpo: { dato: 1 } };

describe("ErrorApi.reintentarEnSeg", () => {
  it.each([
    ["30", 30],
    [undefined, null],
    ["abc", null],
    ["0", null],
  ])("con Retry-After %s vale %s", async (cabecera, esperado) => {
    instalarBackend({
      "GET /a": [
        {
          status: 429,
          cuerpo: { detail: { codigo: "demasiados_intentos" } },
          headers: cabecera === undefined ? {} : { "Retry-After": cabecera },
        },
      ],
    });
    await expect(solicitar("/a")).rejects.toMatchObject({ estado: 429, reintentarEnSeg: esperado });
  });
});

describe("solicitar: 422 con lista de errores de FastAPI", () => {
  it("el mensaje une los msg sin el prefijo de pydantic y conserva los campos", async () => {
    instalarBackend({
      "PATCH /x": [
        {
          status: 422,
          cuerpo: {
            detail: [
              { loc: ["body", "a"], msg: "Value error, a fuera de rango" },
              { loc: ["body", "b"], msg: "b requerido" },
            ],
          },
        },
      ],
    });
    await expect(solicitar("/x", { metodo: "PATCH", cuerpo: {} })).rejects.toMatchObject({
      estado: 422,
      message: "a fuera de rango. b requerido",
      campos: { a: "a fuera de rango", b: "b requerido" },
    });
  });
});

describe("solicitar: renovación ante 401", () => {
  it("ante un 401 llama una vez a /auth/refrescar y reintenta la petición original una vez", async () => {
    const backend = instalarBackend({
      "GET /turnos": [SIN_SESION, OK],
      "POST /auth/refrescar": [{ cuerpo: { expira_en: 3600 } }],
    });
    await expect(solicitar("/turnos")).resolves.toEqual({ dato: 1 });
    expect(backend.cuantas("POST /auth/refrescar")).toBe(1);
    expect(backend.cuantas("GET /turnos")).toBe(2);
  });

  it("reintenta también las peticiones que no son GET", async () => {
    const backend = instalarBackend({
      "POST /turnos": [SIN_SESION, OK],
      "POST /auth/refrescar": [{}],
    });
    await solicitar("/turnos", { metodo: "POST", cuerpo: { a: 1 } });
    expect(backend.llamadas.filter((l) => l.clave === "POST /turnos").map((l) => l.cuerpo)).toEqual([{ a: 1 }, { a: 1 }]);
  });

  it("con 3 peticiones en paralelo que reciben 401 hace un único refresh", async () => {
    const backend = instalarBackend({
      "GET /a": [SIN_SESION, OK],
      "GET /b": [SIN_SESION, OK],
      "GET /c": [SIN_SESION, OK],
      "POST /auth/refrescar": [{}],
    });
    await Promise.all([solicitar("/a"), solicitar("/b"), solicitar("/c")]);
    expect(backend.cuantas("POST /auth/refrescar")).toBe(1);
    expect(backend.cuantas("GET /a") + backend.cuantas("GET /b") + backend.cuantas("GET /c")).toBe(6);
  });

  it("si el refresh falla devuelve el error original, avisa a la app y no entra en bucle", async () => {
    const backend = instalarBackend({ "GET /a": [SIN_SESION], "GET /b": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    const alPerder = vi.fn();
    registrarAlPerderSesion(alPerder);
    await expect(solicitar("/a")).rejects.toMatchObject({ estado: 401, codigo: "no_autenticado" });
    expect(alPerder).toHaveBeenCalledTimes(1);
    expect(backend.cuantas("GET /a")).toBe(1);
    // Mientras la sesión esté perdida no se vuelve a pegarle al refresh.
    await expect(solicitar("/b")).rejects.toBeInstanceOf(ErrorApi);
    expect(backend.cuantas("POST /auth/refrescar")).toBe(1);
  });

  it("un 429 del refresh no se reintenta", async () => {
    const backend = instalarBackend({
      "GET /a": [SIN_SESION],
      "POST /auth/refrescar": [{ status: 429, cuerpo: { detail: { codigo: "demasiados_intentos" } } }],
    });
    await expect(solicitar("/a")).rejects.toMatchObject({ estado: 401 });
    expect(backend.cuantas("POST /auth/refrescar")).toBe(1);
  });

  it("si el segundo intento también da 401 no vuelve a refrescar", async () => {
    const backend = instalarBackend({ "GET /a": [SIN_SESION], "POST /auth/refrescar": [{}] });
    await expect(solicitar("/a")).rejects.toMatchObject({ estado: 401 });
    expect(backend.cuantas("POST /auth/refrescar")).toBe(1);
    expect(backend.cuantas("GET /a")).toBe(2);
  });

  it.each([
    ["POST", "/auth/login"],
    ["POST", "/auth/mfa/verificar"],
    ["POST", "/auth/mfa/enrolar"],
  ] as const)("%s %s no dispara refresh", async (metodo, ruta) => {
    const backend = instalarBackend({ [`${metodo} ${ruta}`]: [SIN_SESION], "POST /auth/refrescar": [{}] });
    await expect(solicitar(ruta, { metodo })).rejects.toMatchObject({ estado: 401 });
    expect(backend.cuantas("POST /auth/refrescar")).toBe(0);
  });
});
