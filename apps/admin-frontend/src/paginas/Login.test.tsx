import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ProveedorSesion } from "../auth/SesionContext";
import { instalarBackend, SIN_SESION, sesionDe, type RespuestaFalsa } from "../test/falsoBackend";

type OpcionesWidget = { sitekey: string; callback: (t: string) => void };

let opciones: OpcionesWidget[] = [];
const remover = vi.fn();

beforeEach(() => {
  opciones = [];
  remover.mockClear();
  window.turnstile = {
    render: (_c, o) => {
      opciones.push(o);
      return `w${opciones.length}`;
    },
    remove: remover,
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  delete window.turnstile;
});

function montarLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <ProveedorSesion>
        <App />
      </ProveedorSesion>
    </MemoryRouter>,
  );
}

async function llenar(usuario: ReturnType<typeof userEvent.setup>) {
  await usuario.type(await screen.findByLabelText("Email"), "admin@plataforma.com");
  await usuario.type(screen.getByLabelText("Contraseña"), "secreta123");
}

function backendLogin(respuestas: RespuestaFalsa[]) {
  return instalarBackend({
    "GET /auth/sesion": [SIN_SESION, sesionDe({ aal: "aal2" })],
    "POST /auth/refrescar": [SIN_SESION],
    "POST /auth/login": respuestas,
  });
}

const OK: RespuestaFalsa = { cuerpo: { siguiente_paso: "listo", expira_en: 3600 } };

describe("login sin Turnstile configurado", () => {
  it("no renderiza el widget, no carga el script y no manda turnstile_token", async () => {
    vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "");
    const backend = backendLogin([OK]);
    const usuario = userEvent.setup();
    montarLogin();
    await llenar(usuario);

    expect(screen.queryByTestId("turnstile")).not.toBeInTheDocument();
    expect(opciones).toHaveLength(0);
    expect(document.querySelector('script[src*="challenges.cloudflare.com"]')).toBeNull();

    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));
    await screen.findByRole("heading", { name: "Panel" });
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/login")?.cuerpo).toEqual({
      email: "admin@plataforma.com",
      password: "secreta123",
    });
  });
});

describe("login con Turnstile configurado", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "clave-publica");
  });

  it("renderiza el widget, bloquea el envío hasta tener token y lo manda como turnstile_token", async () => {
    const backend = backendLogin([OK]);
    const usuario = userEvent.setup();
    montarLogin();
    await llenar(usuario);

    await waitFor(() => expect(opciones).toHaveLength(1));
    expect(opciones[0]?.sitekey).toBe("clave-publica");
    expect(screen.getByRole("button", { name: "Ingresar" })).toBeDisabled();

    opciones[0]?.callback("token-abc");
    await waitFor(() => expect(screen.getByRole("button", { name: "Ingresar" })).toBeEnabled());
    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));

    await screen.findByRole("heading", { name: "Panel" });
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/login")?.cuerpo).toEqual({
      email: "admin@plataforma.com",
      password: "secreta123",
      turnstile_token: "token-abc",
    });
  });

  it("400 turnstile_invalido: mensaje claro y widget nuevo (el token es de un solo uso)", async () => {
    backendLogin([
      { status: 400, cuerpo: { detail: { codigo: "turnstile_invalido", mensaje: "Captcha inválido o ausente" } } },
    ]);
    const usuario = userEvent.setup();
    montarLogin();
    await llenar(usuario);
    await waitFor(() => expect(opciones).toHaveLength(1));
    opciones[0]?.callback("token-1");
    await waitFor(() => expect(screen.getByRole("button", { name: "Ingresar" })).toBeEnabled());
    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo validar el captcha");
    await waitFor(() => expect(opciones).toHaveLength(2));
    expect(remover).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Ingresar" })).toBeDisabled();
  });

  it("503 turnstile_no_disponible: avisa que el servicio de verificación no está disponible", async () => {
    backendLogin([
      { status: 503, cuerpo: { detail: { codigo: "turnstile_no_disponible", mensaje: "No se pudo verificar el captcha" } } },
    ]);
    const usuario = userEvent.setup();
    montarLogin();
    await llenar(usuario);
    await waitFor(() => expect(opciones).toHaveLength(1));
    opciones[0]?.callback("t");
    await waitFor(() => expect(screen.getByRole("button", { name: "Ingresar" })).toBeEnabled());
    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no está disponible en este momento");
  });
});

describe("429 demasiados_intentos", () => {
  const detalle = { codigo: "demasiados_intentos", mensaje: "Demasiados intentos, probá de nuevo más tarde" };

  async function disparar(retryAfter: string | null) {
    vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "");
    instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (entrada: RequestInfo | URL, init?: RequestInit) => {
      if (String(entrada).endsWith("/auth/login")) {
        return new Response(JSON.stringify({ detail: detalle }), {
          status: 429,
          headers: { "Content-Type": "application/json", ...(retryAfter ? { "Retry-After": retryAfter } : {}) },
        });
      }
      return base(entrada, init);
    });
    const usuario = userEvent.setup();
    montarLogin();
    await llenar(usuario);
    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));
    return screen.findByRole("alert");
  }

  it("muestra la espera en segundos según Retry-After", async () => {
    expect(await disparar("30")).toHaveTextContent("Demasiados intentos. Probá de nuevo en 30 segundos.");
  });

  it("muestra la espera en minutos cuando es larga", async () => {
    expect(await disparar("600")).toHaveTextContent("Probá de nuevo en 10 minutos.");
  });

  it("sin Retry-After usa un texto genérico", async () => {
    expect(await disparar(null)).toHaveTextContent("Probá de nuevo en unos minutos.");
  });
});
