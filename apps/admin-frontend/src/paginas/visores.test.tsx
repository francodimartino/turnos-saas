import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../test/falsoBackend";
import { montarEn } from "../test/montar";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ID = "aaaaaaaa-0000-0000-0000-000000000001";
const SESION = { "GET /auth/sesion": [sesionDe({ aal: "aal2" })] };

function entrada(extra: Record<string, unknown> = {}) {
  return {
    id: "e-1",
    tenant_id: ID,
    actor_type: "platform_admin",
    actor_id: "u-1",
    action: "tenant_suspend",
    entity: "tenants",
    entity_id: ID,
    metadata: { motivo: "prueba" },
    created_at: "2026-10-05T14:30:00Z",
    ...extra,
  };
}

describe("configuración de la plataforma", () => {
  it("muestra el período de gracia y lo edita con un entero positivo", async () => {
    const backend = instalarBackend({
      ...SESION,
      "GET /settings": [{ cuerpo: { tenant_deletion_grace_period_days: 90, otra_clave: "x" } }],
      "PUT /settings/tenant_deletion_grace_period_days": [{ cuerpo: { key: "k", desde: 90, hasta: 120 } }],
    });
    const usuario = userEvent.setup();
    montarEn("/configuracion");
    const campo = await screen.findByLabelText(/Período de gracia/);
    expect(campo).toHaveValue("90");
    expect(screen.getByText(/solo lectura/)).toBeInTheDocument();

    await usuario.clear(campo);
    await usuario.type(campo, "120");
    await usuario.click(screen.getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText(/Guardado: 90 → 120/)).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PUT /settings/tenant_deletion_grace_period_days")?.cuerpo).toEqual({
      valor: 120,
      confirmar_reduccion: false,
    });
  });

  it("rechaza valores que no son enteros positivos sin llamar al backend", async () => {
    const backend = instalarBackend({
      ...SESION,
      "GET /settings": [{ cuerpo: { tenant_deletion_grace_period_days: 90 } }],
    });
    const usuario = userEvent.setup();
    montarEn("/configuracion");
    const campo = await screen.findByLabelText(/Período de gracia/);
    for (const malo of ["0", "-5", "1.5", "abc"]) {
      await usuario.clear(campo);
      await usuario.type(campo, malo);
      await usuario.click(screen.getByRole("button", { name: "Guardar" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("entero de días mayor que cero");
    }
    expect(backend.cuantas("PUT /settings/tenant_deletion_grace_period_days")).toBe(0);
  });

  it("muestra el error del backend (422)", async () => {
    instalarBackend({
      ...SESION,
      "GET /settings": [{ cuerpo: { tenant_deletion_grace_period_days: 90 } }],
      "PUT /settings/tenant_deletion_grace_period_days": [
        { status: 422, cuerpo: { detail: { codigo: "datos_invalidos", mensaje: "debe ser un entero positivo" } } },
      ],
    });
    const usuario = userEvent.setup();
    montarEn("/configuracion");
    await screen.findByLabelText(/Período de gracia/);
    await usuario.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("debe ser un entero positivo");
  });
});

describe("auditoría del tenant", () => {
  it("lista las entradas en hora de Buenos Aires", async () => {
    instalarBackend({ ...SESION, [`GET /tenants/${ID}/audit-log`]: [{ cuerpo: [entrada()] }] });
    montarEn(`/tenants/${ID}/auditoria`);
    const fila = (await screen.findByText("tenant_suspend")).closest("tr")!;
    expect(within(fila).getByText(/05\/10\/2026.*11:30/)).toBeInTheDocument();
    expect(within(fila).getByText("platform_admin")).toBeInTheDocument();
    expect(within(fila).getByText(/motivo/)).toBeInTheDocument();
  });

  it("aplica los filtros en el backend, con las fechas en hora de Buenos Aires", async () => {
    const backend = instalarBackend({
      ...SESION,
      [`GET /tenants/${ID}/audit-log`]: [{ cuerpo: [entrada()] }, { cuerpo: [] }],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}/auditoria`);
    await screen.findByText("tenant_suspend");

    await usuario.type(screen.getByLabelText("Acción"), "impersonate_view");
    await usuario.selectOptions(screen.getByLabelText("Actor"), "platform_admin");
    await usuario.type(screen.getByLabelText(/Desde/), "2026-10-01T09:00");
    await usuario.type(screen.getByLabelText(/Hasta/), "2026-10-05T18:30");
    await usuario.click(screen.getByRole("button", { name: "Filtrar" }));

    expect(await screen.findByText("No hay registros para esos filtros.")).toBeInTheDocument();
    const ultima = backend.llamadas.filter((l) => l.clave === `GET /tenants/${ID}/audit-log`).at(-1)!;
    const q = new URL(ultima.url, "http://x").searchParams;
    expect(q.get("action")).toBe("impersonate_view");
    expect(q.get("actor_type")).toBe("platform_admin");
    expect(q.get("desde")).toBe("2026-10-01T09:00:00-03:00");
    expect(q.get("hasta")).toBe("2026-10-05T18:30:00-03:00");
  });

  it("muestra el error si el tenant no existe", async () => {
    instalarBackend({
      ...SESION,
      [`GET /tenants/${ID}/audit-log`]: [
        { status: 404, cuerpo: { detail: { codigo: "tenant_no_encontrado", mensaje: "El tenant no existe" } } },
      ],
    });
    montarEn(`/tenants/${ID}/auditoria`);
    expect(await screen.findByRole("alert")).toHaveTextContent("El tenant no existe");
  });
});

describe("notificaciones con problemas", () => {
  it("lista las notificaciones fallidas/atascadas y las limpiezas de Calendar fallidas", async () => {
    instalarBackend({
      ...SESION,
      "GET /notificaciones/problemas": [
        {
          cuerpo: {
            notificaciones: [
              {
                id: "n-1",
                tenant_id: ID,
                end_customer_id: "c-1",
                channel: "telegram",
                template: "recordatorio",
                status: "failed",
                retry_count: 3,
                last_error: "chat no encontrado",
                created_at: "2026-10-05T14:30:00Z",
                sent_at: null,
              },
              {
                id: "n-2",
                tenant_id: ID,
                end_customer_id: "c-2",
                channel: "whatsapp",
                template: null,
                status: "pending",
                retry_count: 0,
                last_error: null,
                created_at: "2026-10-05T10:00:00Z",
                sent_at: null,
              },
            ],
            limpiezas_calendar: [
              entrada({ id: "l-1", action: "calendar_scrub_failed", actor_type: "system", metadata: { event: "evt-123" } }),
            ],
          },
        },
      ],
    });
    montarEn("/notificaciones");
    const fallida = (await screen.findByText("chat no encontrado")).closest("tr")!;
    expect(within(fallida).getByText("Fallida")).toBeInTheDocument();
    expect(within(fallida).getByText("3")).toBeInTheDocument();
    expect(within(fallida).getByText(/05\/10\/2026.*11:30/)).toBeInTheDocument();
    expect(within(fallida).getByRole("link", { name: "Ver tenant" })).toHaveAttribute("href", `/tenants/${ID}`);
    expect(screen.getByText("Pendiente")).toBeInTheDocument();
    expect(screen.getByText(/evt-123/)).toBeInTheDocument();
    expect(screen.getByText("Sin resolver")).toBeInTheDocument();
  });

  it("muestra un estado por evento: resuelta, o una sola entrada pendiente con sus intentos", async () => {
    instalarBackend({
      ...SESION,
      "GET /notificaciones/problemas": [
        {
          cuerpo: {
            notificaciones: [],
            limpiezas_calendar: [
              entrada({ id: "l-2", action: "calendar_scrub_retry_failed", actor_type: "system", metadata: { event_id: "evt-pend", intentos: 2 } }),
              entrada({ id: "l-3", action: "calendar_scrub_resolved", actor_type: "system", metadata: { event_id: "evt-ok", intentos: 0 } }),
              entrada({ id: "l-4", action: "calendar_scrub_discarded", actor_type: "platform_admin", metadata: { event_id: "evt-desc", motivo: "evento borrado" } }),
            ],
          },
        },
      ],
    });
    montarEn("/notificaciones");
    const pendiente = (await screen.findByText(/evt-pend/)).closest("tr")!;
    expect(within(pendiente).getByText(/Reintento fallido \(2 intentos\)/)).toBeInTheDocument();
    const resuelta = screen.getByText(/evt-ok/).closest("tr")!;
    expect(within(resuelta).getByText("Resuelta")).toBeInTheDocument();
    const descartada = screen.getByText(/evt-desc/).closest("tr")!;
    expect(within(descartada).getByText("Descartada")).toBeInTheDocument();
    expect(screen.queryByText("Sin resolver")).not.toBeInTheDocument();
    expect(screen.getAllByText(/evt-/)).toHaveLength(3);
  });

  it("sin problemas lo dice en cada sección", async () => {
    instalarBackend({
      ...SESION,
      "GET /notificaciones/problemas": [{ cuerpo: { notificaciones: [], limpiezas_calendar: [] } }],
    });
    montarEn("/notificaciones");
    expect(await screen.findByText("No hay notificaciones con problemas.")).toBeInTheDocument();
    expect(screen.getByText("No hay limpiezas pendientes.")).toBeInTheDocument();
  });
});

describe("verificar integridad de la auditoría", () => {
  it("dice que la cadena del tenant y la de la plataforma están íntegras", async () => {
    instalarBackend({
      ...SESION,
      [`GET /tenants/${ID}/audit-log`]: [{ cuerpo: [] }],
      [`GET /tenants/${ID}/audit-log/verificar-cadena`]: [
        { cuerpo: { tenant_id: ID, integra: true, fila_invalida_id: null, fila_invalida_chain_seq: null } },
      ],
      "GET /plataforma/audit-log/verificar-cadena": [
        { cuerpo: { tenant_id: null, integra: true, fila_invalida_id: null, fila_invalida_chain_seq: null } },
      ],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}/auditoria`);
    await usuario.click(await screen.findByRole("button", { name: "Verificar integridad" }));
    expect(await screen.findByText(/del tenant está íntegra/)).toBeInTheDocument();
    await usuario.click(screen.getByRole("button", { name: "Verificar integridad de la plataforma" }));
    expect(await screen.findByText(/de la plataforma está íntegra/)).toBeInTheDocument();
  });

  it("muestra la posición de la primera fila inválida", async () => {
    instalarBackend({
      ...SESION,
      [`GET /tenants/${ID}/audit-log`]: [{ cuerpo: [] }],
      [`GET /tenants/${ID}/audit-log/verificar-cadena`]: [
        { cuerpo: { tenant_id: ID, integra: false, fila_invalida_id: "fila-9", fila_invalida_chain_seq: 42 } },
      ],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}/auditoria`);
    await usuario.click(await screen.findByRole("button", { name: "Verificar integridad" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("NO está íntegra");
    expect(alerta).toHaveTextContent("42");
    expect(alerta).toHaveTextContent("fila-9");
  });

  it("muestra el error del backend", async () => {
    instalarBackend({
      ...SESION,
      [`GET /tenants/${ID}/audit-log`]: [{ cuerpo: [] }],
      [`GET /tenants/${ID}/audit-log/verificar-cadena`]: [
        { status: 404, cuerpo: { detail: { codigo: "tenant_no_encontrado", mensaje: "El tenant no existe" } } },
      ],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}/auditoria`);
    await usuario.click(await screen.findByRole("button", { name: "Verificar integridad" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El tenant no existe");
  });
});

describe("descartar una limpieza de Calendar", () => {
  const PROBLEMAS = {
    cuerpo: {
      notificaciones: [],
      limpiezas_calendar: [
        entrada({ id: "l-1", action: "calendar_scrub_failed", actor_type: "system", metadata: { event_id: "evt-pend", intentos: 1 } }),
        entrada({ id: "l-2", action: "calendar_scrub_resolved", actor_type: "system", metadata: { event_id: "evt-ok" } }),
      ],
    },
  };
  const RUTA = `POST /tenants/${ID}/limpiezas-calendar/descartar`;

  it("bloquea el motivo vacío y no llama al backend", async () => {
    const backend = instalarBackend({ ...SESION, "GET /notificaciones/problemas": [PROBLEMAS] });
    const usuario = userEvent.setup();
    montarEn("/notificaciones");
    await screen.findByRole("button", { name: "Descartar" });
    expect(screen.getAllByRole("button", { name: "Descartar" })).toHaveLength(1);
    await usuario.click(screen.getByRole("button", { name: "Descartar" }));
    await usuario.click(screen.getByRole("button", { name: "Confirmar descarte" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El motivo es obligatorio");
    expect(backend.cuantas(RUTA)).toBe(0);
  });

  it("descarta con motivo y confirmación, y la quita de la lista", async () => {
    const backend = instalarBackend({
      ...SESION,
      "GET /notificaciones/problemas": [PROBLEMAS],
      [RUTA]: [{ cuerpo: { tenant_id: ID, event_id: "evt-pend", estado: "descartada" } }],
    });
    const usuario = userEvent.setup();
    montarEn("/notificaciones");
    await usuario.click(await screen.findByRole("button", { name: "Descartar" }));
    expect(backend.cuantas(RUTA)).toBe(0);
    await usuario.type(screen.getByLabelText("Motivo"), "evento borrado a mano");
    await usuario.click(screen.getByRole("button", { name: "Confirmar descarte" }));
    await waitFor(() => expect(screen.queryByText(/evt-pend/)).not.toBeInTheDocument());
    expect(backend.llamadas.find((l) => l.clave === RUTA)?.cuerpo).toEqual({
      event_id: "evt-pend",
      motivo: "evento borrado a mano",
    });
    expect(screen.getByText(/evt-ok/)).toBeInTheDocument();
  });

  it("muestra el error del backend y deja la fila", async () => {
    instalarBackend({
      ...SESION,
      "GET /notificaciones/problemas": [PROBLEMAS],
      [RUTA]: [
        { status: 404, cuerpo: { detail: { codigo: "limpieza_no_pendiente", mensaje: "La limpieza ya no está pendiente" } } },
      ],
    });
    const usuario = userEvent.setup();
    montarEn("/notificaciones");
    await usuario.click(await screen.findByRole("button", { name: "Descartar" }));
    await usuario.type(screen.getByLabelText("Motivo"), "x");
    await usuario.click(screen.getByRole("button", { name: "Confirmar descarte" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("La limpieza ya no está pendiente");
    expect(screen.getAllByText(/evt-pend/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Confirmar descarte" })).toBeEnabled();
  });
});
