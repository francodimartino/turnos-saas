import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SECCIONES } from "../../layout/secciones";
import { seccionesVisibles } from "../../layout/permisos";
import { instalarBackend, sesionDe, type ManejadorFalso, type RespuestaFalsa } from "../../test/falsoBackend";
import { montarPantalla } from "../../test/montar";
import { PaginaApariencia } from "./PaginaApariencia";

const OWNER = sesionDe({ is_owner: true, aal: "aal2" });
const SOLO_AGENDA = sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_schedule"] });
const OTROS_PERMISOS = sesionDe({ is_owner: false, aal: "aal1", permisos: ["manage_staff", "view_bookings"] });

const RUTA_LOGO = "logo-0123456789abcdef0123456789abcdef.png";
const SETTINGS = { color_principal: "#112233", color_secundario: "#445566", cancelacion_plazo_horas: 12 };
const CON_LOGO = { ...SETTINGS, logo_ruta: `t-1/${RUTA_LOGO}` };
const TEXTO_DEL_BACKEND = "TEXTO-DEL-BACKEND-QUE-NO-SE-MUESTRA";

const error = (status: number, codigo: string): RespuestaFalsa => ({
  status,
  cuerpo: { detail: { codigo, mensaje: TEXTO_DEL_BACKEND } },
});
const ok = (settings: object): RespuestaFalsa => ({ cuerpo: { settings } });

function base(extra: Record<string, ManejadorFalso | RespuestaFalsa[]> = {}, settings: object = SETTINGS) {
  return instalarBackend({
    "GET /auth/sesion": [OWNER],
    "GET /local/settings": [ok(settings)],
    ...extra,
  });
}

function imagen(nombre: string, tipo: string, bytes = 100) {
  return new File([new Uint8Array(bytes)], nombre, { type: tipo });
}

/** Un `userEvent` que no filtra por `accept`: lo que se prueba es la validación propia, no la del navegador. */
const usuario = () => userEvent.setup({ applyAccept: false });

/** Respuesta retenida: `soltar()` la libera. Sirve para observar el estado en vuelo. */
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

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:http://localhost/previa");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  // jsdom no trae estos métodos: se borran para no filtrar el reemplazo a otros archivos de test.
  Reflect.deleteProperty(URL, "createObjectURL");
  Reflect.deleteProperty(URL, "revokeObjectURL");
});

describe("logo: validación en el cliente", () => {
  it("el input solo acepta PNG, JPG y WebP", async () => {
    base();
    montarPantalla(<PaginaApariencia />);
    const input = await screen.findByLabelText("Archivo del logo");
    const acepta = input.getAttribute("accept") ?? "";
    for (const tipo of ["image/png", "image/jpeg", "image/webp", ".png", ".jpg", ".webp"]) expect(acepta).toContain(tipo);
    expect(acepta).not.toContain("svg");
  });

  it.each([
    ["logo.svg", "image/svg+xml"],
    ["logo.gif", "image/gif"],
    ["logo.png", "image/gif"],
    ["logo.gif", "image/png"],
    ["logo.pdf", "application/pdf"],
  ])("rechaza %s (%s) antes de enviar", async (nombre, tipo) => {
    const backend = base();
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen(nombre, tipo));
    expect(await screen.findByRole("alert")).toHaveTextContent(/PNG, JPG o WebP/);
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeDisabled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(backend.cuantas("POST /local/logo")).toBe(0);
  });

  it.each([
    ["logo.png", "image/png"],
    ["logo.jpg", "image/jpeg"],
    ["logo.jpeg", "image/jpeg"],
    ["logo.webp", "image/webp"],
  ])("acepta %s (%s) y habilita la subida", async (nombre, tipo) => {
    base();
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen(nombre, tipo));
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("acepta un archivo de exactamente 1 MB y lo envía con su tipo", async () => {
    const backend = base({ "POST /local/logo": () => ok(CON_LOGO) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen("logo.png", "image/png", 1_048_576));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    expect(await screen.findByText("Logo guardado.")).toBeInTheDocument();
    const llamada = backend.llamadas.find((l) => l.clave === "POST /local/logo");
    expect(llamada?.tipoContenido).toBe("image/png");
    expect(llamada?.cuerpo).toBeInstanceOf(Blob);
    expect((llamada?.cuerpo as Blob).size).toBe(1_048_576);
  });

  it("rechaza un archivo de 1 MB y un byte, sin llamar al backend", async () => {
    const backend = base();
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen("logo.png", "image/png", 1_048_577));
    expect(await screen.findByRole("alert")).toHaveTextContent(/más de 1 MB/);
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeDisabled();
    expect(backend.cuantas("POST /local/logo")).toBe(0);
  });

  it("un archivo inválido elegido después de uno válido deja la vista previa sin imagen cuando no hay logo previo", async () => {
    let n = 0;
    URL.createObjectURL = vi.fn(() => `blob:previa-${++n}`);
    base();
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const input = await screen.findByLabelText("Archivo del logo");
    await u.upload(input, imagen("logo.png", "image/png"));
    expect(screen.getByAltText("Logo del negocio")).toBeInTheDocument();
    expect(screen.getByText("El logo elegido todavía no se subió.")).toBeInTheDocument();
    await u.upload(input, imagen("logo.svg", "image/svg+xml"));
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeDisabled();
    expect(screen.queryByAltText("Logo del negocio")).not.toBeInTheDocument();
    expect(screen.getByText("Todavía no hay un logo.")).toBeInTheDocument();
    expect(screen.queryByText("El logo elegido todavía no se subió.")).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:previa-1");
  });

  it.each([
    ["un SVG", () => imagen("logo.svg", "image/svg+xml")],
    ["un archivo de más de 1 MB", () => imagen("grande.png", "image/png", 1_048_577)],
  ])("%s elegido después de una subida de la sesión devuelve la vista previa a la imagen subida", async (_n, invalido) => {
    let n = 0;
    URL.createObjectURL = vi.fn(() => `blob:previa-${++n}`);
    base({ "POST /local/logo": () => ok(CON_LOGO) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const input = await screen.findByLabelText("Archivo del logo");
    await u.upload(input, imagen("a.png", "image/png"));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    expect(await screen.findByText("Logo guardado.")).toBeInTheDocument();
    expect(screen.getByAltText("Logo del negocio")).toHaveAttribute("src", "blob:previa-1");
    await u.upload(screen.getByLabelText("Archivo del logo"), imagen("b.png", "image/png"));
    expect(screen.getByAltText("Logo del negocio")).toHaveAttribute("src", "blob:previa-2");
    expect(screen.getByText("El logo elegido todavía no se subió.")).toBeInTheDocument();
    await u.upload(screen.getByLabelText("Archivo del logo"), invalido());
    expect(screen.getByAltText("Logo del negocio")).toHaveAttribute("src", "blob:previa-1");
    expect(screen.queryByText("El logo elegido todavía no se subió.")).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:previa-2");
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:previa-1");
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeDisabled();
  });
});

describe("logo: subir y quitar", () => {
  it("al subir muestra la vista previa, guarda y deja el estado nuevo (con botón para quitar)", async () => {
    const backend = base({ "POST /local/logo": () => ok(CON_LOGO) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    expect(await screen.findByText("Todavía no hay un logo.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quitar logo" })).not.toBeInTheDocument();
    await u.upload(screen.getByLabelText("Archivo del logo"), imagen("logo.webp", "image/webp"));
    expect(screen.getByAltText("Logo del negocio")).toHaveAttribute("src", "blob:http://localhost/previa");
    expect(screen.getByText("El logo elegido todavía no se subió.")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    expect(await screen.findByText("Logo guardado.")).toBeInTheDocument();
    expect(backend.cuantas("POST /local/logo")).toBe(1);
    expect(screen.getByRole("button", { name: "Quitar logo" })).toBeInTheDocument();
    expect(screen.getByAltText("Logo del negocio")).toBeInTheDocument();
    expect(screen.queryByText("El logo elegido todavía no se subió.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeDisabled();
  });

  it("con un logo guardado avisa que existe y permite quitarlo; tras quitarlo la pantalla lo refleja", async () => {
    const backend = base({ "DELETE /local/logo": () => ok(SETTINGS) }, CON_LOGO);
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    expect(await screen.findByText(/Hay un logo guardado/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Quitar logo" }));
    expect(await screen.findByText("Logo quitado.")).toBeInTheDocument();
    expect(backend.cuantas("DELETE /local/logo")).toBe(1);
    expect(screen.queryByRole("button", { name: "Quitar logo" })).not.toBeInTheDocument();
    expect(screen.getByText("Todavía no hay un logo.")).toBeInTheDocument();
    expect(screen.queryByAltText("Logo del negocio")).not.toBeInTheDocument();
  });

  it("quitar un logo recién subido borra también la imagen de la vista previa", async () => {
    base({ "POST /local/logo": () => ok(CON_LOGO), "DELETE /local/logo": () => ok(SETTINGS) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen("logo.png", "image/png"));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    await u.click(await screen.findByRole("button", { name: "Quitar logo" }));
    expect(await screen.findByText("Logo quitado.")).toBeInTheDocument();
    expect(screen.queryByAltText("Logo del negocio")).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:http://localhost/previa");
  });

  it.each([
    ["logo_tipo_invalido", 422, /no reconoció la imagen/],
    ["logo_invalido", 422, /no pudo procesar la imagen/],
    ["logo_demasiado_grande", 413, /por pesar más de 1 MB/],
    ["cuerpo_demasiado_grande", 413, /por pesar más de 1 MB/],
    ["almacenamiento_no_disponible", 503, /almacenamiento de imágenes no responde/],
    ["settings_demasiado_grandes", 422, /No hay lugar para guardar el logo/],
    ["no_encontrado", 404, /No se encontró el negocio al subir el logo/],
    ["permiso_denegado", 403, /no tiene permiso para cambiar el logo/],
    ["otro_codigo", 403, /no tiene permiso para cambiar el logo/],
    ["otro_codigo", 500, /No se pudo subir el logo\. Probá de nuevo\./],
    ["error_desconocido", 502, /No se pudo subir el logo\. Probá de nuevo\./],
  ])("subir: el error %s (%i) se informa con su propio mensaje, una sola vez", async (codigo, status, mensaje) => {
    const backend = base({ "POST /local/logo": () => error(status, codigo) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen("logo.png", "image/png"));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(mensaje);
    expect(alerta).not.toHaveTextContent(TEXTO_DEL_BACKEND);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(backend.cuantas("POST /local/logo")).toBe(1);
    // El archivo sigue elegido: se puede reintentar.
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeEnabled();
  });

  it("subir: sin conexión muestra el aviso de red y no el nombre del archivo", async () => {
    base({
      "POST /local/logo": () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen("secreto-del-cliente.png", "image/png"));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/No se pudo conectar con el servidor/);
    expect(alerta).not.toHaveTextContent("secreto-del-cliente");
  });

  it("subir: un fallo no cambia el logo guardado y un reintento exitoso lo guarda", async () => {
    let intento = 0;
    base({ "POST /local/logo": () => (++intento === 1 ? error(503, "almacenamiento_no_disponible") : ok(CON_LOGO)) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen("logo.png", "image/png"));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quitar logo" })).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    expect(await screen.findByText("Logo guardado.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Quitar logo" })).toBeInTheDocument();
  });

  it.each([
    ["almacenamiento_no_disponible", 503, /el logo sigue puesto/],
    ["no_encontrado", 404, /No se encontró el negocio al quitar el logo/],
    ["permiso_denegado", 403, /no tiene permiso para quitar el logo/],
    ["otro_codigo", 403, /no tiene permiso para quitar el logo/],
    ["otro_codigo", 500, /No se pudo quitar el logo\. Probá de nuevo\./],
    ["error_desconocido", 502, /No se pudo quitar el logo\. Probá de nuevo\./],
  ])("quitar: el error %s (%i) se informa con su propio mensaje, una sola vez", async (codigo, status, mensaje) => {
    base({ "DELETE /local/logo": () => error(status, codigo) }, CON_LOGO);
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.click(await screen.findByRole("button", { name: "Quitar logo" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(mensaje);
    expect(alerta).not.toHaveTextContent(TEXTO_DEL_BACKEND);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    // El logo sigue guardado.
    expect(screen.getByRole("button", { name: "Quitar logo" })).toBeEnabled();
  });

  it("quitar: sin conexión muestra el aviso de red", async () => {
    base(
      {
        "DELETE /local/logo": () => {
          throw new TypeError("Failed to fetch");
        },
      },
      CON_LOGO,
    );
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.click(await screen.findByRole("button", { name: "Quitar logo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudo conectar con el servidor/);
  });

  it("con la subida en vuelo se deshabilitan los botones y el input, y un segundo clic no envía otra", async () => {
    const { manejador, soltar } = retenida(ok(CON_LOGO));
    const backend = base({ "POST /local/logo": manejador }, CON_LOGO);
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.upload(await screen.findByLabelText("Archivo del logo"), imagen("logo.png", "image/png"));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    await u.click(screen.getByRole("button", { name: "Subir logo" }));
    expect(screen.getByRole("button", { name: "Subir logo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Quitar logo" })).toBeDisabled();
    expect(screen.getByLabelText("Archivo del logo")).toBeDisabled();
    soltar();
    expect(await screen.findByText("Logo guardado.")).toBeInTheDocument();
    expect(backend.cuantas("POST /local/logo")).toBe(1);
    expect(screen.getByRole("button", { name: "Quitar logo" })).toBeEnabled();
    expect(screen.getByLabelText("Archivo del logo")).toBeEnabled();
  });

  it("con quitar en vuelo se deshabilitan los botones y un segundo clic no envía otra petición", async () => {
    const { manejador, soltar } = retenida(ok(SETTINGS));
    const backend = base({ "DELETE /local/logo": manejador }, CON_LOGO);
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const quitar = await screen.findByRole("button", { name: "Quitar logo" });
    await u.click(quitar);
    await u.click(quitar);
    expect(quitar).toBeDisabled();
    expect(screen.getByLabelText("Archivo del logo")).toBeDisabled();
    soltar();
    expect(await screen.findByText("Logo quitado.")).toBeInTheDocument();
    expect(backend.cuantas("DELETE /local/logo")).toBe(1);
  });
});

describe("colores y vista previa", () => {
  it("muestra los valores actuales y la vista previa los usa", async () => {
    base();
    montarPantalla(<PaginaApariencia />);
    expect(await screen.findByLabelText("Color principal")).toHaveValue("#112233");
    expect(screen.getByLabelText("Color secundario")).toHaveValue("#445566");
    expect(screen.getByTestId("previa-cabecera")).toHaveStyle({ backgroundColor: "#112233" });
    expect(screen.getByTestId("previa-boton")).toHaveStyle({ backgroundColor: "#445566" });
  });

  it("la vista previa sigue lo que se escribe, y con un color inválido vuelve al neutro", async () => {
    base();
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const principal = await screen.findByLabelText("Color principal");
    await u.clear(principal);
    await u.type(principal, "#FF0000");
    expect(screen.getByTestId("previa-cabecera")).toHaveStyle({ backgroundColor: "#ff0000" });
    await u.clear(principal);
    await u.type(principal, "#FF00");
    expect(screen.getByTestId("previa-cabecera")).toHaveStyle({ backgroundColor: "#e4e7eb" });
  });

  it("el selector de color y el campo de texto quedan sincronizados", async () => {
    base();
    montarPantalla(<PaginaApariencia />);
    const selector = await screen.findByLabelText("Elegir color secundario");
    expect(selector).toHaveValue("#445566");
    fireEvent.input(selector, { target: { value: "#00ff00" } });
    expect(screen.getByLabelText("Color secundario")).toHaveValue("#00ff00");
    expect(screen.getByTestId("previa-boton")).toHaveStyle({ backgroundColor: "#00ff00" });
  });

  it.each(["#1F6FEB", "#abcdef", "#000000"])("guarda el color válido %s y solo envía lo que cambió", async (color) => {
    const backend = base({ "PATCH /local/apariencia": () => ok({ ...SETTINGS, color_principal: color }) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const principal = await screen.findByLabelText("Color principal");
    await u.clear(principal);
    await u.type(principal, color);
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PATCH /local/apariencia")?.cuerpo).toEqual({ color_principal: color });
  });

  it.each(["#FFF", "123456", "#12345", "#GGGGGG", "rojo"])("el color %s se rechaza en el cliente, junto al campo", async (color) => {
    const backend = base();
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const secundario = await screen.findByLabelText("Color secundario");
    await u.clear(secundario);
    await u.type(secundario, color);
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(secundario).toHaveAccessibleDescription(/#RRGGBB/);
    expect(secundario).toBeInvalid();
    expect(backend.cuantas("PATCH /local/apariencia")).toBe(0);
  });

  it("vaciar un color lo borra (null)", async () => {
    const backend = base({ "PATCH /local/apariencia": () => ok({ ...SETTINGS, color_secundario: null }) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.clear(await screen.findByLabelText("Color secundario"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PATCH /local/apariencia")?.cuerpo).toEqual({ color_secundario: null });
    expect(screen.getByLabelText("Color secundario")).toHaveValue("");
  });
});

describe("plazo de cancelación", () => {
  it("muestra el plazo actual y, sin valor, indica que rige el de 24 horas", async () => {
    base({}, {});
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    expect(plazo).toHaveValue("");
    expect(plazo).toHaveAttribute("placeholder", "24");
    expect(plazo).toHaveAccessibleDescription(/Entre 0 y 720.*Vacío: 24 horas/);
  });

  it("muestra el plazo guardado", async () => {
    base();
    montarPantalla(<PaginaApariencia />);
    expect(await screen.findByLabelText("Plazo de cancelación (horas)")).toHaveValue("12");
  });

  it.each(["0", "1", "720", "48"])("el plazo %s se acepta y se envía como número", async (valor) => {
    const backend = base({ "PATCH /local/apariencia": () => ok({ ...SETTINGS, cancelacion_plazo_horas: Number(valor) }) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, valor);
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PATCH /local/apariencia")?.cuerpo).toEqual({
      cancelacion_plazo_horas: Number(valor),
    });
  });

  it.each(["721", "1000", "-1", "2.5", "2,5", "abc", "1e2", "+5"])("el plazo %s se rechaza en el cliente sin llamar al backend", async (valor) => {
    const backend = base();
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, valor);
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(plazo).toHaveAccessibleDescription(/entero|entre 0 y 720/);
    expect(plazo).toBeInvalid();
    expect(backend.cuantas("PATCH /local/apariencia")).toBe(0);
  });

  it("vaciar el plazo guardado vuelve al valor por defecto (null)", async () => {
    const backend = base({ "PATCH /local/apariencia": () => ok({ color_principal: "#112233", color_secundario: "#445566" }) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.clear(await screen.findByLabelText("Plazo de cancelación (horas)"));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.llamadas.find((l) => l.clave === "PATCH /local/apariencia")?.cuerpo).toEqual({ cancelacion_plazo_horas: null });
    expect(screen.getByLabelText("Plazo de cancelación (horas)")).toHaveValue("");
  });

  it("sin cambios no llama al backend; tras guardar, lo guardado es la nueva base de comparación", async () => {
    const backend = base({ "PATCH /local/apariencia": () => ok({ ...SETTINGS, cancelacion_plazo_horas: 6 }) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await u.click(await screen.findByRole("button", { name: "Guardar cambios" }));
    expect(screen.getByText("No hay cambios para guardar.")).toBeInTheDocument();
    expect(backend.cuantas("PATCH /local/apariencia")).toBe(0);
    const plazo = screen.getByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, "6");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(plazo).toHaveValue("6");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(screen.getByText("No hay cambios para guardar.")).toBeInTheDocument();
    expect(backend.cuantas("PATCH /local/apariencia")).toBe(1);
  });

  it.each([
    ["apariencia_invalida", 422, /rechazó los valores/],
    ["settings_demasiado_grandes", 422, /superan el tamaño permitido/],
    ["no_encontrado", 404, /No se encontró el negocio al guardar/],
    ["permiso_denegado", 403, /no tiene permiso para cambiar la apariencia/],
    ["otro_codigo", 403, /no tiene permiso para cambiar la apariencia/],
    ["otro_codigo", 500, /No se pudieron guardar los colores y el plazo\. Probá de nuevo\./],
    ["error_desconocido", 502, /No se pudieron guardar los colores y el plazo\. Probá de nuevo\./],
  ])("guardar: el error %s (%i) se informa con su propio mensaje, una sola vez", async (codigo, status, mensaje) => {
    const backend = base({ "PATCH /local/apariencia": () => error(status, codigo) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, "5");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(mensaje);
    expect(alerta).not.toHaveTextContent(TEXTO_DEL_BACKEND);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(backend.cuantas("PATCH /local/apariencia")).toBe(1);
    // No se muestra como guardado ni se pisa lo escrito.
    expect(screen.queryByText("Cambios guardados.")).not.toBeInTheDocument();
    expect(plazo).toHaveValue("5");
  });

  it("guardar: un 422 en el formato estándar de FastAPI (lista de errores) usa el mensaje propio, no el del servidor", async () => {
    base({
      "PATCH /local/apariencia": () => ({
        status: 422,
        cuerpo: { detail: [{ loc: ["body", "cancelacion_plazo_horas"], msg: `Value error, ${TEXTO_DEL_BACKEND}`, type: "value_error" }] },
      }),
    });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, "5");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent(/rechazó los valores/);
    expect(alerta).not.toHaveTextContent(TEXTO_DEL_BACKEND);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("guardar: sin conexión muestra el aviso de red", async () => {
    base({
      "PATCH /local/apariencia": () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, "5");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudo conectar con el servidor/);
  });

  it("con el guardado en vuelo se deshabilita el formulario y un segundo envío no repite la petición", async () => {
    const { manejador, soltar } = retenida(ok({ ...SETTINGS, cancelacion_plazo_horas: 5 }));
    const backend = base({ "PATCH /local/apariencia": manejador });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, "5");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
    expect(plazo).toBeDisabled();
    expect(screen.getByLabelText("Color principal")).toBeDisabled();
    soltar();
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.cuantas("PATCH /local/apariencia")).toBe(1);
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeEnabled();
  });
});

describe("permisos", () => {
  it("el owner ve la pantalla completa", async () => {
    base();
    montarPantalla(<PaginaApariencia />);
    expect(await screen.findByLabelText("Archivo del logo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guardar cambios" })).toBeInTheDocument();
  });

  it("quien tiene exactamente manage_schedule (sin ser owner) la ve y puede guardar", async () => {
    const backend = base({
      "GET /auth/sesion": [SOLO_AGENDA],
      "PATCH /local/apariencia": () => ok({ ...SETTINGS, cancelacion_plazo_horas: 3 }),
    });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    const plazo = await screen.findByLabelText("Plazo de cancelación (horas)");
    await u.clear(plazo);
    await u.type(plazo, "3");
    await u.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(backend.cuantas("PATCH /local/apariencia")).toBe(1);
  });

  it("quien tiene otros permisos pero no manage_schedule no ve los controles ni se piden los datos", async () => {
    const backend = base({ "GET /auth/sesion": [OTROS_PERMISOS] });
    montarPantalla(<PaginaApariencia />);
    expect(await screen.findByText(/No tenés permiso para cambiar la apariencia/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Archivo del logo")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guardar cambios" })).not.toBeInTheDocument();
    expect(backend.cuantas("GET /local/settings")).toBe(0);
  });

  it("el enlace de la sección solo aparece con manage_schedule", () => {
    const sesion = (permisos: string[], isOwner = false) => ({ userId: "u", tenantId: "t", isOwner, aal: "aal1" as const, permisos });
    const etiquetas = (s: ReturnType<typeof sesion>) => seccionesVisibles(SECCIONES, s).map((x) => x.etiqueta);
    expect(etiquetas(sesion(["manage_schedule"]))).toContain("Apariencia");
    expect(etiquetas(sesion([], true))).toContain("Apariencia");
    expect(etiquetas(sesion(["manage_staff", "view_bookings"]))).not.toContain("Apariencia");
    expect(etiquetas(sesion([]))).not.toContain("Apariencia");
  });
});

describe("carga", () => {
  it("si no se pueden leer los datos muestra el error y permite reintentar", async () => {
    let intento = 0;
    base({ "GET /local/settings": () => (++intento === 1 ? error(500, "x") : ok(SETTINGS)) });
    const u = usuario();
    montarPantalla(<PaginaApariencia />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    await u.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByLabelText("Color principal")).toHaveValue("#112233");
  });
});
