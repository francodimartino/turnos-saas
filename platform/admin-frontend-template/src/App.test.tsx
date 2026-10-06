import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ProveedorSesion } from "./auth/SesionContext";
import { instalarBackend, SIN_ACCESO, SIN_SESION, sesionDe } from "./test/falsoBackend";

afterEach(() => {
  vi.unstubAllGlobals();
});

function montar(ruta: string) {
  return render(
    <MemoryRouter initialEntries={[ruta]}>
      <ProveedorSesion>
        <App />
      </ProveedorSesion>
    </MemoryRouter>,
  );
}

async function completarLogin(usuario: ReturnType<typeof userEvent.setup>) {
  await usuario.type(await screen.findByLabelText("Email"), "admin@plataforma.com");
  await usuario.type(screen.getByLabelText("Contraseña"), "secreta123");
  await usuario.click(screen.getByRole("button", { name: "Ingresar" }));
}

const FACTOR = "11111111-1111-1111-1111-111111111111";

describe("guardia de rutas", () => {
  it("sin sesión redirige a login", async () => {
    instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Panel" })).not.toBeInTheDocument();
  });

  it("con sesión aal2 muestra el panel y siempre pide credentials: include", async () => {
    const backend = instalarBackend({ "GET /auth/sesion": [sesionDe({ aal: "aal2" })] });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    expect(backend.llamadas.every((l) => l.credentials === "include")).toBe(true);
  });

  it("renueva con el refresh token cuando el access token venció", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ aal: "aal2" })],
      "POST /auth/refrescar": [{ cuerpo: { expira_en: 3600 } }],
    });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    expect(backend.cuantas("POST /auth/refrescar")).toBe(1);
  });

  it("una sesión aal1 no ve el panel: va a login", async () => {
    instalarBackend({ "GET /auth/sesion": [sesionDe({ aal: "aal1" })] });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Panel" })).not.toBeInTheDocument();
  });

  it("una cuenta que no es administradora ve el error de acceso, no el panel", async () => {
    instalarBackend({ "GET /auth/sesion": [SIN_ACCESO] });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Sin acceso" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("no es administradora de la plataforma");
    expect(screen.queryByRole("heading", { name: "Panel" })).not.toBeInTheDocument();
  });
});

describe("login", () => {
  it("login exitoso con MFA ya verificada entra al panel, sin guardar nada en localStorage", async () => {
    const guardar = vi.spyOn(Storage.prototype, "setItem");
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ aal: "aal2" })],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [{ cuerpo: { siguiente_paso: "listo", expira_en: 3600 } }],
    });
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);

    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/login")?.cuerpo).toEqual({
      email: "admin@plataforma.com",
      password: "secreta123",
    });
    expect(guardar).not.toHaveBeenCalled();
  });

  it("muestra el error de credenciales y se queda en login", async () => {
    instalarBackend({
      "GET /auth/sesion": [SIN_SESION],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [
        { status: 401, cuerpo: { detail: { codigo: "credenciales_invalidas", mensaje: "Email o contraseña incorrectos" } } },
      ],
    });
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);

    expect(await screen.findByRole("alert")).toHaveTextContent("Email o contraseña incorrectos");
    expect(screen.getByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ingresar" })).toBeEnabled();
  });

  it("una cuenta que no es administradora (403) ve un mensaje claro y no entra", async () => {
    instalarBackend({
      "GET /auth/sesion": [SIN_SESION],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [SIN_ACCESO],
    });
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);

    expect(await screen.findByRole("alert")).toHaveTextContent("no es administradora de la plataforma");
    expect(screen.queryByRole("heading", { name: "Panel" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ingresar" })).toBeEnabled();
  });

  it("muestra un mensaje si no hay conexión con el servidor", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fallo de red")));
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo conectar con el servidor");
  });
});

describe("MFA obligatoria", () => {
  it("fuerza el enrollment a un admin sin factor (aal1) y termina en el panel con aal2", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ aal: "aal2" })],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [{ cuerpo: { siguiente_paso: "enrolar_mfa", expira_en: 3600 } }],
      "POST /auth/mfa/enrolar": [
        {
          cuerpo: {
            factor_id: FACTOR,
            qr_code: "data:image/svg+xml;utf-8,<svg xmlns='http://www.w3.org/2000/svg'/>",
            secret: "JBSWY3DPEHPK3PXP",
            uri: "otpauth://totp/Turnos",
          },
        },
      ],
      "POST /auth/mfa/verificar": [{ cuerpo: { siguiente_paso: "listo", expira_en: 3600 } }],
    });
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);

    expect(await screen.findByRole("heading", { name: /Configurá la verificación/ })).toBeInTheDocument();
    expect(await screen.findByAltText(/Código QR/)).toBeInTheDocument();
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(backend.cuantas("POST /auth/mfa/enrolar")).toBe(1);

    await usuario.type(screen.getByLabelText("Código de 6 dígitos"), "123456");
    await usuario.click(screen.getByRole("button", { name: "Activar" }));

    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/mfa/verificar")?.cuerpo).toEqual({
      factor_id: FACTOR,
      codigo: "123456",
    });
  });

  it("verifica el código con el factor existente y muestra el error de un código incorrecto", async () => {
    const otro = "22222222-2222-2222-2222-222222222222";
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ aal: "aal2" })],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [{ cuerpo: { siguiente_paso: "verificar_mfa", factor_id: otro, expira_en: 3600 } }],
      "POST /auth/mfa/verificar": [
        {
          status: 401,
          cuerpo: { detail: { codigo: "codigo_mfa_invalido", mensaje: "El código de verificación es incorrecto o venció" } },
        },
        { cuerpo: { siguiente_paso: "listo", expira_en: 3600 } },
      ],
    });
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);

    const campo = await screen.findByLabelText("Código de 6 dígitos");
    await usuario.type(campo, "000000");
    await usuario.click(screen.getByRole("button", { name: "Verificar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("incorrecto o venció");

    await usuario.clear(campo);
    await usuario.type(campo, "654321");
    await usuario.click(screen.getByRole("button", { name: "Verificar" }));
    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    expect(backend.cuantas("POST /auth/mfa/enrolar")).toBe(0);
    expect(backend.llamadas.filter((l) => l.clave === "POST /auth/mfa/verificar")[1]?.cuerpo).toEqual({
      factor_id: otro,
      codigo: "654321",
    });
  });

  it("las pantallas de MFA sin un login previo mandan a login", async () => {
    instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    montar("/mfa/enrolar");
    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
  });
});

describe("logout", () => {
  it("cierra la sesión en el backend y vuelve a login", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [sesionDe({ aal: "aal2" })],
      "POST /auth/logout": [{ status: 204 }],
    });
    const usuario = userEvent.setup();
    montar("/");
    await usuario.click(await screen.findByRole("button", { name: "Cerrar sesión" }));

    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(backend.cuantas("POST /auth/logout")).toBe(1);
  });

  it("la pantalla de acceso denegado también permite cerrar sesión", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_ACCESO],
      "POST /auth/logout": [{ status: 204 }],
    });
    const usuario = userEvent.setup();
    montar("/");
    await usuario.click(await screen.findByRole("button", { name: "Cerrar sesión" }));

    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(backend.cuantas("POST /auth/logout")).toBe(1);
  });
});
