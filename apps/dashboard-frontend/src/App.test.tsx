import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ProveedorSesion } from "./auth/SesionContext";
import { instalarBackend, SIN_SESION, sesionDe } from "./test/falsoBackend";

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

const CREDENCIALES_MALAS = {
  status: 401,
  cuerpo: { detail: { codigo: "credenciales_invalidas", mensaje: "Email o contraseña incorrectos" } },
};

async function completarLogin(usuario: ReturnType<typeof userEvent.setup>) {
  await usuario.type(await screen.findByLabelText("Email"), "ana@negocio.com");
  await usuario.type(screen.getByLabelText("Contraseña"), "secreta123");
  await usuario.click(screen.getByRole("button", { name: "Ingresar" }));
}

describe("rutas protegidas", () => {
  it("sin sesión redirige a login", async () => {
    instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
  });

  it("con sesión aal2 muestra el panel y pide siempre credentials: include", async () => {
    const backend = instalarBackend({ "GET /auth/sesion": [sesionDe({ is_owner: true, aal: "aal2" })] });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    expect(backend.llamadas.every((l) => l.credentials === "include")).toBe(true);
  });

  it("renueva con el refresh token cuando el access token venció", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ is_owner: false, aal: "aal1" })],
      "POST /auth/refrescar": [{ cuerpo: { expira_en: 3600 } }],
    });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    expect(backend.cuantas("POST /auth/refrescar")).toBe(1);
  });

  it("un owner con sesión aal1 no ve el panel: va a login", async () => {
    instalarBackend({ "GET /auth/sesion": [sesionDe({ is_owner: true, aal: "aal1" })] });
    montar("/");
    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Panel" })).not.toBeInTheDocument();
  });
});

describe("login", () => {
  it("login exitoso entra al panel, sin guardar nada en localStorage", async () => {
    const guardar = vi.spyOn(Storage.prototype, "setItem");
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ is_owner: false, aal: "aal1" })],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [{ cuerpo: { siguiente_paso: "listo", expira_en: 3600 } }],
    });
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);

    expect(await screen.findByRole("heading", { name: "Panel" })).toBeInTheDocument();
    const login = backend.llamadas.find((l) => l.clave === "POST /auth/login");
    expect(login?.cuerpo).toEqual({ email: "ana@negocio.com", password: "secreta123" });
    expect(guardar).not.toHaveBeenCalled();
  });

  it("muestra el error de credenciales y se queda en login", async () => {
    instalarBackend({
      "GET /auth/sesion": [SIN_SESION],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [CREDENCIALES_MALAS],
    });
    const usuario = userEvent.setup();
    montar("/login");
    await completarLogin(usuario);

    expect(await screen.findByRole("alert")).toHaveTextContent("Email o contraseña incorrectos");
    expect(screen.getByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
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

describe("MFA", () => {
  it("fuerza el enrollment a un owner sin factor (aal1) y termina en el panel con aal2", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ is_owner: true, aal: "aal2" })],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [{ cuerpo: { siguiente_paso: "enrolar_mfa", expira_en: 3600 } }],
      "POST /auth/mfa/enrolar": [
        {
          cuerpo: {
            factor_id: "11111111-1111-1111-1111-111111111111",
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
    const verificar = backend.llamadas.find((l) => l.clave === "POST /auth/mfa/verificar");
    expect(verificar?.cuerpo).toEqual({ factor_id: "11111111-1111-1111-1111-111111111111", codigo: "123456" });
  });

  it("verifica el código con el factor existente y muestra el error de un código incorrecto", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION, sesionDe({ is_owner: true, aal: "aal2" })],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/login": [
        { cuerpo: { siguiente_paso: "verificar_mfa", factor_id: "22222222-2222-2222-2222-222222222222", expira_en: 3600 } },
      ],
      "POST /auth/mfa/verificar": [
        { status: 401, cuerpo: { detail: { codigo: "codigo_mfa_invalido", mensaje: "El código de verificación es incorrecto o venció" } } },
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
      factor_id: "22222222-2222-2222-2222-222222222222",
      codigo: "654321",
    });
  });

  it("las pantallas de MFA sin un login previo mandan a login", async () => {
    instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    montar("/mfa/enrolar");
    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
  });
});

describe("aceptar invitación", () => {
  it("manda token_hash y contraseña al backend y continúa según el siguiente paso", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [SIN_SESION],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/invitacion/aceptar": [{ cuerpo: { siguiente_paso: "listo", expira_en: 3600 } }],
    });
    const usuario = userEvent.setup();
    montar("/invitacion?token_hash=abc123");
    await usuario.type(await screen.findByLabelText("Contraseña nueva"), "una-clave-larga");
    await usuario.type(screen.getByLabelText("Repetí la contraseña"), "una-clave-larga");
    await usuario.click(screen.getByRole("button", { name: "Crear cuenta" }));

    await waitFor(() => expect(backend.cuantas("POST /auth/invitacion/aceptar")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /auth/invitacion/aceptar")?.cuerpo).toEqual({
      token_hash: "abc123",
      password: "una-clave-larga",
    });
  });

  it("un owner invitado sin MFA pasa al enrollment", async () => {
    instalarBackend({
      "GET /auth/sesion": [SIN_SESION],
      "POST /auth/refrescar": [SIN_SESION],
      "POST /auth/invitacion/aceptar": [{ cuerpo: { siguiente_paso: "enrolar_mfa", expira_en: 3600 } }],
      "POST /auth/mfa/enrolar": [{ cuerpo: { factor_id: "f", qr_code: "data:image/svg+xml;utf-8,<svg/>", secret: "S", uri: "u" } }],
    });
    const usuario = userEvent.setup();
    montar("/invitacion?token_hash=abc123");
    await usuario.type(await screen.findByLabelText("Contraseña nueva"), "una-clave-larga");
    await usuario.type(screen.getByLabelText("Repetí la contraseña"), "una-clave-larga");
    await usuario.click(screen.getByRole("button", { name: "Crear cuenta" }));
    expect(await screen.findByRole("heading", { name: /Configurá la verificación/ })).toBeInTheDocument();
  });

  it("valida que las contraseñas coincidan sin llamar al backend", async () => {
    const backend = instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    const usuario = userEvent.setup();
    montar("/invitacion?token_hash=abc123");
    await usuario.type(await screen.findByLabelText("Contraseña nueva"), "una-clave-larga");
    await usuario.type(screen.getByLabelText("Repetí la contraseña"), "otra-distinta-1");
    await usuario.click(screen.getByRole("button", { name: "Crear cuenta" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no coinciden");
    expect(backend.cuantas("POST /auth/invitacion/aceptar")).toBe(0);
  });

  it("sin token_hash en el enlace avisa que la invitación no es válida", async () => {
    instalarBackend({ "GET /auth/sesion": [SIN_SESION], "POST /auth/refrescar": [SIN_SESION] });
    montar("/invitacion");
    expect(await screen.findByRole("alert")).toHaveTextContent("no es válido");
  });
});
