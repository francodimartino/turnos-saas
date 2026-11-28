import { act, screen, waitFor, within } from "@testing-library/react";
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
const RUTA = `POST /tenants/${ID}/slug`;
const CAMBIO = { cuerpo: { tenant_id: ID, slug_anterior: "peluqueria-ana", slug_nuevo: "ana-peluqueria" } };

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

function montar(rutas: Record<string, unknown[]> = {}, fichas: Tenant[] = [tenant()]) {
  const backend = instalarBackend({
    ...SESION,
    [`GET /tenants/${ID}/facturacion`]: [{ cuerpo: { tenant_id: ID, paid_until: null, billing_notes: null } }],
    [`GET /tenants/${ID}`]: fichas.map((cuerpo) => ({ cuerpo })),
    ...rutas,
  } as Parameters<typeof instalarBackend>[0]);
  montarEn(`/tenants/${ID}`);
  return backend;
}

async function escribirYPedirConfirmacion(slug: string) {
  const usuario = userEvent.setup();
  await screen.findByRole("heading", { name: "Slug del negocio" });
  await usuario.type(screen.getByLabelText(/Slug nuevo/), slug);
  await usuario.click(screen.getByRole("button", { name: "Cambiar slug" }));
  const dialogo = await screen.findByRole("alertdialog");
  return { usuario, dialogo };
}

const ERRORES: [string, number, RegExp][] = [
  ["slug_invalido", 422, /El slug no es válido/],
  ["slug_reservado", 422, /está reservado por la plataforma/],
  ["slug_en_uso", 409, /ya lo usa otro negocio/],
  ["slug_sin_cambio", 409, /ya tiene ese slug/],
  ["cambio_slug_concurrente", 409, /se cruzó con este/],
  ["purga_en_curso", 409, /en proceso de purga/],
  ["transicion_invalida", 409, /estado actual del negocio/],
  ["tenant_no_encontrado", 404, /ya no existe/],
];

describe("ficha: cambio de slug", () => {
  it("pide confirmación que explica la redirección de 180 días y cambia el slug", async () => {
    const backend = montar({ [RUTA]: [CAMBIO] });
    const { usuario, dialogo } = await escribirYPedirConfirmacion("  ana-peluqueria ");
    expect(within(dialogo).getByText(/redirige al nuevo durante 180 días/)).toBeInTheDocument();
    expect(backend.cuantas(RUTA)).toBe(0);

    await usuario.click(within(dialogo).getByRole("button", { name: "Confirmar cambio" }));

    const estado = await screen.findByRole("status");
    expect(estado).toHaveTextContent(/Slug cambiado a ana-peluqueria/);
    expect(estado).toHaveTextContent(/peluqueria-ana.*sigue redirigiendo.*180 días/);
    expect(backend.llamadas.find((l) => l.clave === RUTA)?.cuerpo).toEqual({ slug: "ana-peluqueria" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("tras el éxito la ficha se recarga y muestra el slug nuevo", async () => {
    const backend = montar({ [RUTA]: [CAMBIO] }, [tenant(), tenant({ slug: "ana-peluqueria" })]);
    const { usuario, dialogo } = await escribirYPedirConfirmacion("ana-peluqueria");
    const dato = screen.getByText("Slug", { selector: "dt" }).nextElementSibling as HTMLElement;
    expect(dato).toHaveTextContent("peluqueria-ana");
    await usuario.click(within(dialogo).getByRole("button", { name: "Confirmar cambio" }));
    await waitFor(() => expect(dato).toHaveTextContent("ana-peluqueria"));
    expect(backend.cuantas(`GET /tenants/${ID}`)).toBe(2);
  });

  it.each(ERRORES)("muestra un mensaje propio para %s sin reflejar lo escrito ni el texto del servidor", async (codigo, status, patron) => {
    montar({ [RUTA]: [{ status, cuerpo: { detail: { codigo, mensaje: "TEXTO-DEL-SERVIDOR zz-secreto" } } }] });
    const { usuario, dialogo } = await escribirYPedirConfirmacion("zz-secreto");
    await usuario.click(within(dialogo).getByRole("button", { name: "Confirmar cambio" }));

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(patron);
    expect(alerta.textContent).not.toContain("zz-secreto");
    expect(alerta.textContent).not.toContain("TEXTO-DEL-SERVIDOR");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirmar cambio" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeEnabled();
  });

  it("los ocho mensajes son distintos entre sí y de los de las acciones de ciclo de vida", () => {
    expect(new Set(ERRORES.map((e) => e[2].source)).size).toBe(8);
    for (const [, , patron] of ERRORES) expect("no admite cambios de estado").not.toMatch(patron);
  });

  it.each([
    ["un 500 del servidor", { status: 500, cuerpo: { detail: { codigo: "error_interno", mensaje: "boom interno" } } }],
    ["un código que el panel no conoce", { status: 409, cuerpo: { detail: { codigo: "otro_codigo", mensaje: "boom interno" } } }],
    ["una respuesta sin JSON", { status: 502, cuerpo: undefined }],
  ])("ante %s muestra el mensaje genérico", async (_nombre, respuesta) => {
    montar({ [RUTA]: [respuesta] });
    const { usuario, dialogo } = await escribirYPedirConfirmacion("nuevo-slug");
    await usuario.click(within(dialogo).getByRole("button", { name: "Confirmar cambio" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("No se pudo cambiar el slug. Probá de nuevo en unos minutos.");
    expect(alerta.textContent).not.toContain("boom interno");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("ante un fallo de red muestra el mensaje genérico", async () => {
    montar();
    const { usuario, dialogo } = await escribirYPedirConfirmacion("nuevo-slug");
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", (entrada: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? Promise.reject(new TypeError("Failed to fetch")) : real(entrada, init),
    );
    await usuario.click(within(dialogo).getByRole("button", { name: "Confirmar cambio" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudo cambiar el slug/);
  });

  it("deshabilita Confirmar y Cancelar mientras el POST está en vuelo y no envía dos veces", async () => {
    let liberar: (r: Response) => void = () => undefined;
    const retenida = new Promise<Response>((resolver) => {
      liberar = resolver;
    });
    montar();
    const { usuario, dialogo } = await escribirYPedirConfirmacion("ana-peluqueria");
    const real = globalThis.fetch;
    const enviados: string[] = [];
    vi.stubGlobal("fetch", (entrada: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method !== "POST") return real(entrada, init);
      enviados.push(String(entrada));
      return retenida;
    });
    const confirmar = within(dialogo).getByRole("button", { name: "Confirmar cambio" });
    const cancelar = within(dialogo).getByRole("button", { name: "Cancelar" });

    await usuario.click(confirmar);
    expect(confirmar).toBeDisabled();
    expect(cancelar).toBeDisabled();
    await usuario.click(confirmar);
    await usuario.click(cancelar);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    await act(async () => {
      liberar(new Response(JSON.stringify(CAMBIO.cuerpo), { status: 200, headers: { "Content-Type": "application/json" } }));
    });
    expect(await screen.findByRole("status")).toHaveTextContent(/Slug cambiado/);
    expect(enviados).toHaveLength(1);
  });

  it("cancelar la confirmación no cambia nada", async () => {
    const backend = montar();
    const { usuario, dialogo } = await escribirYPedirConfirmacion("ana-peluqueria");
    await usuario.click(within(dialogo).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(backend.cuantas(RUTA)).toBe(0);
  });

  it.each(["purging", "purged"])("en estado %s no ofrece cambiar el slug", async (status) => {
    montar({}, [tenant({ status })]);
    await screen.findByRole("heading", { name: "Peluquería Ana" });
    expect(screen.queryByRole("heading", { name: "Slug del negocio" })).not.toBeInTheDocument();
  });
});
