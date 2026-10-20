import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaProfesionales } from "../catalogo/PaginaProfesionales";

const { irAUrlExterna } = vi.hoisted(() => ({ irAUrlExterna: vi.fn() }));
vi.mock("../../compartido/navegacion", async (original) => ({
  ...(await original<typeof import("../../compartido/navegacion")>()),
  irAUrlExterna,
}));

afterEach(() => {
  vi.unstubAllGlobals();
  irAUrlExterna.mockReset();
});

const ANA = { id: "p-1", name: "Ana", is_active: true, tenant_user_id: null };
const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const URL_GOOGLE = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x";
const ESTADO = (conectado: boolean, configurado = true) => ({
  cuerpo: {
    profesional_id: "p-1",
    conectado,
    conectado_desde: conectado ? "2026-03-01T15:00:00Z" : null,
    google_configurado: configurado,
  },
});

describe("Google Calendar de un profesional", () => {
  it("conectar pide la URL al backend y lleva al navegador a Google", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /profesionales/p-1/calendar": [ESTADO(false)],
      "POST /profesionales/p-1/calendar/conexion": () => ({ cuerpo: { url: URL_GOOGLE } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Google Calendar de Ana" }));
    expect(await screen.findByText(/No conectado/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Conectar Google Calendar" }));
    await waitFor(() => expect(irAUrlExterna).toHaveBeenCalledWith(URL_GOOGLE));
    expect(backend.cuantas("POST /profesionales/p-1/calendar/conexion")).toBe(1);
  });

  it("no sigue una URL que no sea https", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /profesionales/p-1/calendar": [ESTADO(false)],
      "POST /profesionales/p-1/calendar/conexion": () => ({ cuerpo: { url: "javascript:alert(1)" } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Google Calendar de Ana" }));
    await u.click(await screen.findByRole("button", { name: "Conectar Google Calendar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/inválida/);
    expect(irAUrlExterna).not.toHaveBeenCalled();
  });

  it("conectado muestra desde cuándo y desconectar pide confirmación", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /profesionales/p-1/calendar": [ESTADO(true), ESTADO(false)],
      "DELETE /profesionales/p-1/calendar": () => ({ cuerpo: { conectado: false, habia_conexion: true } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Google Calendar de Ana" }));
    expect(await screen.findByText(/Conectado desde el 01\/03\/2026/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Conectar Google Calendar" })).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Desconectar Google Calendar" }));
    expect(backend.cuantas("DELETE /profesionales/p-1/calendar")).toBe(0);
    await u.click(screen.getByRole("button", { name: "Confirmar desconexión" }));
    expect(await screen.findByText(/No conectado/)).toBeInTheDocument();
    expect(backend.cuantas("DELETE /profesionales/p-1/calendar")).toBe(1);
  });

  it("si Google no está configurado, avisa y no deja conectar", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /profesionales/p-1/calendar": [ESTADO(false, false)],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Google Calendar de Ana" }));
    expect(await screen.findByText(/todavía no está disponible/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Conectar Google Calendar" })).toBeDisabled();
  });

  it("un 403 del backend se muestra como mensaje", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /profesionales/p-1/calendar": [ESTADO(false)],
      "POST /profesionales/p-1/calendar/conexion": () => ({
        status: 403,
        cuerpo: { detail: { codigo: "permiso_insuficiente", mensaje: "Falta permiso" } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Google Calendar de Ana" }));
    await u.click(await screen.findByRole("button", { name: "Conectar Google Calendar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No tenés permiso/);
  });

  it("la vuelta de Google con éxito abre el panel del profesional y confirma la conexión", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /profesionales/p-1/calendar": [ESTADO(true)],
    });
    montarPantalla(<PaginaProfesionales />, "/profesionales?google_calendar=ok&profesional=p-1");
    expect(await screen.findByText(/Google Calendar quedó conectado/)).toBeInTheDocument();
    const panel = await screen.findByRole("region", { name: "Google Calendar de Ana" });
    expect(await within(panel).findByText(/Conectado desde/)).toBeInTheDocument();
  });

  it("la vuelta de Google con error muestra un mensaje claro y se puede cerrar", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />, "/profesionales?google_calendar=cancelado");
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se concedió el acceso/);
    await u.click(screen.getByRole("button", { name: "Cerrar aviso" }));
    expect(screen.queryByText(/No se concedió el acceso/)).not.toBeInTheDocument();
  });

  it("un resultado desconocido cae en un mensaje genérico", async () => {
    instalarBackend({ "GET /auth/sesion": [OWNER], "GET /profesionales": [{ cuerpo: [ANA] }] });
    montarPantalla(<PaginaProfesionales />, "/profesionales?google_calendar=rarisimo");
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudo completar la conexión/);
  });
});
