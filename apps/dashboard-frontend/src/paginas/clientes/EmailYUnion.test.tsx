import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaClientes } from "./PaginaClientes";

afterEach(() => vi.unstubAllGlobals());

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const SETTINGS = { cuerpo: { settings: { end_customer_extra_fields: [] } } };
const BASE = {
  channel: "telegram",
  deleted_at: null,
  created_at: "2026-01-01T10:00:00Z",
  sin_cliente_identificable: false,
};
const ANA = {
  ...BASE,
  id: "c-1",
  phone: "+5491155551234",
  name: "Ana Pérez",
  email: "ana@ejemplo.com",
  canales: [
    { canal: "telegram", verificado: true },
    { canal: "email", verificado: false },
  ],
};
const BETO = { ...BASE, id: "c-2", phone: "+5491166660000", name: "Beto Gómez", email: null, canales: [] };
const LISTA = (...clientes: unknown[]) => ({ cuerpo: { clientes, limite: 50, offset: 0 } });
const DETALLE_ANA = { cuerpo: { ...ANA, extra_data: {} } };
const ID_BETO = "11111111-1111-4111-8111-111111111111";
const BETO_UUID = { ...BETO, id: ID_BETO };

function error(estado: number, codigo: string, extra: Record<string, unknown> = {}) {
  return () => ({
    status: estado,
    cuerpo: { detail: { codigo, mensaje: "TEXTO-DEL-SERVIDOR", ...extra } },
  });
}

describe("email en el alta y la edición", () => {
  it("el alta manda el email; sin email no manda el campo", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA()],
      "POST /clientes": () => ({ status: 201, cuerpo: ANA }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.type(screen.getByLabelText("Teléfono"), "1155551234");
    await u.type(screen.getByLabelText("Email"), "ana@ejemplo.com");
    await u.type(screen.getByLabelText("Nombre"), "Ana Pérez");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(backend.cuantas("POST /clientes")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /clientes")?.cuerpo).toEqual({
      phone: "1155551234",
      name: "Ana Pérez",
      email: "ana@ejemplo.com",
      extra_data: {},
    });
  });

  it("un email mal escrito se marca junto al campo sin llamar al backend", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA()],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.type(screen.getByLabelText("Teléfono"), "1155551234");
    await u.type(screen.getByLabelText("Email"), "ana@");
    await u.type(screen.getByLabelText("Nombre"), "Ana");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(/email válido/);
    expect(backend.cuantas("POST /clientes")).toBe(0);
  });

  it("422 email_invalido del backend se muestra junto al campo, sin el texto del servidor", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA()],
      "POST /clientes": error(422, "email_invalido"),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.type(screen.getByLabelText("Teléfono"), "1155551234");
    await u.type(screen.getByLabelText("Email"), "ana@ejemplo.com");
    await u.type(screen.getByLabelText("Nombre"), "Ana");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(/email válido/));
    expect(screen.queryByText(/TEXTO-DEL-SERVIDOR/)).not.toBeInTheDocument();
  });

  it("409 cliente_existente ofrece un enlace a la ficha existente", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [DETALLE_ANA],
      "POST /clientes": error(409, "cliente_existente", { cliente_id: "c-1" }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Nuevo cliente" }));
    await u.type(screen.getByLabelText("Teléfono"), "1100000000");
    await u.type(screen.getByLabelText("Email"), "ana@ejemplo.com");
    await u.type(screen.getByLabelText("Nombre"), "Otra");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    const enlace = await screen.findByRole("link", { name: "Ver el cliente existente" });
    expect(enlace).toHaveAttribute("href", "/clientes?cliente=c-1");
    expect(screen.getByLabelText("Email")).toHaveAccessibleDescription(/Ya existe un cliente con ese email/);
    expect(screen.queryByText(/TEXTO-DEL-SERVIDOR/)).not.toBeInTheDocument();
  });

  it("editar: manda solo el email cambiado y permite borrarlo con null", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [DETALLE_ANA],
      "PATCH /clientes/c-1": () => ({ cuerpo: ANA }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await u.click(await screen.findByRole("button", { name: "Editar cliente" }));
    await u.clear(screen.getByLabelText("Email"));
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(backend.cuantas("PATCH /clientes/c-1")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "PATCH /clientes/c-1")?.cuerpo).toEqual({ email: null });
  });
});

describe("detalle: email y canales", () => {
  it("lista el email y cada canal con su estado de verificación", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /local/settings": [SETTINGS],
      "GET /clientes": [LISTA(ANA)],
      "GET /clientes/c-1": [DETALLE_ANA],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    const detalle = await screen.findByRole("region", { name: "Detalle del cliente" });
    expect(await within(detalle).findByText("ana@ejemplo.com")).toBeInTheDocument();
    const canales = within(detalle).getByRole("list", { name: "Canales del cliente" });
    expect(within(canales).getByText("Telegram (verificado)")).toBeInTheDocument();
    expect(within(canales).getByText("Email (sin verificar)")).toBeInTheDocument();
  });
});

describe("unir dos fichas", () => {
  async function abrirUnion(u: ReturnType<typeof userEvent.setup>) {
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await u.click(await screen.findByRole("button", { name: "Unir con otra ficha" }));
    await u.type(screen.getByLabelText(/Buscar la otra ficha/), "Beto");
    await u.click(screen.getByRole("button", { name: "Buscar ficha" }));
    await u.click(await screen.findByRole("button", { name: "Elegir a Beto Gómez" }));
  }

  const RUTAS = {
    "GET /auth/sesion": [OWNER],
    "GET /local/settings": [SETTINGS],
    "GET /clientes": [LISTA(ANA, BETO_UUID)],
    "GET /clientes/c-1": [DETALLE_ANA],
  };

  it("busca, explica qué ficha queda y recién al confirmar llama al backend", async () => {
    const backend = instalarBackend({
      ...RUTAS,
      "POST /clientes/c-1/unir": () => ({
        cuerpo: { absorbida_id: ID_BETO, avisos: 0, canales: 0, cliente: ANA, packs: 0, registros_envio: 0, turnos: 3 },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await abrirUnion(u);
    // La búsqueda no incluye la propia ficha, y todavía no se llamó al endpoint.
    expect(backend.cuantas("POST /clientes/c-1/unir")).toBe(0);
    const grupo = screen.getByRole("group", { name: "Confirmar unión de fichas" });
    expect(within(grupo).getByText(/irreversible/)).toBeInTheDocument();
    expect(within(grupo).getByText(/Queda la ficha de/)).toHaveTextContent("Ana Pérez");
    expect(within(grupo).getByText(/se anonimiza/)).toHaveTextContent("Beto Gómez");
    await u.click(within(grupo).getByRole("button", { name: "Confirmar unión" }));
    expect(await screen.findByText(/Las fichas se unieron/)).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "POST /clientes/c-1/unir")?.cuerpo).toEqual({
      absorber_id: ID_BETO,
    });
  });

  it("cancelar la confirmación no llama al backend", async () => {
    const backend = instalarBackend(RUTAS);
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await abrirUnion(u);
    await u.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.getByRole("button", { name: "Unir con otra ficha" })).toBeInTheDocument();
    expect(backend.cuantas("POST /clientes/c-1/unir")).toBe(0);
  });

  it.each([
    [409, "canal_en_conflicto", /cuenta distinta del mismo canal/],
    [409, "cliente_anonimizado", /ya fue anonimizada/],
    [422, "union_misma_ficha", /consigo misma/],
    [404, "cliente_no_encontrado", /No encontramos una de las fichas/],
    [403, "sin_permiso", /No tenés permiso/],
  ])("el %i %s se muestra con un mensaje propio, sin el texto del servidor", async (estado, codigo, mensaje) => {
    instalarBackend({ ...RUTAS, "POST /clientes/c-1/unir": error(estado, codigo) });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await abrirUnion(u);
    await u.click(screen.getByRole("button", { name: "Confirmar unión" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(mensaje);
    expect(screen.queryByText(/TEXTO-DEL-SERVIDOR/)).not.toBeInTheDocument();
  });

  it("sin manage_customers no se ofrece unir ni editar", async () => {
    instalarBackend({
      ...RUTAS,
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings"] })],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaClientes />);
    await u.click(await screen.findByRole("button", { name: "Ver Ana Pérez" }));
    await screen.findByRole("region", { name: "Detalle del cliente" });
    await screen.findByText("ana@ejemplo.com");
    expect(screen.queryByRole("button", { name: "Unir con otra ficha" })).not.toBeInTheDocument();
  });
});
