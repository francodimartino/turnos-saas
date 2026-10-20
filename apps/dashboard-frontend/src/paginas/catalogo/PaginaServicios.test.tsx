import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaServicios } from "./PaginaServicios";

afterEach(() => vi.unstubAllGlobals());

const CORTE = { id: "s-1", name: "Corte", duration_minutes: 30, price: "15000.00", deposit_amount: null, is_active: true };
const OWNER = sesionDe({ is_owner: true, aal: "aal2" });

describe("PaginaServicios", () => {
  it("crea un servicio con precio y duración y recarga la lista", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /servicios": [
        { cuerpo: [CORTE] },
        { cuerpo: [CORTE, { ...CORTE, id: "s-2", name: "Barba", price: "8000.50", duration_minutes: 20 }] },
      ],
      "POST /servicios": () => ({ status: 201, cuerpo: {} }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);

    expect(await screen.findByText("Corte")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Nuevo servicio" }));
    await u.type(screen.getByLabelText("Nombre"), "Barba");
    await u.type(screen.getByLabelText("Duración (minutos)"), "20");
    await u.type(screen.getByLabelText("Precio (ARS)"), "8000,50");
    await u.click(screen.getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText("Barba")).toBeInTheDocument();
    const alta = backend.llamadas.find((l) => l.clave === "POST /servicios");
    expect(alta?.cuerpo).toEqual({ name: "Barba", duration_minutes: 20, price: "8000.50", is_active: true });
    expect(alta?.credentials).toBe("include");
  });

  it("valida los campos antes de enviar", async () => {
    const backend = instalarBackend({ "GET /auth/sesion": [OWNER], "GET /servicios": [{ cuerpo: [] }] });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);
    await u.click(await screen.findByRole("button", { name: "Nuevo servicio" }));
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    expect(screen.getByLabelText("Nombre")).toHaveAccessibleDescription("Ingresá el nombre del servicio");
    expect(screen.getByLabelText("Duración (minutos)")).toBeInvalid();
    expect(backend.cuantas("POST /servicios")).toBe(0);
  });

  it("un 422 valor_invalido del backend se muestra junto al campo", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /servicios": [{ cuerpo: [] }],
      "POST /servicios": () => ({
        status: 422,
        cuerpo: { detail: { codigo: "valor_invalido", mensaje: "El precio no puede ser negativo." } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);
    await u.click(await screen.findByRole("button", { name: "Nuevo servicio" }));
    await u.type(screen.getByLabelText("Nombre"), "Corte");
    await u.type(screen.getByLabelText("Duración (minutos)"), "30");
    await u.type(screen.getByLabelText("Precio (ARS)"), "100");
    await u.click(screen.getByRole("button", { name: "Guardar" }));

    const precio = screen.getByLabelText("Precio (ARS)");
    const alerta = await within(precio.closest(".campo") as HTMLElement).findByRole("alert");
    expect(alerta).toHaveTextContent("El precio no puede ser negativo.");
    expect(precio).toHaveAccessibleDescription("El precio no puede ser negativo.");
    expect(screen.getByLabelText("Nombre")).toBeValid();
  });

  it("un 422 de forma (FastAPI) se muestra junto al campo que nombra", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /servicios": [{ cuerpo: [CORTE] }],
      "PATCH /servicios/s-1": () => ({
        status: 422,
        cuerpo: { detail: [{ loc: ["body", "name"], msg: "String should have at most 200 characters", type: "x" }] },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);
    await u.click(await screen.findByRole("button", { name: "Editar Corte" }));
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    const nombre = await screen.findByLabelText("Nombre");
    await waitFor(() => expect(nombre).toHaveAccessibleDescription("String should have at most 200 characters"));
  });

  it("editar envía el servicio modificado", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /servicios": [{ cuerpo: [CORTE] }],
      "PATCH /servicios/s-1": () => ({ cuerpo: {} }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);
    await u.click(await screen.findByRole("button", { name: "Editar Corte" }));
    const precio = screen.getByLabelText("Precio (ARS)");
    expect(precio).toHaveValue("15000.00");
    await u.clear(precio);
    await u.type(precio, "16000");
    await u.click(screen.getByLabelText(/Activo/));
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(backend.cuantas("PATCH /servicios/s-1")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "PATCH /servicios/s-1")?.cuerpo).toEqual({
      name: "Corte",
      duration_minutes: 30,
      price: "16000",
      is_active: false,
    });
  });

  it("borrar con 409 en_uso explica que hay que desactivarlo", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /servicios": [{ cuerpo: [CORTE] }],
      "DELETE /servicios/s-1": () => ({
        status: 409,
        cuerpo: { detail: { codigo: "en_uso", mensaje: "El registro está en uso." } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);
    await u.click(await screen.findByRole("button", { name: "Borrar Corte" }));
    await u.click(screen.getByRole("button", { name: "Confirmar borrado de Corte" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/desactivalo/);
    expect(screen.getByText("Corte")).toBeInTheDocument();
  });

  it("borrar un servicio sin uso lo quita de la lista", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /servicios": [{ cuerpo: [CORTE] }, { cuerpo: [] }],
      "DELETE /servicios/s-1": [{ status: 204 }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);
    await u.click(await screen.findByRole("button", { name: "Borrar Corte" }));
    await u.click(screen.getByRole("button", { name: "Confirmar borrado de Corte" }));
    expect(await screen.findByText("Todavía no cargaste servicios.")).toBeInTheDocument();
  });

  it("sin el permiso edit_prices no aparecen los controles de precio", async () => {
    instalarBackend({
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_staff"] })],
      "GET /servicios": [{ cuerpo: [CORTE] }],
    });
    montarPantalla(<PaginaServicios />);
    expect(await screen.findByText("Corte")).toBeInTheDocument();
    expect(screen.getByText(/Solo podés ver los servicios/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nuevo servicio" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Editar/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Borrar/ })).not.toBeInTheDocument();
  });

  it("con edit_prices (sin ser owner) aparecen los controles", async () => {
    instalarBackend({
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["edit_prices"] })],
      "GET /servicios": [{ cuerpo: [CORTE] }],
    });
    montarPantalla(<PaginaServicios />);
    expect(await screen.findByRole("button", { name: "Nuevo servicio" })).toBeInTheDocument();
  });

  it("muestra el error de carga y permite reintentar", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /servicios": [
        { status: 500, cuerpo: { detail: { codigo: "x", mensaje: "Falló la carga" } } },
        { cuerpo: [CORTE] },
      ],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaServicios />);
    expect(await screen.findByText("Falló la carga")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Corte")).toBeInTheDocument();
  });
});
