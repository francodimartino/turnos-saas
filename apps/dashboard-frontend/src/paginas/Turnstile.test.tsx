import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ProveedorSesion } from "../auth/SesionContext";
import { instalarBackend, SIN_SESION } from "../test/falsoBackend";

interface OpcionesFalsas {
  callback: (token: string) => void;
  "expired-callback": () => void;
  "error-callback": () => void;
}

let opciones: OpcionesFalsas | null = null;
const reset = vi.fn();
const remove = vi.fn();
const renderFalso = vi.fn((_contenedor: HTMLElement, o: OpcionesFalsas) => {
  opciones = o;
  return "widget-1";
});

beforeEach(() => {
  opciones = null;
  renderFalso.mockClear();
  reset.mockClear();
  remove.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  delete window.turnstile;
});

function activarWidget() {
  vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "1x00000000000000000000AA");
  window.turnstile = { render: renderFalso, reset, remove };
}

function montar(ruta: string) {
  return render(
    <MemoryRouter initialEntries={[ruta]}>
      <ProveedorSesion>
        <App />
      </ProveedorSesion>
    </MemoryRouter>,
  );
}

async function resolverDesafio(token: string) {
  await waitFor(() => expect(opciones).not.toBeNull());
  act(() => opciones!.callback(token));
}

const OK = { cuerpo: { siguiente_paso: "listo", expira_en: 3600 } };

describe("Turnstile en el login", () => {
  it("sin site key no hay widget ni se manda turnstile_token", async () => {
    const usuario = userEvent.setup();
    const backend = instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/login": [OK] });
    montar("/login");
    await usuario.type(await screen.findByLabelText("Email"), "ana@negocio.com");
    await usuario.type(screen.getByLabelText("Contraseña"), "secreta123");
    expect(screen.queryByTestId("turnstile")).not.toBeInTheDocument();
    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));
    await waitFor(() => expect(backend.cuantas("POST /auth/login")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/login")?.cuerpo).toEqual({
      email: "ana@negocio.com",
      password: "secreta123",
    });
    expect(renderFalso).not.toHaveBeenCalled();
    expect(document.querySelector('script[src*="challenges.cloudflare.com"]')).toBeNull();
  });

  it("con site key renderiza el widget, bloquea el envío hasta tener token y lo manda", async () => {
    activarWidget();
    const usuario = userEvent.setup();
    const backend = instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/login": [OK] });
    montar("/login");
    await usuario.type(await screen.findByLabelText("Email"), "ana@negocio.com");
    await usuario.type(screen.getByLabelText("Contraseña"), "secreta123");
    expect(screen.getByTestId("turnstile")).toBeInTheDocument();
    await waitFor(() => expect(renderFalso).toHaveBeenCalledTimes(1));
    expect(renderFalso.mock.calls[0]?.[1]).toMatchObject({ sitekey: "1x00000000000000000000AA" });
    expect(screen.getByRole("button", { name: "Ingresar" })).toBeDisabled();

    await resolverDesafio("tok-123");
    expect(screen.getByRole("button", { name: "Ingresar" })).toBeEnabled();
    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));
    await waitFor(() => expect(backend.cuantas("POST /auth/login")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/login")?.cuerpo).toEqual({
      email: "ana@negocio.com",
      password: "secreta123",
      turnstile_token: "tok-123",
    });
  });

  it.each([
    [400, "turnstile_invalido", "No pudimos verificar que sos una persona. Probá de nuevo."],
    [
      503,
      "turnstile_no_disponible",
      "La verificación anti-bots no está disponible en este momento. Probá de nuevo en unos segundos.",
    ],
    [429, "demasiados_intentos", "Demasiados intentos. Esperá un rato antes de volver a probar."],
  ])("ante un %i %s muestra el mensaje y reinicia el widget", async (status, codigo, mensaje) => {
    activarWidget();
    const usuario = userEvent.setup();
    instalarBackend({
      "GET /auth/sesion": [SIN_SESION],
      "POST /auth/login": [{ status, cuerpo: { detail: { codigo, mensaje: "texto del servidor" } } }],
    });
    montar("/login");
    await usuario.type(await screen.findByLabelText("Email"), "ana@negocio.com");
    await usuario.type(screen.getByLabelText("Contraseña"), "secreta123");
    await resolverDesafio("tok-1");
    await usuario.click(screen.getByRole("button", { name: "Ingresar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(mensaje);
    expect(reset).toHaveBeenCalledWith("widget-1");
    expect(screen.getByRole("button", { name: "Ingresar" })).toBeDisabled();
  });
});

describe("Turnstile en aceptar invitación", () => {
  async function completar(usuario: ReturnType<typeof userEvent.setup>) {
    await usuario.type(await screen.findByLabelText("Contraseña nueva"), "claveLarga1");
    await usuario.type(screen.getByLabelText("Repetí la contraseña"), "claveLarga1");
  }

  it("sin site key no hay widget ni se manda turnstile_token", async () => {
    const usuario = userEvent.setup();
    const backend = instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/invitacion/aceptar": [OK] });
    montar("/invitacion?token_hash=abc");
    await completar(usuario);
    expect(screen.queryByTestId("turnstile")).not.toBeInTheDocument();
    await usuario.click(screen.getByRole("button", { name: "Crear cuenta" }));
    await waitFor(() => expect(backend.cuantas("POST /auth/invitacion/aceptar")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/invitacion/aceptar")?.cuerpo).toEqual({
      token_hash: "abc",
      password: "claveLarga1",
    });
  });

  it("con site key bloquea el envío hasta tener token y lo manda", async () => {
    activarWidget();
    const usuario = userEvent.setup();
    const backend = instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/invitacion/aceptar": [OK] });
    montar("/invitacion?token_hash=abc");
    await completar(usuario);
    expect(screen.getByTestId("turnstile")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Crear cuenta" })).toBeDisabled();
    await resolverDesafio("tok-9");
    await usuario.click(screen.getByRole("button", { name: "Crear cuenta" }));
    await waitFor(() => expect(backend.cuantas("POST /auth/invitacion/aceptar")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/invitacion/aceptar")?.cuerpo).toEqual({
      token_hash: "abc",
      password: "claveLarga1",
      turnstile_token: "tok-9",
    });
  });

  it("ante un 400 turnstile_invalido muestra el mensaje y reinicia el widget", async () => {
    activarWidget();
    const usuario = userEvent.setup();
    instalarBackend({
      "GET /auth/sesion": [SIN_SESION],
      "POST /auth/invitacion/aceptar": [
        { status: 400, cuerpo: { detail: { codigo: "turnstile_invalido", mensaje: "x" } } },
      ],
    });
    montar("/invitacion?token_hash=abc");
    await completar(usuario);
    await resolverDesafio("tok-9");
    await usuario.click(screen.getByRole("button", { name: "Crear cuenta" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No pudimos verificar que sos una persona");
    expect(reset).toHaveBeenCalledWith("widget-1");
  });
});
