import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Tenant } from "../../api/panel";
import { instalarBackend, sesionDe } from "../../test/falsoBackend";
import { montarEn } from "../../test/montar";

afterEach(() => {
  vi.unstubAllGlobals();
});

const ID = "aaaaaaaa-0000-0000-0000-000000000001";
const SESION = { "GET /auth/sesion": [sesionDe({ aal: "aal2" })] };

function tenant(extra: Partial<Tenant> = {}): Tenant {
  return {
    id: ID,
    name: "Peluquería Ana",
    slug: "peluqueria-ana",
    plan: "pilot",
    status: "active",
    deactivated_at: null,
    paid_until: "2026-11-15",
    created_at: "2026-10-05T14:30:00Z",
    invitacion_pendiente: false,
    owner_nombre: "Ana Gómez",
    owner_email: "ana.owner@example.com",
    ...extra,
  };
}

const FACTURACION = { [`GET /tenants/${ID}/facturacion`]: [{ cuerpo: { tenant_id: ID, paid_until: null, billing_notes: null } }] };

function error(status: number, codigo: string, mensaje: string) {
  return { status, cuerpo: { detail: { codigo, mensaje } } };
}

describe("lista de tenants", () => {
  it("muestra cada tenant con su estado, el pago y la fecha en hora de Buenos Aires", async () => {
    instalarBackend({
      ...SESION,
      "GET /tenants": [
        {
          cuerpo: [
            tenant(),
            tenant({ id: "b", name: "Gym Sur", slug: "gym-sur", status: "suspended", invitacion_pendiente: true }),
          ],
        },
      ],
    });
    montarEn("/tenants");
    expect(await screen.findByRole("link", { name: "Peluquería Ana" })).toHaveAttribute("href", `/tenants/${ID}`);
    const fila = screen.getByRole("link", { name: "Peluquería Ana" }).closest("tr")!;
    expect(within(fila).getByText("Activo")).toBeInTheDocument();
    expect(within(fila).getByText("15/11/2026")).toBeInTheDocument();
    // 14:30 UTC son las 11:30 en Buenos Aires.
    expect(within(fila).getByText(/05\/10\/2026.*11:30/)).toBeInTheDocument();
    const otra = screen.getByRole("link", { name: "Gym Sur" }).closest("tr")!;
    expect(within(otra).getByText("Suspendido")).toBeInTheDocument();
    expect(within(otra).getByText(/Invitación pendiente/)).toBeInTheDocument();
  });

  it("filtra por estado en el backend", async () => {
    const backend = instalarBackend({ ...SESION, "GET /tenants": [{ cuerpo: [tenant()] }] });
    const usuario = userEvent.setup();
    montarEn("/tenants");
    await screen.findByRole("link", { name: "Peluquería Ana" });
    await usuario.selectOptions(screen.getByLabelText("Estado"), "inactive");
    await vi.waitFor(() => expect(backend.llamadas.some((l) => l.url === "/tenants?status=inactive")).toBe(true));
  });
});

describe("identidad del owner", () => {
  it("la lista muestra nombre y email del owner, o Sin nombre si no se conoce", async () => {
    instalarBackend({
      ...SESION,
      "GET /tenants": [
        { cuerpo: [tenant(), tenant({ id: "b", name: "Gym Sur", owner_nombre: null, owner_email: "gym@example.com" })] },
      ],
    });
    montarEn("/tenants");
    expect(await screen.findByText("Ana Gómez")).toBeInTheDocument();
    expect(screen.getByText("ana.owner@example.com")).toBeInTheDocument();
    expect(screen.getByText("Sin nombre")).toBeInTheDocument();
    expect(screen.getByText("gym@example.com")).toBeInTheDocument();
  });

  it("la ficha muestra nombre y email del owner", async () => {
    instalarBackend({ ...SESION, ...FACTURACION, [`GET /tenants/${ID}`]: [{ cuerpo: tenant() }] });
    montarEn(`/tenants/${ID}`);
    const dd = (await screen.findByText("Owner")).nextElementSibling as HTMLElement;
    expect(dd).toHaveTextContent("Ana Gómez");
    expect(dd).toHaveTextContent("ana.owner@example.com");
  });
});

describe("alta de tenant", () => {
  async function completar(usuario: ReturnType<typeof userEvent.setup>) {
    await usuario.type(await screen.findByLabelText("Nombre del negocio"), "Peluquería Ana");
    await usuario.type(screen.getByLabelText(/^Slug/), "peluqueria-ana");
    await usuario.type(screen.getByLabelText(/^Nombre del owner/), "Ana Gómez");
    await usuario.type(screen.getByLabelText(/^Email del owner/), "ana@example.com");
    await usuario.click(screen.getByRole("button", { name: "Crear tenant" }));
  }

  it("crea el tenant y confirma que la invitación salió", async () => {
    const backend = instalarBackend({
      ...SESION,
      "POST /tenants": [{ status: 201, cuerpo: { tenant_id: ID, estado_invitacion: "ok" } }],
    });
    const usuario = userEvent.setup();
    montarEn("/tenants/nuevo");
    await completar(usuario);

    expect(await screen.findByRole("status")).toHaveTextContent("la invitación al owner fue enviada");
    expect(backend.llamadas.find((l) => l.clave === "POST /tenants")?.cuerpo).toEqual({
      name: "Peluquería Ana",
      slug: "peluqueria-ana",
      owner_email: "ana@example.com",
      owner_nombre: "Ana Gómez",
    });
    expect(screen.getByRole("link", { name: /ficha del tenant/ })).toHaveAttribute("href", `/tenants/${ID}`);
    expect(screen.queryByText("ana@example.com")).not.toBeInTheDocument();
  });

  it("muestra el 422 datos_invalidos del nombre del owner", async () => {
    instalarBackend({
      ...SESION,
      "POST /tenants": [
        {
          status: 422,
          cuerpo: { detail: { codigo: "datos_invalidos", mensaje: "owner_nombre inválido: máximo 120 caracteres" } },
        },
      ],
    });
    const usuario = userEvent.setup();
    montarEn("/tenants/nuevo");
    await completar(usuario);
    expect(await screen.findByRole("alert")).toHaveTextContent("owner_nombre inválido");
  });

  it("avisa si la invitación quedó pendiente", async () => {
    instalarBackend({
      ...SESION,
      "POST /tenants": [{ status: 201, cuerpo: { tenant_id: ID, estado_invitacion: "invite_pendiente" } }],
    });
    const usuario = userEvent.setup();
    montarEn("/tenants/nuevo");
    await completar(usuario);
    expect(await screen.findByRole("alert")).toHaveTextContent("reenviá la invitación desde la ficha");
  });

  it("muestra el 409 de slug repetido y deja corregir", async () => {
    instalarBackend({
      ...SESION,
      "POST /tenants": [error(409, "slug_en_uso", "Ya existe un tenant con ese slug")],
    });
    const usuario = userEvent.setup();
    montarEn("/tenants/nuevo");
    await completar(usuario);
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya existe un tenant con ese slug");
    expect(screen.getByRole("button", { name: "Crear tenant" })).toBeEnabled();
  });
});

describe("ficha: ciclo de vida", () => {
  function ficha(t: Tenant, extra: Record<string, unknown[]> = {}) {
    return instalarBackend({
      ...SESION,
      ...FACTURACION,
      [`GET /tenants/${ID}`]: [{ cuerpo: t }],
      ...extra,
    } as Parameters<typeof instalarBackend>[0]);
  }

  it.each([
    ["active", ["Suspender", "Dar de baja"], ["Levantar suspensión", "Reactivar"]],
    ["suspended", ["Levantar suspensión", "Dar de baja"], ["Suspender", "Reactivar"]],
    ["inactive", ["Reactivar"], ["Suspender", "Levantar suspensión", "Dar de baja"]],
    ["purging", [], ["Suspender", "Levantar suspensión", "Dar de baja", "Reactivar"]],
    ["purged", [], ["Suspender", "Levantar suspensión", "Dar de baja", "Reactivar"]],
  ] as const)("en estado %s ofrece solo las acciones válidas", async (status, si, no) => {
    ficha(tenant({ status }));
    montarEn(`/tenants/${ID}`);
    await screen.findByRole("heading", { name: "Peluquería Ana" });
    for (const nombre of si) expect(screen.getByRole("button", { name: nombre })).toBeInTheDocument();
    for (const nombre of no) expect(screen.queryByRole("button", { name: nombre })).not.toBeInTheDocument();
    if (status === "purged") {
      expect(screen.getByText(/fue purgado/)).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /solo lectura/ })).not.toBeInTheDocument();
    }
  });

  it("en purga en curso muestra el badge y explica que no admite acciones", async () => {
    ficha(tenant({ status: "purging" }));
    montarEn(`/tenants/${ID}`);
    await screen.findByRole("heading", { name: "Peluquería Ana" });
    expect(screen.getAllByText("Purgando").length).toBeGreaterThan(0);
    expect(screen.getByText(/purga de este negocio está en curso/)).toBeInTheDocument();
  });

  it("el listado muestra el badge Purgando para un tenant en purging", async () => {
    instalarBackend({
      ...SESION,
      "GET /tenants": [{ cuerpo: [tenant({ status: "purging" })] }],
    });
    montarEn("/tenants");
    const fila = (await screen.findByRole("link", { name: "Peluquería Ana" })).closest("tr")!;
    expect(within(fila).getByText("Purgando")).toBeInTheDocument();
  });

  it("si el backend responde purga_en_curso lo muestra con un mensaje claro", async () => {
    ficha(tenant(), {
      [`POST /tenants/${ID}/suspender`]: [error(409, "purga_en_curso", "x")],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.click(await screen.findByRole("button", { name: "Suspender" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/purga de este negocio está en curso/);
  });

  it("suspende y recarga la ficha con el estado nuevo", async () => {
    const backend = ficha(tenant(), {
      [`GET /tenants/${ID}`]: [{ cuerpo: tenant() }, { cuerpo: tenant({ status: "suspended" }) }],
      [`POST /tenants/${ID}/suspender`]: [{ status: 204 }],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.click(await screen.findByRole("button", { name: "Suspender" }));

    expect(await screen.findByRole("button", { name: "Levantar suspensión" })).toBeInTheDocument();
    expect(screen.getByText("Tenant suspendido.")).toBeInTheDocument();
    expect(backend.cuantas(`POST /tenants/${ID}/suspender`)).toBe(1);
  });

  it("una transición inválida (409) muestra el error y refresca el estado real", async () => {
    ficha(tenant(), {
      [`GET /tenants/${ID}`]: [{ cuerpo: tenant() }, { cuerpo: tenant({ status: "inactive" }) }],
      [`POST /tenants/${ID}/suspender`]: [error(409, "transicion_invalida", "No se puede pasar de inactive a suspended")],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.click(await screen.findByRole("button", { name: "Suspender" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No se puede pasar de inactive a suspended");
    expect(await screen.findByRole("button", { name: "Reactivar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspender" })).not.toBeInTheDocument();
  });

  it("dar de baja exige explicar el período de gracia y escribir el slug", async () => {
    const backend = ficha(tenant(), {
      "GET /settings": [{ cuerpo: { tenant_deletion_grace_period_days: 90 } }],
      [`GET /tenants/${ID}`]: [{ cuerpo: tenant() }, { cuerpo: tenant({ status: "inactive", deactivated_at: "2026-10-05T15:00:00Z" }) }],
      [`POST /tenants/${ID}/dar-de-baja`]: [{ status: 204 }],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.click(await screen.findByRole("button", { name: "Dar de baja" }));

    const dialogo = await screen.findByRole("alertdialog");
    expect(await within(dialogo).findByText(/período de gracia de 90 días/)).toBeInTheDocument();
    expect(within(dialogo).getByText(/purga es irreversible/)).toBeInTheDocument();
    const confirmar = within(dialogo).getByRole("button", { name: "Confirmar baja" });
    expect(confirmar).toBeDisabled();
    expect(backend.cuantas(`POST /tenants/${ID}/dar-de-baja`)).toBe(0);

    await usuario.type(within(dialogo).getByLabelText(/escribí el slug/), "otro-slug");
    expect(confirmar).toBeDisabled();
    await usuario.clear(within(dialogo).getByLabelText(/escribí el slug/));
    await usuario.type(within(dialogo).getByLabelText(/escribí el slug/), "peluqueria-ana");
    await usuario.click(confirmar);

    expect(await screen.findByText("Tenant dado de baja.")).toBeInTheDocument();
    expect(backend.cuantas(`POST /tenants/${ID}/dar-de-baja`)).toBe(1);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "Reactivar" })).toBeInTheDocument();
  });

  it("cancelar la confirmación no da de baja", async () => {
    const backend = ficha(tenant());
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.click(await screen.findByRole("button", { name: "Dar de baja" }));
    await usuario.click(await screen.findByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(backend.cuantas(`POST /tenants/${ID}/dar-de-baja`)).toBe(0);
  });
});

describe("ficha: invitación pendiente", () => {
  it("reenvía sin volver a pedir el email y deja de mostrarse como pendiente", async () => {
    const backend = instalarBackend({
      ...SESION,
      ...FACTURACION,
      [`GET /tenants/${ID}`]: [
        { cuerpo: tenant({ invitacion_pendiente: true }) },
        { cuerpo: tenant({ invitacion_pendiente: false }) },
      ],
      [`POST /tenants/${ID}/reenviar-invitacion`]: [{ cuerpo: { tenant_id: ID, estado_invitacion: "ok" } }],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.click(await screen.findByRole("button", { name: "Reenviar invitación" }));

    expect(await screen.findByText("Invitación enviada.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === `POST /tenants/${ID}/reenviar-invitacion`)?.cuerpo).toBeUndefined();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Reenviar invitación" })).not.toBeInTheDocument());
  });

  it("permite corregir el email y muestra el 409 si ya no hay invitación pendiente", async () => {
    const backend = instalarBackend({
      ...SESION,
      ...FACTURACION,
      [`GET /tenants/${ID}`]: [{ cuerpo: tenant({ invitacion_pendiente: true }) }],
      [`POST /tenants/${ID}/reenviar-invitacion`]: [
        error(409, "invitacion_no_pendiente", "El tenant no tiene una invitación pendiente"),
      ],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.type(await screen.findByLabelText(/Corregir el nombre/), "Ana Nueva");
    await usuario.type(screen.getByLabelText(/Corregir el email/), "nuevo@example.com");
    await usuario.click(screen.getByRole("button", { name: "Reenviar invitación" }));

    expect(await screen.findByText("El tenant no tiene una invitación pendiente")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === `POST /tenants/${ID}/reenviar-invitacion`)?.cuerpo).toEqual({
      owner_email: "nuevo@example.com",
      owner_nombre: "Ana Nueva",
    });
  });

  it("reenvía solo con el nombre corregido", async () => {
    const backend = instalarBackend({
      ...SESION,
      ...FACTURACION,
      [`GET /tenants/${ID}`]: [{ cuerpo: tenant({ invitacion_pendiente: true }) }],
      [`POST /tenants/${ID}/reenviar-invitacion`]: [{ cuerpo: { tenant_id: ID, estado_invitacion: "ok" } }],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    await usuario.type(await screen.findByLabelText(/Corregir el nombre/), "Solo Nombre");
    await usuario.click(screen.getByRole("button", { name: "Reenviar invitación" }));
    expect(await screen.findByText("Invitación enviada.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === `POST /tenants/${ID}/reenviar-invitacion`)?.cuerpo).toEqual({
      owner_nombre: "Solo Nombre",
    });
  });

  it("sin invitación pendiente no ofrece reenviar", async () => {
    instalarBackend({ ...SESION, ...FACTURACION, [`GET /tenants/${ID}`]: [{ cuerpo: tenant() }] });
    montarEn(`/tenants/${ID}`);
    await screen.findByRole("heading", { name: "Peluquería Ana" });
    expect(screen.queryByRole("button", { name: "Reenviar invitación" })).not.toBeInTheDocument();
  });
});

describe("ficha: facturación", () => {
  it("carga el estado de pago y guarda los dos campos; vaciar uno manda null", async () => {
    const backend = instalarBackend({
      ...SESION,
      [`GET /tenants/${ID}`]: [{ cuerpo: tenant() }],
      [`GET /tenants/${ID}/facturacion`]: [
        { cuerpo: { tenant_id: ID, paid_until: "2026-11-15", billing_notes: "Transferencia de octubre" } },
      ],
      [`PUT /tenants/${ID}/facturacion`]: [{ cuerpo: { tenant_id: ID, paid_until: "2026-12-15", billing_notes: null } }],
    });
    const usuario = userEvent.setup();
    montarEn(`/tenants/${ID}`);
    const fecha = await screen.findByLabelText("Pagado hasta");
    expect(fecha).toHaveValue("2026-11-15");
    expect(screen.getByLabelText("Notas de facturación")).toHaveValue("Transferencia de octubre");

    await usuario.clear(fecha);
    await usuario.type(fecha, "2026-12-15");
    await usuario.clear(screen.getByLabelText("Notas de facturación"));
    await usuario.click(screen.getByRole("button", { name: "Guardar facturación" }));

    expect(await screen.findByText("Facturación guardada.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === `PUT /tenants/${ID}/facturacion`)?.cuerpo).toEqual({
      paid_until: "2026-12-15",
      billing_notes: null,
    });
  });
});

describe("ficha: credencial de Telegram", () => {
  const RESPUESTA_PUT = {
    cuerpo: {
      integration_id: "i-1",
      tenant_id: ID,
      integration_type: "telegram_bot",
      webhook_key: "11111111-2222-3333-4444-555555555555",
    },
  };
  const CLAVE_PUT = `PUT /tenants/${ID}/integraciones/telegram_bot`;

  function montarFicha(respuestas: object[]) {
    const backend = instalarBackend({
      ...SESION,
      ...FACTURACION,
      [`GET /tenants/${ID}`]: [{ cuerpo: tenant() }],
      [CLAVE_PUT]: respuestas,
    });
    return { backend, ...montarEn(`/tenants/${ID}`) };
  }

  it("manda el JSON {bot_token, webhook_secret}, muestra una vez el secreto y vacía los campos", async () => {
    const { backend, container } = montarFicha([RESPUESTA_PUT]);
    const usuario = userEvent.setup();
    const campo = await screen.findByLabelText(/Token del bot/);
    expect(campo).toHaveAttribute("type", "password");
    await usuario.type(campo, "123456:ABC-super-secreto");
    await usuario.type(screen.getByLabelText(/Secreto del webhook/), "mi_secreto-1_largo-suficiente-xyz0123");
    await usuario.click(screen.getByRole("button", { name: "Guardar credencial" }));

    expect(await screen.findByText(/Credencial guardada/)).toBeInTheDocument();
    expect(screen.getByText("11111111-2222-3333-4444-555555555555")).toBeInTheDocument();
    expect(screen.getByText("mi_secreto-1_largo-suficiente-xyz0123")).toBeInTheDocument();
    expect(screen.getByText(/Copialo ahora/)).toBeInTheDocument();
    const cuerpo = backend.llamadas.find((l) => l.clave === CLAVE_PUT)?.cuerpo as { secret: string };
    expect(JSON.parse(cuerpo.secret)).toEqual({ bot_token: "123456:ABC-super-secreto", webhook_secret: "mi_secreto-1_largo-suficiente-xyz0123" });
    expect(screen.getByLabelText(/Token del bot/)).toHaveValue("");
    expect(screen.getByLabelText(/Secreto del webhook/)).toHaveValue("");
    expect(container.innerHTML).not.toContain("super-secreto");
    expect(screen.getByRole("button", { name: "Guardar credencial" })).toBeDisabled();
  });

  it("genera un secreto aleatorio de 32+ caracteres válidos cuando el campo queda vacío", async () => {
    const { backend } = montarFicha([RESPUESTA_PUT]);
    const usuario = userEvent.setup();
    await usuario.type(await screen.findByLabelText(/Token del bot/), "123456:ABCdef");
    await usuario.click(screen.getByRole("button", { name: "Guardar credencial" }));

    await screen.findByText(/Credencial guardada/);
    const cuerpo = backend.llamadas.find((l) => l.clave === CLAVE_PUT)?.cuerpo as { secret: string };
    const { webhook_secret } = JSON.parse(cuerpo.secret) as { webhook_secret: string };
    expect(webhook_secret).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    expect(screen.getByText(webhook_secret)).toBeInTheDocument();
  });

  it("rechaza un token mal formado sin llamar al backend", async () => {
    const { backend } = montarFicha([RESPUESTA_PUT]);
    const usuario = userEvent.setup();
    await usuario.type(await screen.findByLabelText(/Token del bot/), "esto-no-es-un-token");
    await usuario.click(screen.getByRole("button", { name: "Guardar credencial" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/formato/);
    expect(backend.llamadas.some((l) => l.clave === CLAVE_PUT)).toBe(false);
  });

  it("si el backend rechaza, muestra su mensaje y no deja nada en pantalla", async () => {
    const { container } = montarFicha([error(422, "datos_invalidos", "El secret no es válido")]);
    const usuario = userEvent.setup();
    await usuario.type(await screen.findByLabelText(/Token del bot/), "123456:valor-secreto-x");
    await usuario.click(screen.getByRole("button", { name: "Guardar credencial" }));

    expect(await screen.findByText("El secret no es válido")).toBeInTheDocument();
    expect(screen.getByLabelText(/Token del bot/)).toHaveValue("");
    expect(container.innerHTML).not.toContain("valor-secreto-x");
  });
});
