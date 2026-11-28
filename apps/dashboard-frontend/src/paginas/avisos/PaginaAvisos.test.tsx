import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { seccionesVisibles } from "../../layout/permisos";
import { SECCIONES } from "../../layout/secciones";
import { instalarBackend, sesionDe, type ManejadorFalso, type RespuestaFalsa } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaAvisos } from "./PaginaAvisos";

afterEach(() => vi.unstubAllGlobals());

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const SOLO_EQUIPO = sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_staff"] });
const OTROS_PERMISOS = sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_schedule", "view_bookings"] });
const TEXTO_DEL_BACKEND = "TEXTO-DEL-BACKEND-QUE-NO-SE-MUESTRA";

const ANA = {
  id: "11111111-1111-1111-1111-111111111111",
  nombre: "Ana Pérez",
  email: "ana@negocio.com",
  is_owner: true,
  es_yo: true,
  es_default: true,
  recibe_avisos: true,
  canal_email: true,
  canal_whatsapp: false,
  recibe_por_email: true,
};
const BETO = {
  ...ANA,
  id: "22222222-2222-2222-2222-222222222222",
  nombre: "Beto Gómez",
  email: "beto@negocio.com",
  is_owner: false,
  es_yo: false,
  es_default: false,
  recibe_avisos: false,
  canal_email: false,
  recibe_por_email: false,
};
const PENDIENTE = {
  ...BETO,
  id: "33333333-3333-3333-3333-333333333333",
  nombre: null,
  email: "nueva@negocio.com",
  es_yo: null,
  es_default: true,
  recibe_avisos: false,
  canal_email: false,
};
const CANALES = [
  { canal: "email", disponible: true },
  { canal: "whatsapp", disponible: false },
];
const avisos = (...staff: object[]): RespuestaFalsa => ({ cuerpo: { staff, canales: CANALES } });
const error = (status: number, codigo: string): RespuestaFalsa => ({
  status,
  cuerpo: { detail: { codigo, mensaje: TEXTO_DEL_BACKEND } },
});

function base(extra: Record<string, ManejadorFalso | RespuestaFalsa[]> = {}, inicial = avisos(ANA, BETO, PENDIENTE)) {
  return instalarBackend({ "GET /auth/sesion": [OWNER], "GET /local/avisos": [inicial], ...extra });
}

function retenida(respuesta: RespuestaFalsa) {
  let soltar: () => void = () => undefined;
  const compuerta = new Promise<void>((res) => {
    soltar = res;
  });
  const manejador: ManejadorFalso = async () => {
    await compuerta;
    return respuesta;
  };
  return { manejador, soltar };
}

const cuerpoDelPut = (backend: ReturnType<typeof base>) => backend.llamadas.find((l) => l.clave === "PUT /local/avisos")?.cuerpo;

describe("listado", () => {
  it("lista al equipo con su estado de avisos y su origen (por defecto o personalizado)", async () => {
    base();
    montarPantalla(<PaginaAvisos />);
    expect(await screen.findByText("Ana Pérez")).toBeInTheDocument();
    expect(screen.getByText(/\(vos\)/)).toBeInTheDocument();
    expect(screen.getByText("Beto Gómez")).toBeInTheDocument();
    expect(screen.getByLabelText("Recibe avisos: Ana Pérez")).toBeChecked();
    expect(screen.getByLabelText("Email: Ana Pérez")).toBeChecked();
    expect(screen.getByLabelText("Recibe avisos: Beto Gómez")).not.toBeChecked();
    expect(screen.getAllByText("Según el rol (por defecto)")).toHaveLength(2);
    expect(screen.getByText(/Personalizada/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Volver al default: Beto Gómez" })).toBeInTheDocument();
  });

  it("una invitación pendiente (sin nombre y sin es_yo) se muestra con su email", async () => {
    base();
    montarPantalla(<PaginaAvisos />);
    expect(await screen.findByText("nueva@negocio.com")).toBeInTheDocument();
    expect(screen.getByText(/invitación pendiente/)).toBeInTheDocument();
    expect(screen.getAllByText(/\(vos\)/)).toHaveLength(1);
    expect(screen.getByLabelText("Recibe avisos: nueva@negocio.com")).toBeInTheDocument();
  });

  it("WhatsApp está deshabilitado para todos y la pantalla lo aclara", async () => {
    base();
    montarPantalla(<PaginaAvisos />);
    expect(await screen.findByText(/WhatsApp todavía no está disponible/)).toBeInTheDocument();
    for (const nombre of ["Ana Pérez", "Beto Gómez", "nueva@negocio.com"]) {
      const whatsapp = screen.getByLabelText(`WhatsApp: ${nombre}`);
      expect(whatsapp).toBeDisabled();
      expect(whatsapp).not.toBeChecked();
    }
  });

  it("el canal email no se puede tocar si la persona no recibe avisos", async () => {
    base();
    montarPantalla(<PaginaAvisos />);
    expect(await screen.findByLabelText("Email: Beto Gómez")).toBeDisabled();
    expect(screen.getByLabelText("Email: Ana Pérez")).toBeEnabled();
  });

  it("si no se pueden leer los avisos muestra el error y permite reintentar", async () => {
    let intento = 0;
    base({ "GET /local/avisos": () => (++intento === 1 ? error(500, "x") : avisos(ANA)) });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Ana Pérez")).toBeInTheDocument();
  });
});

describe("guardar cambios", () => {
  it("sin cambios el botón de guardar está deshabilitado", async () => {
    base();
    montarPantalla(<PaginaAvisos />);
    expect(await screen.findByRole("button", { name: "Guardar cambios" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Descartar cambios" })).toBeDisabled();
  });

  it("envía solo las personas que cambiaron, en un único pedido, y muestra lo que devuelve el backend", async () => {
    const respuesta = avisos(
      { ...ANA, es_default: false, recibe_avisos: false, canal_email: false, recibe_por_email: false },
      { ...BETO, recibe_avisos: true, canal_email: true, recibe_por_email: true },
      PENDIENTE,
    );
    const backend = base({ "PUT /local/avisos": () => respuesta });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    await u.click(screen.getByLabelText("Recibe avisos: Beto Gómez"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(cuerpoDelPut(backend)).toEqual({
      cambios: [
        { staff_id: ANA.id, recibe_avisos: false, canal_email: false },
        { staff_id: BETO.id, recibe_avisos: true, canal_email: true },
      ],
    });
    // Se ve el estado nuevo: Ana pasó a personalizada, Beto recibe, y no hay cambios pendientes.
    expect(screen.getByLabelText("Recibe avisos: Ana Pérez")).not.toBeChecked();
    expect(screen.getByLabelText("Recibe avisos: Beto Gómez")).toBeChecked();
    expect(screen.getByRole("button", { name: "Volver al default: Ana Pérez" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
  });

  it("regresión: activar a quien el backend devuelve con recibe_avisos y canal_email en false envía los dos en true", async () => {
    const backend = base({
      "PUT /local/avisos": () =>
        avisos(ANA, BETO, { ...PENDIENTE, es_default: false, recibe_avisos: true, canal_email: true, recibe_por_email: true }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    const recibe = await screen.findByLabelText("Recibe avisos: nueva@negocio.com");
    expect(recibe).not.toBeChecked();
    expect(screen.getByLabelText("Email: nueva@negocio.com")).not.toBeChecked();
    await u.click(recibe);
    expect(screen.getByLabelText("Email: nueva@negocio.com")).toBeChecked();
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(cuerpoDelPut(backend)).toEqual({ cambios: [{ staff_id: PENDIENTE.id, recibe_avisos: true, canal_email: true }] });
    expect(screen.getByLabelText("Recibe avisos: nueva@negocio.com")).toBeChecked();
    expect(screen.getByLabelText("Email: nueva@negocio.com")).toBeChecked();
  });

  // Todo cuerpo enviado deja a la persona en un estado con sentido: si recibe, tiene email; si no recibe, no tiene canal.
  it.each([
    ["no recibe y sin canal", false, false, "recibe", true, true],
    ["no recibe pero con email guardado", false, true, "recibe", true, true],
    ["recibe sin canal (fila anterior)", true, false, "recibe", false, false],
    ["recibe por email", true, true, "recibe", false, false],
    ["recibe sin canal (fila anterior)", true, false, "email", true, true],
    ["recibe por email", true, true, "email", false, false],
  ])("desde %s (%s/%s), tocar %s deja el cuerpo coherente", async (_n, recibeInicial, emailInicial, control, recibeFinal, emailFinal) => {
    const persona = { ...BETO, recibe_avisos: recibeInicial, canal_email: emailInicial, recibe_por_email: recibeInicial && emailInicial };
    const backend = base({ "PUT /local/avisos": () => avisos(ANA, persona, PENDIENTE) }, avisos(ANA, persona, PENDIENTE));
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    const etiqueta = control === "recibe" ? "Recibe avisos: Beto Gómez" : "Email: Beto Gómez";
    await u.click(await screen.findByLabelText(etiqueta));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    const enviado = cuerpoDelPut(backend) as { cambios: { staff_id: string; recibe_avisos: boolean; canal_email: boolean }[] };
    expect(enviado.cambios).toEqual([{ staff_id: BETO.id, recibe_avisos: recibeFinal, canal_email: emailFinal }]);
  });

  it("quitar el email de quien recibe lo deja sin recibir, y no se puede tocar el email de quien no recibe", async () => {
    base();
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Email: Ana Pérez"));
    expect(screen.getByLabelText("Recibe avisos: Ana Pérez")).not.toBeChecked();
    expect(screen.getByLabelText("Email: Ana Pérez")).toBeDisabled();
  });

  it("volver a poner lo original antes de guardar no genera un cambio", async () => {
    const backend = base();
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    const casilla = await screen.findByLabelText("Recibe avisos: Ana Pérez");
    await u.click(casilla);
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeEnabled();
    await u.click(casilla);
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
    expect(backend.cuantas("PUT /local/avisos")).toBe(0);
  });

  it("descartar deja todo como estaba y no llama al backend", async () => {
    const backend = base();
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Beto Gómez"));
    expect(screen.getByLabelText("Recibe avisos: Beto Gómez")).toBeChecked();
    await u.click(screen.getByRole("button", { name: "Descartar cambios" }));
    expect(screen.getByLabelText("Recibe avisos: Beto Gómez")).not.toBeChecked();
    expect(backend.cuantas("PUT /local/avisos")).toBe(0);
  });

  it.each([
    ["cambio_invalido", 422, /Alguno de los cambios no es válido/],
    ["staff_repetido", 422, /aparece más de una vez/],
    ["staff_inexistente", 422, /ya no pertenece al negocio/],
    ["canal_no_disponible", 422, /WhatsApp todavía no está disponible\.$/],
    ["staff_inactivo", 409, /está desactivada/],
    ["sin_destinatario_de_avisos", 409, /al menos una persona que reciba los avisos por email/],
    ["conflicto_de_concurrencia", 409, /Otro cambio se cruzó con este/],
    ["permiso_denegado", 403, /no tiene permiso para configurar los avisos/],
    ["otro_codigo", 403, /no tiene permiso para configurar los avisos/],
    ["otro_codigo", 500, /Probá de nuevo\.$/],
    ["error_desconocido", 502, /Probá de nuevo\.$/],
  ])("guardar: el error %s (%i) se informa con su propio mensaje, una sola vez", async (codigo, status, mensaje) => {
    const backend = base({ "PUT /local/avisos": () => error(status, codigo) });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/^No se guardaron los cambios de avisos\./);
    expect(alerta).toHaveTextContent(mensaje);
    expect(alerta).not.toHaveTextContent(TEXTO_DEL_BACKEND);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(backend.cuantas("PUT /local/avisos")).toBe(1);
    // No se da por guardado: el cambio sigue pendiente y se puede reintentar.
    expect(screen.queryByText("Cambios guardados.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Recibe avisos: Ana Pérez")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeEnabled();
  });

  it("guardar: un 422 en el formato estándar de FastAPI usa el mensaje propio", async () => {
    base({
      "PUT /local/avisos": () => ({
        status: 422,
        cuerpo: { detail: [{ loc: ["body", "cambios"], msg: `Value error, ${TEXTO_DEL_BACKEND}`, type: "value_error" }] },
      }),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/El servidor no aceptó el pedido/);
    expect(alerta).not.toHaveTextContent(TEXTO_DEL_BACKEND);
  });

  it("guardar: sin conexión muestra el aviso de red", async () => {
    base({
      "PUT /local/avisos": () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudo conectar con el servidor/);
  });

  it("un error se borra al volver a intentar y al tener éxito no queda ninguno", async () => {
    let intento = 0;
    base({
      "PUT /local/avisos": () => (++intento === 1 ? error(409, "conflicto_de_concurrencia") : avisos({ ...ANA, es_default: false, recibe_avisos: false, canal_email: false, recibe_por_email: false }, BETO, PENDIENTE)),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("con el guardado en vuelo se deshabilitan los controles y un segundo clic no repite el pedido", async () => {
    const { manejador, soltar } = retenida(avisos({ ...ANA, es_default: false, recibe_avisos: false, canal_email: false, recibe_por_email: false }, BETO, PENDIENTE));
    const backend = base({ "PUT /local/avisos": manejador });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Descartar cambios" })).toBeDisabled();
    expect(screen.getByLabelText("Recibe avisos: Beto Gómez")).toBeDisabled();
    soltar();
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.cuantas("PUT /local/avisos")).toBe(1);
    expect(screen.getByLabelText("Recibe avisos: Beto Gómez")).toBeEnabled();
  });
});

describe("volver al default", () => {
  it("envía por_defecto para esa persona y la pantalla muestra que volvió al default", async () => {
    const backend = base({ "PUT /local/avisos": () => avisos(ANA, { ...BETO, es_default: true }, PENDIENTE) });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByRole("button", { name: "Volver al default: Beto Gómez" }));
    expect(await screen.findByText("Beto Gómez volvió a la configuración por defecto.")).toBeInTheDocument();
    expect(cuerpoDelPut(backend)).toEqual({ cambios: [{ staff_id: BETO.id, por_defecto: true }] });
    expect(screen.queryByRole("button", { name: /Volver al default/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("Según el rol (por defecto)")).toHaveLength(3);
  });

  it("no se puede con cambios sin guardar y la pantalla lo explica", async () => {
    base();
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    expect(screen.getByRole("button", { name: "Volver al default: Beto Gómez" })).toBeDisabled();
    expect(screen.getByText(/Tenés cambios sin guardar/)).toBeInTheDocument();
  });

  it.each([
    ["sin_destinatario_de_avisos", 409, /al menos una persona que reciba los avisos por email/],
    ["staff_inactivo", 409, /está desactivada/],
    ["conflicto_de_concurrencia", 409, /Otro cambio se cruzó con este/],
    ["permiso_denegado", 403, /no tiene permiso para configurar los avisos/],
    ["otro_codigo", 500, /Probá de nuevo\.$/],
  ])("el error %s (%i) se informa con el texto propio de esta acción, una sola vez", async (codigo, status, mensaje) => {
    base({ "PUT /local/avisos": () => error(status, codigo) });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByRole("button", { name: "Volver al default: Beto Gómez" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/^No se pudo volver a esa persona a la configuración por defecto\./);
    expect(alerta).not.toHaveTextContent(/No se guardaron los cambios/);
    expect(alerta).toHaveTextContent(mensaje);
    expect(alerta).not.toHaveTextContent(TEXTO_DEL_BACKEND);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Volver al default: Beto Gómez" })).toBeEnabled();
  });

  it("sin conexión muestra el aviso de red", async () => {
    base({
      "PUT /local/avisos": () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByRole("button", { name: "Volver al default: Beto Gómez" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudo conectar con el servidor/);
  });

  it("en vuelo deshabilita los controles y un segundo clic no repite el pedido", async () => {
    const { manejador, soltar } = retenida(avisos(ANA, { ...BETO, es_default: true }, PENDIENTE));
    const backend = base({ "PUT /local/avisos": manejador });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    const boton = await screen.findByRole("button", { name: "Volver al default: Beto Gómez" });
    await u.click(boton);
    await u.click(boton);
    expect(boton).toBeDisabled();
    expect(screen.getByLabelText("Recibe avisos: Ana Pérez")).toBeDisabled();
    soltar();
    expect(await screen.findByText(/volvió a la configuración por defecto/)).toBeInTheDocument();
    expect(backend.cuantas("PUT /local/avisos")).toBe(1);
  });
});

describe("permisos", () => {
  it("quien tiene exactamente manage_staff (sin ser owner) la ve y puede guardar", async () => {
    const backend = base({
      "GET /auth/sesion": [SOLO_EQUIPO],
      "PUT /local/avisos": () => avisos({ ...ANA, es_default: false, recibe_avisos: false, canal_email: false, recibe_por_email: false }, BETO, PENDIENTE),
    });
    const u = userEvent.setup();
    montarPantalla(<PaginaAvisos />);
    await u.click(await screen.findByLabelText("Recibe avisos: Ana Pérez"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.cuantas("PUT /local/avisos")).toBe(1);
  });

  it("quien tiene otros permisos pero no manage_staff no ve la pantalla ni se piden los datos", async () => {
    const backend = base({ "GET /auth/sesion": [OTROS_PERMISOS] });
    montarPantalla(<PaginaAvisos />);
    expect(await screen.findByText(/No tenés permiso para configurar los avisos del negocio/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guardar cambios" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Recibe avisos/)).not.toBeInTheDocument();
    expect(backend.cuantas("GET /local/avisos")).toBe(0);
  });

  it("el enlace de la sección solo aparece con manage_staff", () => {
    const sesion = (permisos: string[], isOwner = false) => ({ userId: "u", tenantId: "t", isOwner, aal: "aal1" as const, permisos });
    const etiquetas = (s: ReturnType<typeof sesion>) => seccionesVisibles(SECCIONES, s).map((x) => x.etiqueta);
    expect(etiquetas(sesion(["manage_staff"]))).toContain("Avisos");
    expect(etiquetas(sesion([], true))).toContain("Avisos");
    expect(etiquetas(sesion(["manage_schedule", "view_bookings"]))).not.toContain("Avisos");
    expect(etiquetas(sesion([]))).not.toContain("Avisos");
  });
});
