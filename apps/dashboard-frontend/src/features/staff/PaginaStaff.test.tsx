import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { CATALOGO_PERMISOS } from "./catalogoPermisos";
import { PaginaStaff } from "./PaginaStaff";

afterEach(() => vi.unstubAllGlobals());

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const GESTOR = sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_staff"] });
const YO = {
  id: "u-1",
  is_owner: true,
  is_active: true,
  es_yo: true,
  created_at: "2026-01-01T10:00:00Z",
  permission_keys: [],
};
const BETO = {
  id: "u-2",
  is_owner: false,
  is_active: true,
  es_yo: false,
  created_at: "2026-01-02T10:00:00Z",
  permission_keys: ["view_bookings"],
};
const CARLA = { ...BETO, id: "u-3", is_active: false, permission_keys: [] };
const STAFF = (...staff: unknown[]) => ({ cuerpo: { staff } });

describe("catálogo de permisos", () => {
  it("tiene los 8 permisos de ARCHITECTURE §2.4, cada uno con descripción en español", () => {
    expect(CATALOGO_PERMISOS.map((p) => p.clave).sort()).toEqual(
      [
        "approve_bookings",
        "delete_customers",
        "edit_prices",
        "manage_customers",
        "manage_schedule",
        "manage_staff",
        "view_bookings",
        "view_reports",
      ].sort(),
    );
    expect(CATALOGO_PERMISOS.every((p) => p.descripcion.length > 10 && p.etiqueta.length > 0)).toBe(true);
  });
});

describe("PaginaStaff", () => {
  it("el último owner no se puede desactivar: se muestra el 409", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /staff": [STAFF(YO, BETO)],
      "POST /staff/u-1/desactivar": () => ({
        status: 409,
        cuerpo: {
          detail: {
            codigo: "ultimo_owner",
            mensaje: "No se puede desactivar al último owner activo del local. Antes hacé owner a otra persona.",
          },
        },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaStaff />);
    await u.click(await screen.findByRole("button", { name: "Desactivar a Persona 1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/último owner/);
    expect(backend.cuantas("POST /staff/u-1/desactivar")).toBe(1);
    expect(backend.cuantas("GET /staff")).toBe(1);
  });

  it("desactiva y reactiva, y recarga la lista", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /staff": [STAFF(YO, BETO, CARLA), STAFF(YO, { ...BETO, is_active: false }, CARLA)],
      "POST /staff/u-2/desactivar": () => ({ cuerpo: { id: "u-2", is_owner: false, is_active: false } }),
      "POST /staff/u-3/reactivar": () => ({ cuerpo: { id: "u-3", is_owner: false, is_active: true } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaStaff />);
    await u.click(await screen.findByRole("button", { name: "Desactivar a Persona 2" }));
    expect(await screen.findByText("Persona 2 fue desactivada.")).toBeInTheDocument();
    await u.click(await screen.findByRole("button", { name: "Reactivar a Persona 3" }));
    await vi.waitFor(() => expect(backend.cuantas("POST /staff/u-3/reactivar")).toBe(1));
  });

  it("invitar: manda email y permisos elegidos, y avisa", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /staff": [STAFF(YO)],
      "POST /staff/invitaciones": () => ({ status: 201, cuerpo: { tenant_user_id: "u-9", creado: true } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaStaff />);
    await u.click(await screen.findByRole("button", { name: "Invitar a una persona" }));
    await u.type(screen.getByLabelText("Email de la persona"), "nuevo@negocio.com");
    await u.click(screen.getByRole("checkbox", { name: /Gestionar clientes/ }));
    await u.click(screen.getByRole("button", { name: "Enviar invitación" }));
    expect(await screen.findByText(/Invitación enviada/)).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "POST /staff/invitaciones")?.cuerpo).toEqual({
      email: "nuevo@negocio.com",
      permission_keys: ["view_bookings", "manage_customers"],
    });
  });

  it("invitar valida el email sin llamar al backend", async () => {
    const backend = instalarBackend({ "GET /auth/sesion": [OWNER], "GET /staff": [STAFF(YO)] });
    const u = userEvent.setup();
    montarPantalla(<PaginaStaff />);
    await u.click(await screen.findByRole("button", { name: "Invitar a una persona" }));
    await u.type(screen.getByLabelText("Email de la persona"), "no-es-email");
    await u.click(screen.getByRole("button", { name: "Enviar invitación" }));
    expect(screen.getByLabelText("Email de la persona")).toHaveAccessibleDescription(/email válido/);
    expect(backend.cuantas("POST /staff/invitaciones")).toBe(0);
  });

  it("invitaciones no configuradas (503) se explican en el formulario", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /staff": [STAFF(YO)],
      "POST /staff/invitaciones": () => ({
        status: 503,
        cuerpo: {
          detail: {
            codigo: "invitaciones_no_configuradas",
            mensaje: "No se pueden enviar invitaciones por ahora. Avisá a soporte.",
          },
        },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaStaff />);
    await u.click(await screen.findByRole("button", { name: "Invitar a una persona" }));
    await u.type(screen.getByLabelText("Email de la persona"), "nuevo@negocio.com");
    await u.click(screen.getByRole("button", { name: "Enviar invitación" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Avisá a soporte/);
  });

  it("el owner edita los permisos de otra persona (reemplaza el set)", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /staff": [STAFF(YO, BETO), STAFF(YO, { ...BETO, permission_keys: ["view_bookings", "edit_prices"] })],
      "PUT /staff/u-2/permisos": () => ({ cuerpo: { id: "u-2", permission_keys: [] } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaStaff />);
    await u.click(await screen.findByRole("button", { name: "Editar permisos de Persona 2" }));
    expect(screen.getByRole("checkbox", { name: /Ver turnos/ })).toBeChecked();
    await u.click(screen.getByRole("checkbox", { name: /Editar precios/ }));
    await u.click(screen.getByRole("button", { name: "Guardar permisos" }));
    expect(await screen.findByText("Permisos actualizados.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PUT /staff/u-2/permisos")?.cuerpo).toEqual({
      permission_keys: ["view_bookings", "edit_prices"],
    });
  });

  it("el owner da el rol de owner a otra persona", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /staff": [STAFF(YO, BETO)],
      "PUT /staff/u-2/owner": () => ({ cuerpo: { id: "u-2", is_owner: true, is_active: true } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaStaff />);
    await u.click(await screen.findByRole("button", { name: "Hacer owner a Persona 2" }));
    await vi.waitFor(() => expect(backend.cuantas("PUT /staff/u-2/owner")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "PUT /staff/u-2/owner")?.cuerpo).toEqual({ is_owner: true });
  });

  it("con manage_staff pero sin ser owner no hay invitar, permisos ni owner, ni acciones sobre owners", async () => {
    instalarBackend({
      "GET /auth/sesion": [GESTOR],
      "GET /staff": [STAFF(YO, BETO)],
    });
    montarPantalla(<PaginaStaff />);
    expect(await screen.findByRole("button", { name: "Desactivar a Persona 2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Desactivar a Persona 1" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invitar a una persona" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Editar permisos/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /owner a/ })).not.toBeInTheDocument();
  });
});
