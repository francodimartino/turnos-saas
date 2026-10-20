import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaClientes } from "./PaginaClientes";

afterEach(() => vi.unstubAllGlobals());

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const ANA = {
  id: "c-1",
  channel: "telegram",
  phone: "+5491155551234",
  name: "Ana Pérez",
  deleted_at: null,
  created_at: "2026-01-01T10:00:00Z",
  sin_cliente_identificable: false,
};
const SETTINGS = { cuerpo: { settings: { end_customer_extra_fields: [{ key: "obra_social", label: "Obra social" }] } } };
const LISTA = (...clientes: unknown[]) => ({ cuerpo: { clientes, limite: 50, offset: 0 } });
const ANONIMIZADO = {
  ...ANA,
  name: null,
  phone: null,
  deleted_at: "2026-02-01T10:00:00Z",
  sin_cliente_identificable: true,
  extra_data: {},
};

describe("PaginaClientes", () => {
  it("lista y busca por nombre o teléfono", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": (_c, url) =>
        url.includes("busqueda=Ana") ? LISTA(ANA) : LISTA(ANA, { ...ANA, id: "c-2", name: "Beto" }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    expect(await screen.findByText("Beto")).toBeInTheDocument();
    await u.type(screen.getByLabelText("Buscar por nombre o teléfono"), "Ana");
    await u.click(screen.getByRole("button", { name: "Buscar" }));
    await vi.waitFor(() => expect(screen.queryByText("Beto")).not.toBeInTheDocument());
    expect(backend.llamadas.some((l) => l.url.includes("busqueda=Ana"))).toBe(true);
  });

  it("alta manual: manda el teléfono tal cual, el nombre y los datos adicionales", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(), LISTA(ANA)],
      "POST /clientes": () => ({ status: 201, cuerpo: ANA }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.type(screen.getByLabelText("Teléfono"), "11 5555-1234");
    await u.type(screen.getByLabelText("Nombre"), "Ana Pérez");
    await u.type(await screen.findByLabelText("Obra social"), "OSDE");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Ana Pérez")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "POST /clientes")?.cuerpo).toEqual({
      phone: "11 5555-1234",
      name: "Ana Pérez",
      extra_data: { obra_social: "OSDE" },
    });
  });

  it("valida teléfono y nombre sin llamar al backend", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA()],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    expect(screen.getByLabelText("Teléfono")).toHaveAccessibleDescription(/teléfono/);
    expect(screen.getByLabelText("Nombre")).toHaveAccessibleDescription(/nombre/);
    expect(backend.cuantas("POST /clientes")).toBe(0);
  });

  it("un teléfono duplicado (409) ofrece un enlace al cliente existente", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [{ cuerpo: { ...ANA, extra_data: { obra_social: "OSDE" } } }],
      "POST /clientes": () => ({
        status: 409,
        cuerpo: {
          detail: { codigo: "cliente_duplicado", mensaje: "Ya existe un cliente con ese teléfono", cliente_id: "c-1" },
        },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.type(screen.getByLabelText("Teléfono"), "1155551234");
    await u.type(screen.getByLabelText("Nombre"), "Otra");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    const enlace = await screen.findByRole("link", { name: "Ver el cliente existente" });
    expect(enlace).toHaveAttribute("href", "/clientes?cliente=c-1");
    await u.click(enlace);
    const detalle = await screen.findByRole("region", { name: "Detalle del cliente" });
    expect(await within(detalle).findByText("OSDE")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Nuevo cliente" })).not.toBeInTheDocument();
  });

  it("un teléfono inválido (422) se muestra junto al campo", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA()],
      "POST /clientes": () => ({
        status: 422,
        cuerpo: { detail: { codigo: "telefono_invalido", mensaje: "Teléfono inválido" } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.type(screen.getByLabelText("Teléfono"), "123");
    await u.type(screen.getByLabelText("Nombre"), "Ana");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await vi.waitFor(() => expect(screen.getByLabelText("Teléfono")).toHaveAccessibleDescription(/Teléfono inválido/));
  });

  it("edita solo los campos que cambiaron", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [
        { cuerpo: { ...ANA, extra_data: {} } },
        { cuerpo: { ...ANA, name: "Ana María", extra_data: {} } },
      ],
      "PATCH /clientes/c-1": () => ({ cuerpo: ANA }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await u.click(await screen.findByRole("button", { name: "Editar cliente" }));
    const nombre = screen.getByLabelText("Nombre");
    await u.clear(nombre);
    await u.type(nombre, "Ana María");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("heading", { name: "Ana María" })).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PATCH /clientes/c-1")?.cuerpo).toEqual({ name: "Ana María" });
  });

  it("anonimizar exige escribir la palabra, explica los turnos y recién ahí llama al backend", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [{ cuerpo: { ...ANA, extra_data: {} } }, { cuerpo: ANONIMIZADO }],
      "POST /clientes/c-1/anonimizar": () => ({
        cuerpo: { id: "c-1", sin_cliente_identificable: true, calendario: { estado: "ok", eventos: 2, no_limpiados: 0 } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await u.click(await screen.findByRole("button", { name: "Anonimizar a Ana Pérez" }));
    expect(screen.getByText(/irreversible/)).toBeInTheDocument();
    expect(screen.getByText(/turnos futuros/i)).toBeInTheDocument();
    const confirmar = screen.getByRole("button", { name: "Confirmar anonimización" });
    expect(confirmar).toBeDisabled();
    await u.type(screen.getByLabelText(/Para confirmar, escribí/), "anonimizar");
    expect(confirmar).toBeDisabled();
    expect(backend.cuantas("POST /clientes/c-1/anonimizar")).toBe(0);
    await u.clear(screen.getByLabelText(/Para confirmar, escribí/));
    await u.type(screen.getByLabelText(/Para confirmar, escribí/), "ANONIMIZAR");
    await u.click(confirmar);
    expect(await screen.findByText(/fue anonimizado\. Ya no se puede/)).toBeInTheDocument();
    expect(backend.cuantas("POST /clientes/c-1/anonimizar")).toBe(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await screen.findByText(/no quedan datos que lo identifiquen/)).toBeInTheDocument();
  });

  it("si Google Calendar no se pudo limpiar, avisa que el cliente igual quedó anonimizado", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [{ cuerpo: { ...ANA, extra_data: {} } }],
      "POST /clientes/c-1/anonimizar": () => ({
        cuerpo: { id: "c-1", sin_cliente_identificable: true, calendario: { estado: "ok", eventos: 3, no_limpiados: 1 } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await u.click(await screen.findByRole("button", { name: "Anonimizar a Ana Pérez" }));
    await u.type(screen.getByLabelText(/Para confirmar, escribí/), "ANONIMIZAR");
    await u.click(screen.getByRole("button", { name: "Confirmar anonimización" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/1 de 3 evento/);
  });

  it("cancelar la confirmación no anonimiza", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [{ cuerpo: { ...ANA, extra_data: {} } }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await u.click(await screen.findByRole("button", { name: "Anonimizar a Ana Pérez" }));
    await u.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.getByRole("button", { name: "Anonimizar a Ana Pérez" })).toBeInTheDocument();
    expect(backend.cuantas("POST /clientes/c-1/anonimizar")).toBe(0);
  });

  it("los permisos ocultan alta, edición y anonimización", async () => {
    instalarBackend({
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings"] })],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [{ cuerpo: { ...ANA, extra_data: {} } }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await screen.findByRole("region", { name: "Detalle del cliente" });
    expect(screen.queryByRole("button", { name: "Nuevo cliente" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Editar cliente" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Anonimizar a/ })).not.toBeInTheDocument();
  });

  it("manage_customers no alcanza para anonimizar: hace falta delete_customers", async () => {
    instalarBackend({
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings", "manage_customers"] })],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [{ cuerpo: { ...ANA, extra_data: {} } }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    expect(await screen.findByRole("button", { name: "Editar cliente" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Anonimizar a/ })).not.toBeInTheDocument();
  });
});
