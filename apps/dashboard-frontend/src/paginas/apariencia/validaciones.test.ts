import { describe, expect, it } from "vitest";
import { colorDeTextoSobre } from "./VistaPrevia";
import { errorDeArchivoLogo, errorDeColor, errorDePlazo, TAMANO_MAXIMO_LOGO } from "./validaciones";

const archivo = (name: string, type: string, size = 100) => ({ name, type, size });

describe("errorDeArchivoLogo", () => {
  it.each([
    ["logo.png", "image/png"],
    ["logo.jpg", "image/jpeg"],
    ["logo.jpeg", "image/jpeg"],
    ["logo.webp", "image/webp"],
    ["LOGO.PNG", "image/png"],
    ["mi.logo.final.webp", "image/webp"],
  ])("acepta %s (%s)", (name, type) => {
    expect(errorDeArchivoLogo(archivo(name, type))).toBeNull();
  });

  it.each([
    ["logo.svg", "image/svg+xml"],
    ["logo.svg", "image/png"],
    ["logo.png", "image/svg+xml"],
    ["logo.gif", "image/gif"],
    ["logo.png", "image/gif"],
    ["logo.gif", "image/png"],
    ["logo.png", ""],
    ["logo", "image/png"],
    ["logo.pdf", "application/pdf"],
    ["logo.png.exe", "image/png"],
  ])("rechaza %s (%s) por tipo", (name, type) => {
    expect(errorDeArchivoLogo(archivo(name, type))).toMatch(/PNG, JPG o WebP/);
  });

  it("acepta exactamente 1 MB y rechaza un byte más", () => {
    expect(TAMANO_MAXIMO_LOGO).toBe(1_048_576);
    expect(errorDeArchivoLogo(archivo("a.png", "image/png", 1_048_576))).toBeNull();
    expect(errorDeArchivoLogo(archivo("a.png", "image/png", 1_048_577))).toMatch(/más de 1 MB/);
  });

  it("rechaza un archivo vacío", () => {
    expect(errorDeArchivoLogo(archivo("a.png", "image/png", 0))).toMatch(/vacío/);
  });
});

describe("errorDeColor", () => {
  it.each(["#1F6FEB", "#abcdef", "#000000", "#FFFFFF", "", "   ", " #123456 "])("acepta %j", (valor) => {
    expect(errorDeColor(valor)).toBeNull();
  });

  it.each(["#FFF", "123456", "#12345", "#1234567", "#GGGGGG", "rojo", "#12 456", "rgb(1,2,3)", "#12345\n"])(
    "rechaza %j",
    (valor) => {
      expect(errorDeColor(valor)).toMatch(/#RRGGBB/);
    },
  );
});

describe("errorDePlazo", () => {
  it.each(["0", "1", "24", "720", "", "  ", " 48 ", "007"])("acepta %j", (valor) => {
    expect(errorDePlazo(valor)).toBeNull();
  });

  it.each(["721", "1000", "-1", "-0", "2.5", "2,5", "1e2", "+5", "abc", "12 horas", "0x10"])("rechaza %j", (valor) => {
    expect(errorDePlazo(valor)).not.toBeNull();
  });

  it("el mensaje de rango nombra los dos extremos", () => {
    expect(errorDePlazo("721")).toMatch(/entre 0 y 720/);
  });
});

describe("colorDeTextoSobre", () => {
  it("usa texto claro sobre fondos oscuros y oscuro sobre claros", () => {
    expect(colorDeTextoSobre("#000000")).toBe("#ffffff");
    expect(colorDeTextoSobre("#ffffff")).toBe("#1f2933");
  });
});
