// Genera los tipos TypeScript de la API a partir del contrato OpenAPI del backend.
//
// Uso (desde el directorio del frontend):
//   node scripts/tipos-api.mjs             # reescribe el archivo de tipos versionado
//   node scripts/tipos-api.mjs --verificar # no escribe; código 1 si el archivo no coincide con el contrato
//
// El contrato (`contratos/<backend>.openapi.json`) lo regenera el backend con `python scripts/contrato_openapi.py`.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";
import ts from "typescript";

const AQUI = dirname(fileURLToPath(import.meta.url));

/** Contrato de este frontend y archivo de tipos que se versiona. Es lo único que cambia entre frontends. */
export const CONTRATO = resolve(AQUI, "../../../contratos/admin-backend.openapi.json");
export const SALIDA = resolve(AQUI, "../src/api/generado/contrato.ts");

const UNKNOWN = ts.factory.createKeywordTypeNode(ts.SyntaxKind.UnknownKeyword);

const ENCABEZADO = `/**
 * Generado desde contratos/ con openapi-typescript (npm run api:generar). No editar a mano.
 */

`;

/** Texto de los tipos de un contrato (JSON en `ruta`), con saltos de línea `\n`. */
export async function generarTipos(ruta = CONTRATO) {
  const ast = await openapiTS(pathToFileURL(ruta), {
    // `ValorJson` es recursivo (JSON arbitrario) y openapi-typescript lo emite como un alias circular que tsc rechaza
    // (TS2502). Como el panel no inspecciona esos valores (ajustes de la plataforma), se publica como `unknown`.
    transform: (_esquema, { path }) => (path === "#/components/schemas/ValorJson" ? UNKNOWN : undefined),
  });
  return ENCABEZADO + astToString(ast).replace(/\r\n/g, "\n");
}

/** `true` si el archivo de tipos existe y es igual al que sale del contrato. */
export async function estaAlDia(contrato = CONTRATO, salida = SALIDA) {
  if (!existsSync(salida)) return false;
  return readFileSync(salida, "utf8").replace(/\r\n/g, "\n") === (await generarTipos(contrato));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  if (process.argv.includes("--verificar")) {
    if (await estaAlDia()) {
      console.log("Los tipos de la API coinciden con el contrato.");
    } else {
      console.error("Los tipos de la API no coinciden con el contrato. Regeneralos con: npm run api:generar");
      process.exit(1);
    }
  } else {
    writeFileSync(SALIDA, await generarTipos(), { encoding: "utf8" });
    console.log(`Tipos escritos en ${SALIDA}`);
  }
}
