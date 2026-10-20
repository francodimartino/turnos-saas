import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaDisponibilidad } from "./PaginaDisponibilidad";

afterEach(() => vi.unstubAllGlobals());

const ANA = { id: "p-1", name: "Ana", is_active: true, tenant_user_id: null };
const BETO = { id: "p-2", name: "Beto", is_active: true, tenant_user_id: null };
const LUNES = { id: "r-1", professional_id: "p-1", day_of_week: 1, start_time: "09:00:00", end_time: "13:00:00" };
const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const SCHEDULE = sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_schedule"] });
// 2026-12-25 00:00 en Buenos Aires = 03:00 UTC.
const FERIADO = {
  id: "b-1",
  professional_id: null,
  start_at: "2026-12-25T03:00:00+00:00",
  end_at: "2026-12-26T03:00:00+00:00",
  reason: "Navidad",
};

function base(extra: Parameters<typeof instalarBackend>[0] = {}) {
  return instalarBackend({
    "GET /auth/sesion": [OWNER],
    "GET /profesionales": [{ cuerpo: [ANA, BETO] }],
    "GET /reglas-disponibilidad": [{ cuerpo: [LUNES] }],
    "GET /bloqueos-disponibilidad": [{ cuerpo: [FERIADO] }],
    ...extra,
  });
}

describe("PaginaDisponibilidad", () => {
  it("muestra las reglas semanales del profesional y los bloqueos en hora de Buenos Aires", async () => {
    const backend = base();
    montarPantalla(<PaginaDisponibilidad />);
    expect(await screen.findByText(/Lunes: 09:00 a 13:00/)).toBeInTheDocument();
    expect(await screen.findByText(/Todo el local: 25\/12\/2026,? 00:00 a 26\/12\/2026,? 00:00 \(Navidad\)/)).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "GET /reglas-disponibilidad")).toBeDefined();
  });

  it("crea una regla semanal", async () => {
    const backend = base({
      "GET /reglas-disponibilidad": [
        { cuerpo: [LUNES] },
        { cuerpo: [LUNES, { ...LUNES, id: "r-2", day_of_week: 3, start_time: "14:00:00", end_time: "18:30:00" }] },
      ],
      "POST /reglas-disponibilidad": () => ({ status: 201, cuerpo: {} }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await u.click(await screen.findByRole("button", { name: "Agregar horario" }));
    await u.selectOptions(screen.getByLabelText("Día"), "3");
    await u.clear(screen.getByLabelText("Desde"));
    await u.type(screen.getByLabelText("Desde"), "14:00");
    await u.clear(screen.getByLabelText("Hasta"));
    await u.type(screen.getByLabelText("Hasta"), "18:30");
    await u.click(screen.getByRole("button", { name: "Guardar horario" }));
    expect(await screen.findByText(/Miércoles: 14:00 a 18:30/)).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "POST /reglas-disponibilidad")?.cuerpo).toEqual({
      professional_id: "p-1",
      day_of_week: 3,
      start_time: "14:00",
      end_time: "18:30",
    });
  });

  it("un 422 de la base sobre la ventana horaria se muestra junto al campo", async () => {
    base({
      "POST /reglas-disponibilidad": () => ({
        status: 422,
        cuerpo: { detail: { codigo: "valor_invalido", mensaje: "La hora de fin debe ser posterior a la hora de inicio." } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await u.click(await screen.findByRole("button", { name: "Agregar horario" }));
    await u.click(screen.getByRole("button", { name: "Guardar horario" }));
    const hasta = screen.getByLabelText("Hasta");
    await waitFor(() => expect(hasta).toHaveAccessibleDescription(/posterior a la hora de inicio/));
  });

  it("valida que el fin sea posterior al inicio antes de enviar", async () => {
    const backend = base();
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await u.click(await screen.findByRole("button", { name: "Agregar horario" }));
    await u.clear(screen.getByLabelText("Hasta"));
    await u.type(screen.getByLabelText("Hasta"), "08:00");
    await u.click(screen.getByRole("button", { name: "Guardar horario" }));
    expect(screen.getByLabelText("Hasta")).toHaveAccessibleDescription(/posterior/);
    expect(backend.cuantas("POST /reglas-disponibilidad")).toBe(0);
  });

  it("edita y borra una regla", async () => {
    const backend = base({
      "PATCH /reglas-disponibilidad/r-1": () => ({ cuerpo: {} }),
      "DELETE /reglas-disponibilidad/r-1": [{ status: 204 }],
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await u.click(await screen.findByRole("button", { name: /Editar horario del Lunes/ }));
    await u.clear(screen.getByLabelText("Hasta"));
    await u.type(screen.getByLabelText("Hasta"), "14:00");
    await u.click(screen.getByRole("button", { name: "Guardar horario" }));
    await waitFor(() => expect(backend.cuantas("PATCH /reglas-disponibilidad/r-1")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "PATCH /reglas-disponibilidad/r-1")?.cuerpo).toEqual({
      day_of_week: 1,
      start_time: "09:00",
      end_time: "14:00",
    });
    await u.click(await screen.findByRole("button", { name: /Borrar horario del Lunes/ }));
    await u.click(screen.getByRole("button", { name: /Confirmar borrado de horario/ }));
    await waitFor(() => expect(backend.cuantas("DELETE /reglas-disponibilidad/r-1")).toBe(1));
  });

  it("cambiar de profesional pide sus reglas", async () => {
    const backend = base({ "GET /reglas-disponibilidad": [{ cuerpo: [LUNES] }, { cuerpo: [] }] });
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await screen.findByText(/Lunes: 09:00/);
    await u.selectOptions(screen.getByLabelText("Profesional"), "p-2");
    expect(await screen.findByText(/no tiene horarios cargados/)).toBeInTheDocument();
    const pedidos = backend.llamadas.filter((l) => l.clave === "GET /reglas-disponibilidad");
    expect(pedidos).toHaveLength(2);
  });

  it("crea un bloqueo de todo el local con zona horaria de Buenos Aires", async () => {
    const backend = base({ "POST /bloqueos-disponibilidad": () => ({ status: 201, cuerpo: {} }) });
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await u.click(await screen.findByRole("button", { name: "Agregar bloqueo" }));
    await u.type(screen.getByLabelText("Desde (hora de Buenos Aires)"), "2027-01-01T00:00");
    await u.type(screen.getByLabelText("Hasta (hora de Buenos Aires)"), "2027-01-02T00:00");
    await u.type(screen.getByLabelText("Motivo (opcional)"), "Año nuevo");
    await u.click(screen.getByRole("button", { name: "Guardar bloqueo" }));
    await waitFor(() => expect(backend.cuantas("POST /bloqueos-disponibilidad")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "POST /bloqueos-disponibilidad")?.cuerpo).toEqual({
      professional_id: null,
      start_at: "2027-01-01T00:00:00-03:00",
      end_at: "2027-01-02T00:00:00-03:00",
      reason: "Año nuevo",
    });
  });

  it("un bloqueo puede ser de un profesional y un 422 del backend aparece junto al campo", async () => {
    const backend = base({
      "POST /bloqueos-disponibilidad": () => ({
        status: 422,
        cuerpo: { detail: { codigo: "valor_invalido", mensaje: "El bloqueo debe durar más de cero: el inicio debe ser anterior al fin." } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await u.click(await screen.findByRole("button", { name: "Agregar bloqueo" }));
    await u.selectOptions(screen.getByLabelText("Aplica a"), "p-2");
    await u.type(screen.getByLabelText("Desde (hora de Buenos Aires)"), "2027-01-01T10:00");
    await u.type(screen.getByLabelText("Hasta (hora de Buenos Aires)"), "2027-01-01T11:00");
    await u.click(screen.getByRole("button", { name: "Guardar bloqueo" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Hasta (hora de Buenos Aires)")).toHaveAccessibleDescription(/más de cero/),
    );
    expect(backend.llamadas.find((l) => l.clave === "POST /bloqueos-disponibilidad")?.cuerpo).toMatchObject({
      professional_id: "p-2",
    });
  });

  it("editar un bloqueo muestra la hora local y envía zona -03:00", async () => {
    const backend = base({ "PATCH /bloqueos-disponibilidad/b-1": () => ({ cuerpo: {} }) });
    const u = userEvent.setup();
    montarPantalla(<PaginaDisponibilidad />);
    await u.click(await screen.findByRole("button", { name: /Editar bloqueo/ }));
    expect(screen.getByLabelText("Desde (hora de Buenos Aires)")).toHaveValue("2026-12-25T00:00");
    await u.click(screen.getByRole("button", { name: "Guardar bloqueo" }));
    await waitFor(() => expect(backend.cuantas("PATCH /bloqueos-disponibilidad/b-1")).toBe(1));
    expect(backend.llamadas.find((l) => l.clave === "PATCH /bloqueos-disponibilidad/b-1")?.cuerpo).toEqual({
      start_at: "2026-12-25T00:00:00-03:00",
      end_at: "2026-12-26T00:00:00-03:00",
      reason: "Navidad",
    });
  });

  it("sin manage_schedule se ve la disponibilidad pero no se puede modificar", async () => {
    base({ "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_staff"] })] });
    montarPantalla(<PaginaDisponibilidad />);
    expect(await screen.findByText(/Lunes: 09:00 a 13:00/)).toBeInTheDocument();
    expect(await screen.findByText(/Navidad/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Agregar horario" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Agregar bloqueo" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Editar|Borrar/ })).not.toBeInTheDocument();
  });

  it("con manage_schedule (sin ser owner) aparecen las acciones", async () => {
    base({ "GET /auth/sesion": [SCHEDULE] });
    montarPantalla(<PaginaDisponibilidad />);
    expect(await screen.findByRole("button", { name: "Agregar horario" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Agregar bloqueo" })).toBeInTheDocument();
  });

  it("sin profesionales indica cargar uno primero", async () => {
    base({ "GET /profesionales": [{ cuerpo: [] }] });
    montarPantalla(<PaginaDisponibilidad />);
    expect(await screen.findByText(/Primero cargá un profesional/)).toBeInTheDocument();
  });
});
