import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { CONTRATO, SALIDA, estaAlDia, generarTipos } from "../../scripts/tipos-api.mjs";

const CARPETA = mkdtempSync(join(tmpdir(), "contrato-api-"));
afterAll(() => rmSync(CARPETA, { recursive: true, force: true }));

describe("tipos generados del contrato", () => {
  it("el archivo versionado coincide con el contrato (si no: npm run api:generar)", async () => {
    expect(await estaAlDia()).toBe(true);
  });

  it("un archivo de tipos distinto del que sale del contrato se detecta como desactualizado", async () => {
    const copia = join(CARPETA, "contrato.ts");
    writeFileSync(copia, readFileSync(SALIDA, "utf8").replace("price: string;", "precio: string;"));
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
        'export const precio = (s: components["schemas"]["ServicioOut"]): string => s.price;\n',
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
