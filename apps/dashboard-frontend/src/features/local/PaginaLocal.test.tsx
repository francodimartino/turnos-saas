import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaLocal } from "./PaginaLocal";

afterEach(() => vi.unstubAllGlobals());

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const SETTINGS = {
  local_direccion: "Calle 1",
  local_horarios: "Lun a Vie 9 a 18",
  booking_approval_timeout_hours: 24,
  end_customer_extra_fields: ["obra_social"],
  otra_clave: { no: "se toca" },
};

function base(extra: Parameters<typeof instalarBackend>[0] = {}) {
  return instalarBackend({
    "GET /auth/sesion": [OWNER],
    "GET /local/settings": [{ cuerpo: { settings: SETTINGS } }],
    ...extra,
  });
}

describe("PaginaLocal", () => {
  it("muestra los datos actuales del local", async () => {
    base();
    montarPantalla(<PaginaLocal />);
    expect(await screen.findByLabelText("Dirección")).toHaveValue("Calle 1");
    expect(screen.getByLabelText("Horarios de atención")).toHaveValue("Lun a Vie 9 a 18");
    expect(screen.getByLabelText("Contacto")).toHaveValue("");
    expect(screen.getByLabelText("Horas para aprobar un turno")).toHaveValue("24");
    expect(screen.getByLabelText("Identificador del dato 1")).toHaveValue("obra_social");
  });

  it("guarda solo lo que cambió, sin pisar otras claves", async () => {
    const backend = base({ "PATCH /local/settings": () => ({ cuerpo: { settings: SETTINGS } }) });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    const direccion = await screen.findByLabelText("Dirección");
    await u.clear(direccion);
    await u.type(direccion, "Calle 2");
    await u.type(screen.getByLabelText("Contacto"), "11 5555 0000");
    const horas = screen.getByLabelText("Horas para aprobar un turno");
    await u.clear(horas);
    await u.type(horas, "48");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PATCH /local/settings")?.cuerpo).toEqual({
      settings: { local_direccion: "Calle 2", local_contacto: "11 5555 0000", booking_approval_timeout_hours: 48 },
    });
  });

  it("vaciar un texto borra la clave (null) y se pueden editar los datos adicionales de clientes", async () => {
    const backend = base({ "PATCH /local/settings": () => ({ cuerpo: { settings: SETTINGS } }) });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    await u.clear(await screen.findByLabelText("Horarios de atención"));
    await u.click(screen.getByRole("button", { name: "Agregar dato adicional" }));
    await u.type(screen.getByLabelText("Identificador del dato 2"), "alergias");
    await u.type(screen.getByLabelText("Nombre del dato 2"), "Alergias");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await vi.waitFor(() => expect(backend.cuantas("PATCH /local/settings")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "PATCH /local/settings")?.cuerpo).toEqual({
      settings: {
        local_horarios: null,
        end_customer_extra_fields: [
          { key: "obra_social", label: "obra_social" },
          { key: "alergias", label: "Alergias" },
        ],
      },
    });
  });

  it.each(["0", "169", "1000", "2.5", "abc"])("el timeout %s se rechaza en el cliente sin llamar al backend", async (valor) => {
    const backend = base({ "PATCH /local/settings": () => ({ cuerpo: { settings: SETTINGS } }) });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    const horas = await screen.findByLabelText("Horas para aprobar un turno");
    await u.clear(horas);
    await u.type(horas, valor);
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(horas).toHaveAccessibleDescription(/entre 1 y 168|número entero/);
    expect(backend.cuantas("PATCH /local/settings")).toBe(0);
  });

  it.each(["1", "168"])("el timeout %s (límite) se acepta y se envía", async (valor) => {
    const backend = base({ "PATCH /local/settings": () => ({ cuerpo: { settings: SETTINGS } }) });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    const horas = await screen.findByLabelText("Horas para aprobar un turno");
    await u.clear(horas);
    await u.type(horas, valor);
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PATCH /local/settings")?.cuerpo).toEqual({
      settings: { booking_approval_timeout_hours: Number(valor) },
    });
  });

  it("un timeout fuera de 1-168 rechazado por el backend (422) se muestra junto al campo", async () => {
    base({
      "PATCH /local/settings": () => ({
        status: 422,
        cuerpo: { detail: { codigo: "valor_invalido", mensaje: "booking_approval_timeout_hours debe estar entre 1 y 168" } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    const horas = await screen.findByLabelText("Horas para aprobar un turno");
    await u.clear(horas);
    await u.type(horas, "500");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await vi.waitFor(() => expect(horas).toHaveAccessibleDescription(/entre 1 y 168/));
  });

  it("un 422 con el formato estándar de FastAPI (lista de errores) se muestra junto al campo", async () => {
    base({
      "PATCH /local/settings": () => ({
        status: 422,
        cuerpo: {
          detail: [
            {
              loc: ["body", "settings", "booking_approval_timeout_hours"],
              msg: "Value error, booking_approval_timeout_hours debe estar entre 1 y 168",
              type: "value_error",
            },
          ],
        },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    const horas = await screen.findByLabelText("Horas para aprobar un turno");
    await u.clear(horas);
    await u.type(horas, "100");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await vi.waitFor(() => expect(horas).toHaveAccessibleDescription(/entre 1 y 168/));
  });

  it("un 422 de lista cuyo campo no está en la pantalla se muestra como mensaje general", async () => {
    base({
      "PATCH /local/settings": () => ({
        status: 422,
        cuerpo: { detail: [{ loc: ["body", "settings"], msg: "Value error, clave desconocida", type: "value_error" }] },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    const horas = await screen.findByLabelText("Horas para aprobar un turno");
    await u.clear(horas);
    await u.type(horas, "100");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("clave desconocida");
  });

  it("valida que las horas sean un entero y que los datos adicionales tengan identificador", async () => {
    const backend = base();
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    const horas = await screen.findByLabelText("Horas para aprobar un turno");
    await u.clear(horas);
    await u.type(horas, "dos");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(horas).toHaveAccessibleDescription(/número entero/);
    await u.clear(horas);
    await u.type(horas, "24");
    await u.click(screen.getByRole("button", { name: "Agregar dato adicional" }));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(screen.getByText(/necesita un identificador/)).toBeInTheDocument();
    expect(backend.cuantas("PATCH /local/settings")).toBe(0);
  });

  it("sin cambios no llama al backend", async () => {
    const backend = base();
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    await u.click(await screen.findByRole("button", { name: "Guardar cambios" }));
    expect(screen.getByText("No hay cambios para guardar")).toBeInTheDocument();
    expect(backend.cuantas("PATCH /local/settings")).toBe(0);
  });

  it("sin manage_schedule el formulario es de solo lectura y no tiene botón de guardar", async () => {
    base({ "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings"] })] });
    montarPantalla(<PaginaLocal />);
    expect(await screen.findByLabelText("Dirección")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Guardar cambios" })).not.toBeInTheDocument();
  });

  it("un 403 del backend se informa como falta de permiso", async () => {
    base({
      "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_schedule"] })],
      "PATCH /local/settings": () => ({ status: 403, cuerpo: { detail: { codigo: "permiso_denegado", mensaje: "no" } } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaLocal />);
    await u.type(await screen.findByLabelText("Contacto"), "x");
    await u.click(await screen.findByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText(/No tenés permiso/)).toBeInTheDocument();
  });
});
