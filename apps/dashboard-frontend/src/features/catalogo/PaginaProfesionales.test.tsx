import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaProfesionales } from "./PaginaProfesionales";

afterEach(() => vi.unstubAllGlobals());

const ANA = { id: "p-1", name: "Ana", is_active: true, tenant_user_id: null };
const CORTE = { id: "s-1", name: "Corte", duration_minutes: 30, price: "15000.00", deposit_amount: null, is_active: true };
const TINTURA = { ...CORTE, id: "s-2", name: "Tintura" };
const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const USUARIO = "0b0f2c3e-1a2b-4c3d-8e9f-1234567890ab";
const ASIGNACION = (service_id: string, confirmation_mode: string) => ({
  professional_id: "p-1",
  service_id,
  confirmation_mode,
});

describe("PaginaProfesionales", () => {
  it("crea un profesional con usuario vinculado opcional", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [
        { cuerpo: [ANA] },
        { cuerpo: [ANA, { ...ANA, id: "p-2", name: "Beto", tenant_user_id: USUARIO }] },
      ],
      "POST /profesionales": () => ({ status: 201, cuerpo: {} }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Nuevo profesional" }));
    await u.type(screen.getByLabelText("Nombre"), "Beto");
    await u.type(screen.getByLabelText("Usuario vinculado (opcional)"), USUARIO);
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Beto")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "POST /profesionales")?.cuerpo).toEqual({
      name: "Beto",
      tenant_user_id: USUARIO,
      is_active: true,
    });
  });

  it("el usuario vinculado se puede omitir y se valida su formato", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [] }],
      "POST /profesionales": () => ({ status: 201, cuerpo: {} }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Nuevo profesional" }));
    await u.type(screen.getByLabelText("Nombre"), "Carla");
    await u.type(screen.getByLabelText("Usuario vinculado (opcional)"), "no-es-un-uuid");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    expect(screen.getByLabelText("Usuario vinculado (opcional)")).toHaveAccessibleDescription(/formato válido/);
    await u.clear(screen.getByLabelText("Usuario vinculado (opcional)"));
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await vi.waitFor(() => expect(backend.cuantas("POST /profesionales")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /profesionales")?.cuerpo).toMatchObject({
      tenant_user_id: null,
    });
  });

  it("un error del backend sobre la cuenta vinculada se muestra junto a ese campo", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [] }],
      "POST /profesionales": () => ({
        status: 422,
        cuerpo: {
          detail: { codigo: "valor_invalido", mensaje: "Esa cuenta de staff ya está vinculada a otro profesional." },
        },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Nuevo profesional" }));
    await u.type(screen.getByLabelText("Nombre"), "Beto");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    const campo = await screen.findByLabelText("Usuario vinculado (opcional)");
    await vi.waitFor(() => expect(campo).toHaveAccessibleDescription(/ya está vinculada/));
  });

  it("asigna un servicio con modo approval y no ofrece deposit", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /servicios": [{ cuerpo: [CORTE, TINTURA] }],
      "GET /profesionales/p-1/servicios": [
        { cuerpo: [ASIGNACION("s-2", "auto")] },
        { cuerpo: [ASIGNACION("s-2", "auto"), ASIGNACION("s-1", "approval")] },
      ],
      "PUT /profesionales/p-1/servicios/s-1": () => ({ cuerpo: {} }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Servicios de Ana" }));
    const modoCorte = await screen.findByLabelText("Modo de Corte");
    expect(within(modoCorte).queryByRole("option", { name: /seña/i })).not.toBeInTheDocument();
    expect(within(modoCorte).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "ninguno",
      "auto",
      "approval",
    ]);
    expect(screen.getByLabelText("Modo de Tintura")).toHaveValue("auto");
    await u.selectOptions(modoCorte, "approval");
    await vi.waitFor(() => expect(screen.getByLabelText("Modo de Corte")).toHaveValue("approval"));
    expect(backend.llamadas.find((l) => l.clave === "PUT /profesionales/p-1/servicios/s-1")?.cuerpo).toEqual({
      confirmation_mode: "approval",
    });
  });

  it("quitar un servicio lo desasigna", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /servicios": [{ cuerpo: [CORTE] }],
      "GET /profesionales/p-1/servicios": [{ cuerpo: [ASIGNACION("s-1", "auto")] }, { cuerpo: [] }],
      "DELETE /profesionales/p-1/servicios/s-1": [{ status: 204 }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Servicios de Ana" }));
    await u.selectOptions(await screen.findByLabelText("Modo de Corte"), "ninguno");
    await vi.waitFor(() => expect(screen.getByLabelText("Modo de Corte")).toHaveValue("ninguno"));
    expect(backend.cuantas("DELETE /profesionales/p-1/servicios/s-1")).toBe(1);
  });

  it("borrar con 409 en_uso explica que hay que desactivarlo", async () => {
    instalarBackend({
      "GET /auth/sesion": [OWNER],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "DELETE /profesionales/p-1": () => ({ status: 409, cuerpo: { detail: { codigo: "en_uso", mensaje: "en uso" } } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Borrar Ana" }));
    await u.click(screen.getByRole("button", { name: "Confirmar borrado de Ana" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/desactivalo/);
  });

  it("sin manage_staff no hay altas, ediciones ni borrados y los modos no se pueden cambiar", async () => {
    instalarBackend({
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["edit_prices"] })],
      "GET /profesionales": [{ cuerpo: [ANA] }],
      "GET /servicios": [{ cuerpo: [CORTE] }],
      "GET /profesionales/p-1/servicios": [{ cuerpo: [] }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    await u.click(await screen.findByRole("button", { name: "Servicios de Ana" }));
    expect(await screen.findByLabelText("Modo de Corte")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Nuevo profesional" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Editar Ana" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Borrar Ana" })).not.toBeInTheDocument();
  });

  it("para un staff no owner el formulario no ofrece la cuenta vinculada ni la envía, y la columna sigue visible", async () => {
    const VINCULADA = { ...ANA, id: "p-2", name: "Beto", tenant_user_id: USUARIO };
    const backend = instalarBackend({
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_staff"] })],
      "GET /profesionales": [{ cuerpo: [ANA, VINCULADA] }, { cuerpo: [ANA, VINCULADA] }, { cuerpo: [ANA, { ...VINCULADA, name: "Beto B" }] }],
      "POST /profesionales": () => ({ status: 201, cuerpo: {} }),
      "PATCH /profesionales/p-2": () => ({ status: 200, cuerpo: {} }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaProfesionales />);
    expect(await screen.findByText("Vinculado")).toBeInTheDocument();
    expect(screen.getByText("Sin usuario")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Nuevo profesional" }));
    expect(screen.queryByLabelText("Usuario vinculado (opcional)")).not.toBeInTheDocument();
    await u.type(screen.getByLabelText("Nombre"), "Carla");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await vi.waitFor(() => expect(backend.cuantas("POST /profesionales")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /profesionales")?.cuerpo).toEqual({
      name: "Carla",
      is_active: true,
    });
    await vi.waitFor(() => expect(screen.queryByLabelText("Nombre")).not.toBeInTheDocument());
    await u.click(await screen.findByRole("button", { name: "Editar Beto" }));
    expect(screen.queryByLabelText("Usuario vinculado (opcional)")).not.toBeInTheDocument();
    await u.type(screen.getByLabelText("Nombre"), " B");
    await u.click(screen.getByRole("button", { name: "Guardar" }));
    await vi.waitFor(() => expect(backend.cuantas("PATCH /profesionales/p-2")).toBe(1));
    const cuerpo = backend.llamadas.find((l) => l.clave === "PATCH /profesionales/p-2")?.cuerpo as Record<string, unknown>;
    expect(cuerpo).toEqual({ name: "Beto B", is_active: true });
    expect(cuerpo).not.toHaveProperty("tenant_user_id");
    expect(await screen.findByText("Beto B")).toBeInTheDocument();
  });
});
