import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend, sesionDe } from "../test/falsoBackend";
import { montarEn } from "../test/montar";

afterEach(() => {
  vi.unstubAllGlobals();
});

const CLAVE = "tenant_deletion_grace_period_days";
const PUT = `PUT /settings/${CLAVE}`;

function backend(put: Parameters<typeof instalarBackend>[0][string]) {
  return instalarBackend({
    "GET /auth/sesion": [sesionDe({ aal: "aal2" })],
    "GET /settings": [{ cuerpo: { [CLAVE]: 90 } }],
    [PUT]: put,
  });
}

async function escribir(valor: string) {
  const usuario = userEvent.setup();
  montarEn("/configuracion");
  const campo = await screen.findByLabelText(/Período de gracia/);
  await usuario.clear(campo);
  await usuario.type(campo, valor);
  await usuario.click(screen.getByRole("button", { name: "Guardar" }));
  return usuario;
}

const REQUIERE = { status: 409, cuerpo: { detail: { codigo: "confirmacion_reduccion_requerida", mensaje: "x" } } };

describe("configuración: período de gracia", () => {
  it("no deja enviar menos de 30 días", async () => {
    const b = backend([{ cuerpo: {} }]);
    await escribir("29");
    expect(await screen.findByRole("alert")).toHaveTextContent("mínimo es de 30 días");
    expect(b.cuantas(PUT)).toBe(0);
  });

  it("subir o igualar no pide confirmación", async () => {
    const b = backend([{ cuerpo: { key: CLAVE, desde: 90, hasta: 120 } }]);
    await escribir("120");
    expect(await screen.findByText(/Guardado: 90 → 120/)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(b.llamadas.filter((l) => l.clave === PUT)[0]?.cuerpo).toEqual({ valor: 120, confirmar_reduccion: false });
  });

  it("al bajar pide confirmación; cancelar no reenvía", async () => {
    const b = backend([REQUIERE]);
    const usuario = await escribir("45");
    const dialogo = await screen.findByRole("alertdialog");
    expect(dialogo).toHaveTextContent("de 90 a 45 días");
    expect(dialogo).toHaveTextContent("es irreversible");
    await usuario.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(b.cuantas(PUT)).toBe(1);
  });

  it("aceptar reenvía con confirmar_reduccion true", async () => {
    const b = backend([REQUIERE, { cuerpo: { key: CLAVE, desde: 90, hasta: 45 } }]);
    const usuario = await escribir("45");
    await screen.findByRole("alertdialog");
    await usuario.click(screen.getByRole("button", { name: "Confirmar reducción" }));
    expect(await screen.findByText(/Guardado: 90 → 45/)).toBeInTheDocument();
    const puts = b.llamadas.filter((l) => l.clave === PUT);
    expect(puts.map((l) => l.cuerpo)).toEqual([
      { valor: 45, confirmar_reduccion: false },
      { valor: 45, confirmar_reduccion: true },
    ]);
  });

  it("traduce el 422 del backend al mínimo de 30 días", async () => {
    backend([{ status: 422, cuerpo: { detail: [{ loc: ["body", "valor"], msg: "min" }] } }]);
    await escribir("30");
    expect(await screen.findByRole("alert")).toHaveTextContent("mínimo es de 30 días");
  });
});
