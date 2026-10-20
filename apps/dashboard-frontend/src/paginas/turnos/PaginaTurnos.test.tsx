import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaTurnos } from "./PaginaTurnos";

afterEach(() => vi.unstubAllGlobals());

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const SOLO_VER = sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings"] });

const ANA = { id: "p-1", name: "Ana", is_active: true, tenant_user_id: null };
const BETO = { id: "p-2", name: "Beto", is_active: true, tenant_user_id: null };
const CORTE = { id: "s-1", name: "Corte", duration_minutes: 30, price: "1500.00", deposit_amount: null, is_active: true };
const COLOR = { id: "s-2", name: "Color", duration_minutes: 60, price: "5000.00", deposit_amount: null, is_active: true };
const LUCIA = { id: "c-1", name: "Lucía Pérez", phone: "+5491155500101", sin_cliente_identificable: false };
const ANONIMO = { id: "c-2", name: null, phone: null, sin_cliente_identificable: true };

// 2026-10-08 10:00 en Buenos Aires = 13:00 UTC.
const base = {
  end_customer_id: "c-1",
  service_id: "s-1",
  professional_id: "p-1",
  pending_expires_at: null,
  customer_name: "Lucía Pérez",
  service_name: "Corte",
  professional_name: "Ana",
  sin_cliente_identificable: false,
};
const CONFIRMADO = {
  ...base,
  id: "t-1",
  start_at: "2026-10-08T13:00:00+00:00",
  end_at: "2026-10-08T13:30:00+00:00",
  status: "confirmed",
};
const PENDIENTE = {
  ...base,
  id: "t-2",
  professional_id: "p-2",
  professional_name: "Beto",
  start_at: "2026-10-08T14:00:00+00:00",
  end_at: "2026-10-08T14:30:00+00:00",
  status: "pending",
  pending_expires_at: "2026-10-07T20:00:00+00:00",
};
const ANONIMIZADO = {
  ...base,
  id: "t-3",
  end_customer_id: "c-2",
  customer_name: null,
  sin_cliente_identificable: true,
  start_at: "2026-10-08T16:00:00+00:00",
  end_at: "2026-10-08T16:30:00+00:00",
  status: "confirmed",
};

type Rutas = Parameters<typeof instalarBackend>[0];
type Turno = Record<string, unknown>;

/** `GET /turnos` simulado: la lista de pendientes pide `estado=pending`; la agenda, el resto. */
function turnosDe(estado: { agenda: Turno[]; pendientes: Turno[] }) {
  return (_cuerpo: unknown, url: string) => ({
    cuerpo: { turnos: url.includes("estado=pending") ? estado.pendientes : estado.agenda, limite: 200, offset: 0 },
  });
}

function backend(extra: Rutas = {}, sesion = OWNER) {
  return instalarBackend({
    "GET /auth/sesion": [sesion],
    "GET /profesionales": [{ cuerpo: [ANA, BETO] }],
    "GET /servicios": [{ cuerpo: [CORTE, COLOR] }],
    "GET /clientes": [{ cuerpo: { clientes: [LUCIA, ANONIMO] } }],
    "GET /profesionales/p-1/servicios": [
      { cuerpo: [{ professional_id: "p-1", service_id: "s-1", confirmation_mode: "auto" }] },
    ],
    "GET /profesionales/p-2/servicios": [
      {
        cuerpo: [
          { professional_id: "p-2", service_id: "s-1", confirmation_mode: "approval" },
          { professional_id: "p-2", service_id: "s-2", confirmation_mode: "approval" },
        ],
      },
    ],
    "GET /turnos": turnosDe({ agenda: [CONFIRMADO, PENDIENTE, ANONIMIZADO], pendientes: [PENDIENTE] }),
    "GET /packs": [{ cuerpo: { packs: [] } }],
    ...extra,
  });
}

const agenda = () => screen.findByRole("region", { name: "Agenda" });
const pendientes = () => screen.findByRole("region", { name: "Pendientes de aprobación" });

async function filaConfirmada() {
  const a = await agenda();
  return (await within(a).findByText("10:00 a 10:30")).closest("tr") as HTMLElement;
}

describe("PaginaTurnos", () => {
  it("muestra la agenda por día y profesional, distingue los pendientes y los clientes anonimizados", async () => {
    backend();
    montarPantalla(<PaginaTurnos />);
    const a = await agenda();
    expect(await within(a).findByRole("heading", { name: "Ana" })).toBeInTheDocument();
    expect(within(a).getByRole("heading", { name: "Beto" })).toBeInTheDocument();
    expect(within(a).getByRole("heading", { name: /jueves, 8 de octubre/i })).toBeInTheDocument();
    // Hora de Buenos Aires, no UTC.
    expect(within(a).getByText("10:00 a 10:30")).toBeInTheDocument();
    expect(within(a).getAllByText("sin cliente identificable")).toHaveLength(1);
    expect(within(a).getByText("Pendiente de aprobación", { selector: "span" }).closest("tr")).toHaveClass("turno-pendiente");
    expect(within(a).getAllByText("Confirmado", { selector: "span" })[0]?.closest("tr")).not.toHaveClass("turno-pendiente");
  });

  it("aprobar una solicitud pendiente la saca de la lista", async () => {
    let aprobado = false;
    const b = backend({
      "GET /turnos": (_c, url) =>
        aprobado ? { cuerpo: { turnos: [] } } : turnosDe({ agenda: [PENDIENTE], pendientes: [PENDIENTE] })(null, url),
      "POST /turnos/t-2/aprobar": () => {
        aprobado = true;
        return { cuerpo: { ...PENDIENTE, status: "confirmed", calendario: { estado: "creado" }, aviso: "programado" } };
      },
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const p = await pendientes();
    await u.click(await within(p).findByRole("button", { name: "Aprobar" }));
    expect(await within(p).findByText("No hay solicitudes pendientes.")).toBeInTheDocument();
    expect(b.cuantas("POST /turnos/t-2/aprobar")).toBe(1);
  });

  it("rechazar pide confirmación antes de llamar al servidor", async () => {
    const b = backend({
      "POST /turnos/t-2/rechazar": () => ({ cuerpo: { ...PENDIENTE, status: "cancelled" } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const p = await pendientes();
    await u.click(await within(p).findByRole("button", { name: "Rechazar" }));
    expect(b.cuantas("POST /turnos/t-2/rechazar")).toBe(0);
    await u.click(within(p).getByRole("button", { name: "Confirmar rechazo" }));
    expect(b.cuantas("POST /turnos/t-2/rechazar")).toBe(1);
  });

  it("mover a un horario ocupado muestra el choque y deja el formulario abierto", async () => {
    const b = backend({
      "POST /turnos/t-1/mover": () => ({
        status: 409,
        cuerpo: { detail: { codigo: "horario_ocupado", mensaje: "El horario se superpone con otro turno." } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const fila = await filaConfirmada();
    await u.click(within(fila).getByRole("button", { name: "Mover" }));
    const campo = within(fila).getByLabelText("Nuevo horario");
    expect(campo).toHaveValue("2026-10-08T10:00");
    fireEvent.change(campo, { target: { value: "2026-10-09T15:00" } });
    await u.click(within(fila).getByRole("button", { name: "Confirmar movimiento" }));
    expect(await within(fila).findByRole("alert")).toHaveTextContent(/horario está ocupado/i);
    expect(b.llamadas.find((l) => l.clave === "POST /turnos/t-1/mover")?.cuerpo).toEqual({
      start_at: "2026-10-09T15:00:00-03:00",
    });
    expect(within(fila).getByLabelText("Nuevo horario")).toBeInTheDocument();
  });

  it("mover a un horario libre recarga la agenda", async () => {
    const b = backend({
      "POST /turnos/t-1/mover": () => ({ cuerpo: { ...CONFIRMADO, calendario: { estado: "actualizado" } } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const fila = await filaConfirmada();
    await u.click(within(fila).getByRole("button", { name: "Mover" }));
    fireEvent.change(within(fila).getByLabelText("Nuevo horario"), { target: { value: "2026-10-09T11:00" } });
    const antes = b.cuantas("GET /turnos");
    await u.click(within(fila).getByRole("button", { name: "Confirmar movimiento" }));
    await waitFor(() => expect(b.cuantas("GET /turnos")).toBeGreaterThan(antes));
    expect(b.cuantas("POST /turnos/t-1/mover")).toBe(1);
  });

  it("cancelar pide confirmación y solo entonces cancela", async () => {
    const b = backend({
      "POST /turnos/t-1/cancelar": () => ({ cuerpo: { ...CONFIRMADO, status: "cancelled" } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const fila = await filaConfirmada();
    await u.click(within(fila).getByRole("button", { name: "Cancelar turno" }));
    expect(within(fila).getByText(/¿Cancelar este turno\?/)).toBeInTheDocument();
    expect(b.cuantas("POST /turnos/t-1/cancelar")).toBe(0);
    await u.click(within(fila).getByRole("button", { name: "Volver" }));
    expect(b.cuantas("POST /turnos/t-1/cancelar")).toBe(0);
    await u.click(within(fila).getByRole("button", { name: "Cancelar turno" }));
    await u.click(within(fila).getByRole("button", { name: "Confirmar cancelación" }));
    expect(b.cuantas("POST /turnos/t-1/cancelar")).toBe(1);
  });

  it("marcar completado llama al servidor", async () => {
    const b = backend({ "POST /turnos/t-1/completar": () => ({ cuerpo: { ...CONFIRMADO, status: "completed" } }) });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const fila = await filaConfirmada();
    await u.click(within(fila).getByRole("button", { name: "Marcar completado" }));
    expect(b.cuantas("POST /turnos/t-1/completar")).toBe(1);
  });

  it("un turno que otro usuario ya cambió refresca la lista y explica el motivo", async () => {
    const b = backend({
      "POST /turnos/t-2/aprobar": () => ({
        status: 409,
        cuerpo: { detail: { codigo: "estado_invalido", mensaje: "No se puede aprobar un turno en estado 'cancelled'." } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const p = await pendientes();
    const antes = b.cuantas("GET /turnos");
    await u.click(await within(p).findByRole("button", { name: "Aprobar" }));
    expect(await within(p).findByRole("alert")).toHaveTextContent("No se puede aprobar un turno en estado 'cancelled'.");
    await waitFor(() => expect(b.cuantas("GET /turnos")).toBeGreaterThan(antes));
  });

  it("avisa si el cambio quedó guardado pero Calendar falló", async () => {
    backend({
      "POST /turnos/t-1/completar": () => ({ cuerpo: { ...CONFIRMADO, calendario: { estado: "error" } } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const fila = await filaConfirmada();
    await u.click(within(fila).getByRole("button", { name: "Marcar completado" }));
    expect(await screen.findByText(/no se pudo actualizar Google Calendar/)).toBeInTheDocument();
  });

  it("sin approve_bookings se ve todo pero no hay acciones, ni alta de turnos, ni alta de packs", async () => {
    backend({}, SOLO_VER);
    montarPantalla(<PaginaTurnos />);
    await filaConfirmada();
    expect(screen.queryByRole("button", { name: /Aprobar|Rechazar|Mover|Cancelar turno|Marcar completado/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Nuevo turno" })).toBeNull();
    expect(screen.queryByRole("form", { name: "Asignar pack" })).toBeNull();
    expect(screen.getByText(/Solo podés ver los turnos/)).toBeInTheDocument();
  });

  it("filtra por profesional y por estado pidiéndolo al servidor", async () => {
    const b = backend();
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const a = await agenda();
    await within(a).findByText("10:00 a 10:30");
    await u.selectOptions(within(a).getByLabelText("Profesional"), "p-2");
    await u.selectOptions(within(a).getByLabelText("Estado"), "confirmed");
    await waitFor(() => {
      const ultima = b.llamadas.filter((l) => l.clave === "GET /turnos" && !l.url.includes("estado=pending")).at(-1);
      expect(ultima?.url).toContain("professional_id=p-2");
      expect(ultima?.url).toContain("estado=confirmed");
    });
  });

  it("la vista semanal pide siete días de Buenos Aires", async () => {
    const b = backend();
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const a = await agenda();
    await within(a).findByText("10:00 a 10:30");
    fireEvent.change(within(a).getByLabelText("Fecha"), { target: { value: "2026-10-07" } });
    await u.selectOptions(within(a).getByLabelText("Vista"), "semana");
    await waitFor(() => {
      const ultima = b.llamadas.filter((l) => l.clave === "GET /turnos" && !l.url.includes("estado=pending")).at(-1);
      const q = new URL(ultima?.url ?? "", "http://x").searchParams;
      expect(q.get("desde")).toBe("2026-10-05T00:00:00-03:00");
      expect(q.get("hasta")).toBe("2026-10-12T00:00:00-03:00");
    });
  });

  it("crea un turno: solo ofrece los profesionales que hacen el servicio y manda el horario con zona", async () => {
    const b = backend({
      "POST /turnos": () => ({ status: 201, cuerpo: { ...CONFIRMADO, calendario: { estado: "creado" } } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    await u.click(await screen.findByRole("button", { name: "Nuevo turno" }));
    const f = screen.getByRole("region", { name: "Crear turno" });
    // El cliente anonimizado no se puede elegir.
    expect(within(f).queryByRole("option", { name: /sin cliente identificable/ })).toBeNull();
    await u.selectOptions(within(f).getByLabelText("Cliente"), "c-1");
    await u.selectOptions(within(f).getByLabelText("Servicio"), "s-2"); // Color: solo Beto
    expect(within(f).queryByRole("option", { name: "Ana" })).toBeNull();
    expect(within(f).getByRole("option", { name: "Beto" })).toBeInTheDocument();
    await u.selectOptions(within(f).getByLabelText("Servicio"), "s-1");
    await u.selectOptions(within(f).getByLabelText("Profesional"), "p-1");
    fireEvent.change(within(f).getByLabelText("Día y hora (Buenos Aires)"), { target: { value: "2026-10-09T16:30" } });
    await u.click(within(f).getByRole("button", { name: "Crear turno" }));
    expect(await screen.findByText("Turno creado.")).toBeInTheDocument();
    expect(b.llamadas.find((l) => l.clave === "POST /turnos")?.cuerpo).toEqual({
      end_customer_id: "c-1",
      service_id: "s-1",
      professional_id: "p-1",
      start_at: "2026-10-09T16:30:00-03:00",
    });
  });

  it("crear un turno en un horario ocupado muestra el mensaje junto al campo", async () => {
    backend({
      "POST /turnos": () => ({
        status: 409,
        cuerpo: { detail: { codigo: "horario_ocupado", mensaje: "El horario se superpone." } },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    await u.click(await screen.findByRole("button", { name: "Nuevo turno" }));
    const f = screen.getByRole("region", { name: "Crear turno" });
    await u.selectOptions(within(f).getByLabelText("Cliente"), "c-1");
    await u.selectOptions(within(f).getByLabelText("Servicio"), "s-1");
    await u.selectOptions(within(f).getByLabelText("Profesional"), "p-1");
    fireEvent.change(within(f).getByLabelText("Día y hora (Buenos Aires)"), { target: { value: "2026-10-08T10:00" } });
    await u.click(within(f).getByRole("button", { name: "Crear turno" }));
    expect(await within(f).findByText(/Ese horario está ocupado/)).toBeInTheDocument();
    expect(within(f).getByLabelText("Día y hora (Buenos Aires)")).toHaveAttribute("aria-invalid", "true");
  });

  it("no envía el alta si faltan datos y dice cuáles", async () => {
    const b = backend();
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    await u.click(await screen.findByRole("button", { name: "Nuevo turno" }));
    await u.click(screen.getByRole("button", { name: "Crear turno" }));
    const avisos = (await screen.findAllByRole("alert")).map((n) => n.textContent);
    expect(avisos).toContain("Elegí un cliente");
    expect(avisos).toContain("Indicá el día y la hora");
    expect(b.cuantas("POST /turnos")).toBe(0);
  });

  it("lista los packs con su consumo y asigna uno manual", async () => {
    const pack = {
      id: "k-1",
      end_customer_id: "c-1",
      service_id: "s-1",
      total_sessions: 5,
      used_sessions: 2,
      origin: "manual_grant",
      expires_at: null,
    };
    const b = backend({
      "GET /packs": [{ cuerpo: { packs: [pack] } }],
      "POST /packs": () => ({ status: 201, cuerpo: { id: "k-2" } }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const k = await screen.findByRole("region", { name: "Packs" });
    expect(await within(k).findByText("2 de 5 usadas")).toBeInTheDocument();
    expect(within(k).getByText("Sin vencimiento")).toBeInTheDocument();
    await u.selectOptions(within(k).getByLabelText("Cliente del pack"), "c-1");
    await u.selectOptions(within(k).getByLabelText("Servicio del pack"), "s-1");
    await u.type(within(k).getByLabelText("Cantidad de sesiones"), "10");
    await u.click(within(k).getByRole("button", { name: "Asignar pack" }));
    expect(await within(k).findByText("Pack asignado.")).toBeInTheDocument();
    expect(b.llamadas.find((l) => l.clave === "POST /packs")?.cuerpo).toEqual({
      end_customer_id: "c-1",
      service_id: "s-1",
      total_sessions: 10,
    });
  });

  it("valida la cantidad de sesiones del pack", async () => {
    const b = backend();
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const k = await screen.findByRole("region", { name: "Packs" });
    await u.click(await within(k).findByRole("button", { name: "Asignar pack" }));
    expect(within(k).getByText(/Indicá cuántas sesiones/)).toBeInTheDocument();
    expect(b.cuantas("POST /packs")).toBe(0);
  });
});
