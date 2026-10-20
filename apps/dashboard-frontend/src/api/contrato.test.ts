import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { CATALOGO_PERMISOS } from "../paginas/staff/catalogoPermisos";
import { CONTRATO, PERMISOS, SALIDA, SALIDA_PERMISOS, estaAlDia, generarTipos, permisosAlDia } from "../../scripts/tipos-api.mjs";

const CARPETA = mkdtempSync(join(tmpdir(), "contrato-api-"));
afterAll(() => rmSync(CARPETA, { recursive: true, force: true }));

describe("tipos generados del contrato", () => {
  it("el archivo versionado coincide con el contrato (si no: npm run api:generar)", async () => {
    expect(await estaAlDia()).toBe(true);
  });

  it("un archivo de tipos distinto del que sale del contrato se detecta como desactualizado", async () => {
    const copia = join(CARPETA, "contrato.ts");
    writeFileSync(copia, readFileSync(SALIDA, "utf8").replace("price: number;", "precio: number;"));
    expect(await estaAlDia(CONTRATO, copia)).toBe(false);
  });

  /** Compila (con el `tsc` del proyecto) un uso de `ServicioOut.price` contra los tipos de un contrato dado. */
  async function compilaConContrato(contrato: unknown): Promise<boolean> {
    const dir = mkdtempSync(join(CARPETA, "caso-"));
    const rutaContrato = join(dir, "contrato.json");
    writeFileSync(rutaContrato, JSON.stringify(contrato));
    writeFileSync(join(dir, "contrato.ts"), await generarTipos(rutaContrato));
    writeFileSync(
      join(dir, "uso.ts"),
      'import type { components } from "./contrato";\n' +
        'export const precio = (s: components["schemas"]["ServicioOut"]): number => s.price;\n',
    );
    const tsc = resolve(__dirname, "../../node_modules/typescript/bin/tsc");
    try {
      execFileSync(process.execPath, [tsc, "--noEmit", "--strict", "--skipLibCheck", join(dir, "uso.ts")], {
        stdio: "pipe",
      });
      return true;
    } catch {
      return false;
    }
  }

  it("si un campo se renombra en el contrato, el código que lo usa deja de compilar", async () => {
    const contrato = JSON.parse(readFileSync(CONTRATO, "utf8"));
    expect(await compilaConContrato(contrato)).toBe(true);

    const servicio = contrato.components.schemas.ServicioOut;
    servicio.properties.precio = servicio.properties.price;
    delete servicio.properties.price;
    servicio.required = servicio.required.map((c: string) => (c === "price" ? "precio" : c));
    expect(await compilaConContrato(contrato)).toBe(false);
  }, 120_000);
});

describe("tipo Permiso generado del catálogo del backend", () => {
  it("el archivo versionado coincide con contratos/permisos.json (si no: npm run api:generar)", () => {
    expect(permisosAlDia()).toBe(true);
  });

  it("un archivo distinto del que sale del catálogo se detecta como desactualizado", () => {
    const copia = join(CARPETA, "permisos.ts");
    writeFileSync(copia, readFileSync(SALIDA_PERMISOS, "utf8").replace('"view_bookings"', '"ver_turnos"'));
    expect(permisosAlDia(PERMISOS, copia)).toBe(false);
  });

  it("CATALOGO_PERMISOS cubre exactamente las claves de contratos/permisos.json", () => {
    const { claves } = JSON.parse(readFileSync(PERMISOS, "utf8")) as { claves: string[] };
    expect(CATALOGO_PERMISOS.map((p) => p.clave).sort()).toEqual([...claves].sort());
  });

  function compilaUsoDe(clave: string): boolean {
    const dir = mkdtempSync(join(CARPETA, "permiso-"));
    writeFileSync(join(dir, "permisos.ts"), readFileSync(SALIDA_PERMISOS, "utf8"));
    writeFileSync(
      join(dir, "uso.ts"),
      `import type { Permiso } from "./permisos";\nexport const p: Permiso = ${JSON.stringify(clave)};\n`,
    );
    const tsc = resolve(__dirname, "../../node_modules/typescript/bin/tsc");
    try {
      execFileSync(process.execPath, [tsc, "--noEmit", "--strict", "--skipLibCheck", join(dir, "uso.ts")], {
        stdio: "pipe",
      });
      return true;
    } catch {
      return false;
    }
  }

  it("una clave existente compila y una inventada no", () => {
    expect(compilaUsoDe("view_bookings")).toBe(true);
    expect(compilaUsoDe("clave_inventada")).toBe(false);
  }, 120_000);
});
