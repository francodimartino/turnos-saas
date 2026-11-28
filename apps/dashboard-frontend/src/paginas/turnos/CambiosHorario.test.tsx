import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Turno } from "../../api/turnos";
import { instalarBackend, sesionDe, type RespuestaFalsa } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaTurnos } from "./PaginaTurnos";

afterEach(() => vi.unstubAllGlobals());

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
// No es dueño y tiene exactamente el permiso para resolver cambios.
const SOLO_APROBAR = sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings", "approve_bookings"] });
// Incluye al profesional vinculado: ve los turnos pero no tiene permiso para resolver cambios.
const SOLO_VER = sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings"] });

// Las fechas están lejos en el futuro para que el estado "vigente" no dependa del reloj; 13:00 UTC = 10:00 en Buenos Aires.
const VIGENTE: Turno = {
  id: "t-9",
  end_customer_id: "c-1",
  service_id: "s-1",
  professional_id: "p-1",
  start_at: "2099-10-08T13:00:00+00:00",
  end_at: "2099-10-08T13:30:00+00:00",
  status: "confirmed",
  deposit_status: "none",
  booking_pack_id: null,
  pending_expires_at: null,
  starts_at_propuesto: "2099-10-09T15:00:00+00:00",
  ends_at_propuesto: "2099-10-09T15:30:00+00:00",
  cambio_pendiente_expira_at: "2099-10-07T20:00:00+00:00",
  customer_name: "Lucía Pérez",
  service_name: "Corte",
  professional_name: "Ana",
  sin_cliente_identificable: false,
  created_at: "2099-10-01T12:00:00+00:00",
};
// El cambio venció y nadie lo revirtió todavía: el servidor lo sigue listando.
const VENCIDO: Turno = {
  ...VIGENTE,
  id: "t-10",
  customer_name: "Marta Gómez",
  cambio_pendiente_expira_at: "2020-01-01T00:00:00+00:00",
};

const RESUELTO: Turno = { ...VIGENTE, starts_at_propuesto: null, ends_at_propuesto: null, cambio_pendiente_expira_at: null };

type Rutas = Parameters<typeof instalarBackend>[0];

function resultado(aviso: string, turno: Turno = RESUELTO): RespuestaFalsa {
  return {
    cuerpo: { ...turno, google_calendar_event_id: null, customer_phone: null, calendario: { estado: "no_aplica" }, aviso },
  };
}

function error(status: number, codigo: string, mensaje: string): RespuestaFalsa {
  return { status, cuerpo: { detail: { codigo, mensaje } } };
}

const lista = (turnos: Turno[]): RespuestaFalsa => ({ cuerpo: { turnos, limite: 200, offset: 0 } });

/** La agenda y los pendientes vienen vacíos: lo que se prueba acá es la lista de cambios (`cambio_pendiente=true`). */
function backend(cambios: Turno[] | ((url: string) => RespuestaFalsa | Promise<RespuestaFalsa>), extra: Rutas = {}, sesion = OWNER) {
  return instalarBackend({
    "GET /auth/sesion": [sesion],
    "GET /profesionales": [{ cuerpo: [{ id: "p-1", name: "Ana", is_active: true, tenant_user_id: null }] }],
    "GET /servicios": [{ cuerpo: [] }],
    "GET /clientes": [{ cuerpo: { clientes: [] } }],
    "GET /profesionales/p-1/servicios": [{ cuerpo: [] }],
    "GET /turnos": (_c, url) => {
      if (!url.includes("cambio_pendiente=true")) return lista([]);
      return typeof cambios === "function" ? cambios(url) : lista(cambios);
    },
    "GET /packs": [{ cuerpo: { packs: [] } }],
    ...extra,
  });
}

const seccion = () => screen.findByRole("region", { name: "Cambios de horario pendientes" });
const listasDeCambios = (b: ReturnType<typeof backend>) =>
  b.llamadas.filter((l) => l.clave === "GET /turnos" && l.url.includes("cambio_pendiente=true")).length;

async function filaDe(s: HTMLElement, cliente: string) {
  return (await within(s).findByText(cliente)).closest("tr") as HTMLElement;
}

describe("cambios de horario pendientes", () => {
  it("muestra horario actual, propuesto y vencimiento en hora de Buenos Aires", async () => {
    const b = backend([VIGENTE]);
    montarPantalla(<PaginaTurnos />);
    const fila = await filaDe(await seccion(), "Lucía Pérez");
    expect(within(fila).getByText("Cambio de horario pendiente")).toBeInTheDocument();
    expect(within(fila).getByText("Horario actual").nextElementSibling).toHaveTextContent(/08\/10\/2099.*10:00 a 10:30/);
    expect(within(fila).getByText("Horario propuesto").nextElementSibling).toHaveTextContent(/09\/10\/2099.*12:00 a 12:30/);
    expect(within(fila).getByText("Vence").nextElementSibling).toHaveTextContent(/07\/10\/2099.*17:00/);
    expect(within(fila).getByRole("button", { name: "Aprobar cambio de horario" })).toBeEnabled();
    expect(within(fila).getByRole("button", { name: "Rechazar cambio de horario" })).toBeEnabled();
    expect(b.llamadas.find((l) => l.url.includes("cambio_pendiente=true"))?.url).not.toMatch(/desde|hasta/);
  });

  it("un cambio vencido se distingue y solo admite rechazar", async () => {
    backend([VENCIDO]);
    montarPantalla(<PaginaTurnos />);
    const fila = await filaDe(await seccion(), "Marta Gómez");
    expect(within(fila).getByText("Cambio de horario vencido")).toBeInTheDocument();
    expect(within(fila).getByText("Venció")).toBeInTheDocument();
    expect(within(fila).queryByRole("button", { name: "Aprobar cambio de horario" })).not.toBeInTheDocument();
    expect(within(fila).getByRole("button", { name: "Rechazar cambio de horario" })).toBeEnabled();
  });

  it("aprobar pide confirmación, actualiza la lista y avisa que el aviso al cliente quedó en cola", async () => {
    let resuelto = false;
    const b = backend(() => lista(resuelto ? [] : [VIGENTE]), {
      "POST /turnos/t-9/cambio/aprobar": () => {
        resuelto = true;
        return resultado("pendiente_de_envio");
      },
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const s = await seccion();
    await u.click(await within(s).findByRole("button", { name: "Aprobar cambio de horario" }));
    expect(within(s).getByText(/¿Aprobar el cambio\?.*09\/10\/2099.*12:00 a 12:30/)).toBeInTheDocument();
    expect(b.cuantas("POST /turnos/t-9/cambio/aprobar")).toBe(0);
    await u.click(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" }));
    expect(await within(s).findByText("No hay cambios de horario pendientes.")).toBeInTheDocument();
    expect(b.cuantas("POST /turnos/t-9/cambio/aprobar")).toBe(1);
    expect(b.llamadas.find((l) => l.clave === "POST /turnos/t-9/cambio/aprobar")?.cuerpo).toBeUndefined();
    expect(await screen.findByRole("status")).toHaveTextContent(/más de un canal.*cola/);
  });

  it("rechazar pide confirmación, no usa la ruta de rechazar solicitudes y actualiza la lista", async () => {
    let resuelto = false;
    const b = backend(() => lista(resuelto ? [] : [VIGENTE]), {
      "POST /turnos/t-9/cambio/rechazar": () => {
        resuelto = true;
        return resultado("enviado");
      },
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const s = await seccion();
    await u.click(await within(s).findByRole("button", { name: "Rechazar cambio de horario" }));
    expect(within(s).getByText(/¿Rechazar el cambio\?.*conserva su horario actual/)).toBeInTheDocument();
    expect(b.cuantas("POST /turnos/t-9/cambio/rechazar")).toBe(0);
    await u.click(within(s).getByRole("button", { name: "Volver" }));
    expect(b.cuantas("POST /turnos/t-9/cambio/rechazar")).toBe(0);
    await u.click(within(s).getByRole("button", { name: "Rechazar cambio de horario" }));
    await u.click(within(s).getByRole("button", { name: "Confirmar rechazo del cambio" }));
    expect(await within(s).findByText("No hay cambios de horario pendientes.")).toBeInTheDocument();
    expect(b.cuantas("POST /turnos/t-9/cambio/rechazar")).toBe(1);
    expect(b.cuantas("POST /turnos/t-9/rechazar")).toBe(0);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("un cambio vencido se puede rechazar", async () => {
    let resuelto = false;
    const b = backend(() => lista(resuelto ? [] : [VENCIDO]), {
      "POST /turnos/t-10/cambio/rechazar": () => {
        resuelto = true;
        return resultado("enviado", { ...RESUELTO, id: "t-10" });
      },
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const s = await seccion();
    await u.click(await within(s).findByRole("button", { name: "Rechazar cambio de horario" }));
    await u.click(within(s).getByRole("button", { name: "Confirmar rechazo del cambio" }));
    expect(await within(s).findByText("No hay cambios de horario pendientes.")).toBeInTheDocument();
    expect(b.cuantas("POST /turnos/t-10/cambio/rechazar")).toBe(1);
  });

  it("mientras la operación está en vuelo deshabilita las acciones del turno y no envía dos veces", async () => {
    let soltar: (r: RespuestaFalsa) => void = () => undefined;
    const retenida = new Promise<RespuestaFalsa>((resolver) => (soltar = resolver));
    const b = backend([VIGENTE], { "POST /turnos/t-9/cambio/aprobar": () => retenida });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const s = await seccion();
    const fila = await filaDe(s, "Lucía Pérez");
    await u.click(within(fila).getByRole("button", { name: "Aprobar cambio de horario" }));
    const confirmar = within(fila).getByRole("button", { name: "Confirmar aprobación del cambio" });
    await u.click(confirmar);
    await waitFor(() => expect(confirmar).toBeDisabled());
    expect(within(fila).getByRole("button", { name: "Volver" })).toBeDisabled();
    await u.click(confirmar);
    expect(b.cuantas("POST /turnos/t-9/cambio/aprobar")).toBe(1);
    soltar(resultado("enviado"));
    await waitFor(() => expect(within(fila).queryByRole("button", { name: "Confirmar aprobación del cambio" })).not.toBeInTheDocument());
  });

  it("al rechazar en vuelo no queda habilitada ninguna otra resolución del turno", async () => {
    let soltar: (r: RespuestaFalsa) => void = () => undefined;
    const retenida = new Promise<RespuestaFalsa>((resolver) => (soltar = resolver));
    backend([VIGENTE], { "POST /turnos/t-9/cambio/rechazar": () => retenida });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const fila = await filaDe(await seccion(), "Lucía Pérez");
    await u.click(within(fila).getByRole("button", { name: "Rechazar cambio de horario" }));
    await u.click(within(fila).getByRole("button", { name: "Confirmar rechazo del cambio" }));
    await waitFor(() => expect(within(fila).getByRole("button", { name: "Confirmar rechazo del cambio" })).toBeDisabled());
    expect(within(fila).queryByRole("button", { name: "Aprobar cambio de horario" })).not.toBeInTheDocument();
    for (const boton of within(fila).getAllByRole("button")) expect(boton).toBeDisabled();
    soltar(resultado("enviado"));
    await waitFor(() => expect(within(fila).queryByRole("button", { name: "Confirmar rechazo del cambio" })).not.toBeInTheDocument());
  });

  it.each([
    ["error", /no se pudo avisar al cliente/i],
    ["sin_integracion", /no hay una integración/i],
    ["cliente_anonimizado", /anonimizado/i],
    ["sin_canal", /no tiene un canal verificado/i],
  ])("muestra el aviso %s", async (aviso, texto) => {
    backend([VIGENTE], { "POST /turnos/t-9/cambio/aprobar": () => resultado(aviso) });
    const u = userEvent.setup();
    montarPantalla(<PaginaTurnos />);
    const s = await seccion();
    await u.click(await within(s).findByRole("button", { name: "Aprobar cambio de horario" }));
    await u.click(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" }));
    expect(await screen.findByRole("status")).toHaveTextContent(texto);
  });

  describe("errores del servidor", () => {
    const REFRESCAN: [string, string, RegExp][] = [
      ["cambio_no_pendiente", "El turno no tiene un cambio de horario pendiente.", /ya no tiene un cambio de horario pendiente/],
      ["cambio_vencido", "El cambio de horario venció.", /venció o el turno ya empezó.*rechazarlo/],
    ];

    it.each(REFRESCAN)("409 %s: muestra el texto y vuelve a pedir la lista", async (codigo, mensaje, texto) => {
      const b = backend([VIGENTE], { "POST /turnos/t-9/cambio/aprobar": () => error(409, codigo, mensaje) });
      const u = userEvent.setup();
      montarPantalla(<PaginaTurnos />);
      const s = await seccion();
      await u.click(await within(s).findByRole("button", { name: "Aprobar cambio de horario" }));
      const antes = listasDeCambios(b);
      await u.click(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(texto);
      await waitFor(() => expect(listasDeCambios(b)).toBeGreaterThan(antes));
    });

    const SIN_REFRESCAR: [string, number, string, RegExp][] = [
      ["horario_ocupado", 409, "El horario nuevo se superpone.", /horario está ocupado/],
      ["operacion_concurrente", 409, "Hay otra operación en curso.", /Otro cambio sobre este turno está en curso/],
      ["sin_permiso", 403, "Falta el permiso.", /No tenés permiso para hacer este cambio/],
      ["turno_no_encontrado", 404, "No existe ese turno.", /No existe ese turno\./],
    ];

    it.each(SIN_REFRESCAR)("%s (%i): muestra el texto en la fila y deja reintentar", async (codigo, status, mensaje, texto) => {
      const b = backend([VIGENTE], { "POST /turnos/t-9/cambio/aprobar": () => error(status, codigo, mensaje) });
      const u = userEvent.setup();
      montarPantalla(<PaginaTurnos />);
      const s = await seccion();
      await u.click(await within(s).findByRole("button", { name: "Aprobar cambio de horario" }));
      await u.click(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" }));
      expect(await within(s).findByRole("alert")).toHaveTextContent(texto);
      expect(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" })).toBeEnabled();
      expect(b.cuantas("POST /turnos/t-9/cambio/aprobar")).toBe(1);
    });

    it("422: muestra el mensaje del campo", async () => {
      backend([VIGENTE], {
        "POST /turnos/t-9/cambio/rechazar": () => ({
          status: 422,
          cuerpo: { detail: [{ loc: ["path", "turno_id"], msg: "El identificador no es válido", type: "uuid_parsing" }] },
        }),
      });
      const u = userEvent.setup();
      montarPantalla(<PaginaTurnos />);
      const s = await seccion();
      await u.click(await within(s).findByRole("button", { name: "Rechazar cambio de horario" }));
      await u.click(within(s).getByRole("button", { name: "Confirmar rechazo del cambio" }));
      expect(await within(s).findByRole("alert")).toHaveTextContent("El identificador no es válido");
    });

    it("un error se limpia al reintentar", async () => {
      backend([VIGENTE], {
        "POST /turnos/t-9/cambio/aprobar": [error(403, "sin_permiso", "Falta el permiso."), resultado("enviado")],
      });
      const u = userEvent.setup();
      montarPantalla(<PaginaTurnos />);
      const s = await seccion();
      await u.click(await within(s).findByRole("button", { name: "Aprobar cambio de horario" }));
      await u.click(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" }));
      expect(await within(s).findByRole("alert")).toBeInTheDocument();
      await u.click(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" }));
      await waitFor(() => expect(within(s).queryByRole("alert")).not.toBeInTheDocument());
    });
  });

  describe("estados de la lista", () => {
    it("muestra la carga mientras llega la respuesta y después el vacío", async () => {
      let soltar: (r: RespuestaFalsa) => void = () => undefined;
      const retenida = new Promise<RespuestaFalsa>((resolver) => (soltar = resolver));
      backend(() => retenida);
      montarPantalla(<PaginaTurnos />);
      const s = await seccion();
      expect(await within(s).findByText("Cargando…")).toBeInTheDocument();
      soltar(lista([]));
      expect(await within(s).findByText("No hay cambios de horario pendientes.")).toBeInTheDocument();
    });

    it("muestra el error de la carga y reintenta", async () => {
      let falla = true;
      const b = backend(() => (falla ? error(500, "error_interno", "No se pudieron cargar los cambios.") : lista([VIGENTE])));
      const u = userEvent.setup();
      montarPantalla(<PaginaTurnos />);
      const s = await seccion();
      expect(await within(s).findByRole("alert")).toHaveTextContent("No se pudieron cargar los cambios.");
      falla = false;
      await u.click(within(s).getByRole("button", { name: "Reintentar" }));
      expect(await within(s).findByText("Lucía Pérez")).toBeInTheDocument();
      expect(within(s).queryByRole("alert")).not.toBeInTheDocument();
      expect(listasDeCambios(b)).toBe(2);
    });
  });

  describe("permisos", () => {
    it("un usuario no dueño con approve_bookings ve la sección y puede resolver", async () => {
      const b = backend([VIGENTE], { "POST /turnos/t-9/cambio/aprobar": () => resultado("enviado") }, SOLO_APROBAR);
      const u = userEvent.setup();
      montarPantalla(<PaginaTurnos />);
      const s = await seccion();
      await u.click(await within(s).findByRole("button", { name: "Aprobar cambio de horario" }));
      await u.click(within(s).getByRole("button", { name: "Confirmar aprobación del cambio" }));
      await waitFor(() => expect(b.cuantas("POST /turnos/t-9/cambio/aprobar")).toBe(1));
    });

    it("sin approve_bookings (incluido el profesional vinculado) no hay sección, ni botones, ni pedido de cambios", async () => {
      const b = backend([VIGENTE], {}, SOLO_VER);
      montarPantalla(<PaginaTurnos />);
      expect(await screen.findByText(/Solo podés ver los turnos/)).toBeInTheDocument();
      await screen.findByRole("region", { name: "Agenda" });
      expect(screen.queryByRole("region", { name: "Cambios de horario pendientes" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /cambio de horario/i })).not.toBeInTheDocument();
      expect(listasDeCambios(b)).toBe(0);
    });
  });

  it("la agenda también muestra el cambio pendiente del turno", async () => {
    backend([], {
      "GET /turnos": (_c, url) => lista(url.includes("cambio_pendiente=true") ? [] : [{ ...VIGENTE, start_at: new Date(Date.now() + 3_600_000).toISOString() }]),
    });
    montarPantalla(<PaginaTurnos />);
    const a = await screen.findByRole("region", { name: "Agenda" });
    expect(await within(a).findByText("Cambio de horario pendiente")).toBeInTheDocument();
  });
});
