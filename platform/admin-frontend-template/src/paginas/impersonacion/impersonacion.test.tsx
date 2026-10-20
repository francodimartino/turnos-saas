import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarEn } from "../../test/montar";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ID = "aaaaaaaa-0000-0000-0000-000000000001";
const BASE = `/tenants/${ID}/impersonar`;

function backendBase() {
  return instalarBackend({
    "GET /auth/sesion": [sesionDe({ aal: "aal2" })],
    [`GET /tenants/${ID}`]: [
      {
        cuerpo: {
          id: ID,
          name: "Peluquería Ana",
          slug: "peluqueria-ana",
          plan: "pilot",
          status: "active",
          deactivated_at: null,
          paid_until: null,
          created_at: "2026-10-01T00:00:00Z",
          invitacion_pendiente: false,
        },
      },
    ],
    [`GET ${BASE}/bookings`]: [
      {
        cuerpo: [
          {
            id: "b-1",
            start_at: "2026-10-05T14:30:00Z",
            end_at: "2026-10-05T15:00:00Z",
            status: "confirmed",
            customer_name: "María Pérez",
            service_name: "Corte",
            professional_name: "Ana",
          },
        ],
      },
    ],
    [`GET ${BASE}/clientes`]: [
      { cuerpo: [{ id: "c-1", name: "María Pérez", phone: "+5491100000000", channel: "telegram", deleted_at: null }] },
    ],
    [`GET ${BASE}/servicios`]: [
      { cuerpo: [{ id: "s-1", name: "Corte", duration_minutes: 30, price: "5000.00", is_active: true }] },
    ],
    [`GET ${BASE}/profesionales`]: [{ cuerpo: [{ id: "p-1", name: "Ana", is_active: true }] }],
    [`GET ${BASE}/packs`]: [
      {
        cuerpo: [
          {
            id: "k-1",
            end_customer_id: "c-1",
            total_sessions: 5,
            used_sessions: 2,
            payment_status: "paid",
            expires_at: null,
          },
        ],
      },
    ],
  });
}

describe("impersonación de solo lectura", () => {
  it("muestra el banner permanente de solo lectura, también al cambiar de vista", async () => {
    backendBase();
    const usuario = userEvent.setup();
    montarEn(BASE);
    const banner = await screen.findByText(/Modo solo lectura/);
    await waitFor(() => expect(banner.closest(".banner-solo-lectura")).toHaveTextContent("viendo Peluquería Ana"));

    for (const vista of ["Clientes", "Servicios", "Profesionales", "Packs", "Turnos"]) {
      await usuario.click(screen.getByRole("button", { name: vista }));
      await screen.findByRole("heading", { name: vista });
      expect(screen.getByText(/Modo solo lectura/)).toBeInTheDocument();
    }
  });

  it("pide los turnos con el rango obligatorio en hora de Buenos Aires y los muestra", async () => {
    const backend = backendBase();
    montarEn(BASE);
    const fila = (await screen.findByText("María Pérez")).closest("tr")!;
    expect(within(fila).getByText(/05\/10\/2026.*11:30/)).toBeInTheDocument();
    expect(within(fila).getByText("Confirmado")).toBeInTheDocument();
    const llamada = backend.llamadas.find((l) => l.clave === `GET ${BASE}/bookings`)!;
    const q = new URL(llamada.url, "http://x").searchParams;
    expect(q.get("desde")).toMatch(/T00:00:00-03:00$/);
    expect(q.get("hasta")).toMatch(/T23:59:59-03:00$/);
  });

  it("recorre clientes, servicios, profesionales y packs", async () => {
    backendBase();
    const usuario = userEvent.setup();
    montarEn(BASE);
    await usuario.click(await screen.findByRole("button", { name: "Clientes" }));
    expect(await screen.findByText("+5491100000000")).toBeInTheDocument();
    await usuario.click(screen.getByRole("button", { name: "Servicios" }));
    expect(await screen.findByText("5000.00")).toBeInTheDocument();
    await usuario.click(screen.getByRole("button", { name: "Profesionales" }));
    expect(await screen.findByText("Ana")).toBeInTheDocument();
    await usuario.click(screen.getByRole("button", { name: "Packs" }));
    expect(await screen.findByText("2 de 5")).toBeInTheDocument();
  });

  it("no ofrece ningún control de escritura y solo hace GET", async () => {
    const backend = backendBase();
    const usuario = userEvent.setup();
    const { container } = montarEn(BASE);
    for (const vista of ["Clientes", "Servicios", "Profesionales", "Packs"]) {
      await usuario.click(await screen.findByRole("button", { name: vista }));
      await screen.findByRole("heading", { name: vista });
    }
    await usuario.click(screen.getByRole("button", { name: "Turnos" }));
    await screen.findByText("María Pérez");

    // Nada de campos de edición (solo el rango de fechas de turnos) ni botones de acción sobre los datos.
    const botones = screen.getAllByRole("button").map((b) => b.textContent);
    expect(botones).toEqual(expect.arrayContaining(["Cerrar sesión", "Ver turnos"]));
    for (const palabra of [/guardar/i, /editar/i, /eliminar/i, /borrar/i, /cancelar turno/i, /crear/i]) {
      expect(botones.filter((t) => palabra.test(t ?? ""))).toEqual([]);
    }
    expect(container.querySelectorAll("textarea, input[type=text], input[type=email], input[type=password]")).toHaveLength(
      0,
    );
    const metodos = backend.llamadas.map((l) => l.clave.split(" ")[0]);
    expect(metodos.every((m) => m === "GET")).toBe(true);
  });

  it("muestra el error si el backend rechaza la consulta", async () => {
    instalarBackend({
      "GET /auth/sesion": [sesionDe({ aal: "aal2" })],
      [`GET /tenants/${ID}`]: [{ status: 404, cuerpo: { detail: { codigo: "tenant_no_encontrado", mensaje: "x" } } }],
      [`GET ${BASE}/bookings`]: [
        { status: 404, cuerpo: { detail: { codigo: "tenant_no_encontrado", mensaje: "El tenant no existe" } } },
      ],
    });
    montarEn(BASE);
    expect(await screen.findByText("El tenant no existe")).toBeInTheDocument();
    expect(screen.getByText(/Modo solo lectura/)).toBeInTheDocument();
  });
});
