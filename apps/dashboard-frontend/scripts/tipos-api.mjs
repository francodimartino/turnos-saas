// Genera los tipos TypeScript de la API a partir del contrato OpenAPI del backend.
//
// Uso (desde el directorio del frontend):
//   node scripts/tipos-api.mjs             # reescribe los archivos de tipos versionados
//   node scripts/tipos-api.mjs --verificar # no escribe; código 1 si algún archivo no coincide con su contrato
//
// El contrato (`contratos/<backend>.openapi.json`) lo regenera el backend con `python scripts/contrato_openapi.py`.
// El catálogo de permisos (`contratos/permisos.json`) lo regenera con `python scripts/contrato_permisos.py`; de ahí
// sale la unión `Permiso` (`src/api/generado/permisos.ts`).
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const AQUI = dirname(fileURLToPath(import.meta.url));

/** Contrato de este frontend y archivo de tipos que se versiona. Es lo único que cambia entre frontends. */
export const CONTRATO = resolve(AQUI, "../../../contratos/dashboard-backend.openapi.json");
export const SALIDA = resolve(AQUI, "../src/api/generado/contrato.ts");

const ENCABEZADO = `/**
 * Generado desde contratos/ con openapi-typescript (npm run api:generar). No editar a mano.
 */

`;

/** Texto de los tipos de un contrato (JSON en `ruta`), con saltos de línea `\n`. */
export async function generarTipos(ruta = CONTRATO) {
  const ast = await openapiTS(pathToFileURL(ruta));
  return ENCABEZADO + astToString(ast).replace(/\r\n/g, "\n");
}

/** Catálogo de permisos del backend y archivo con la unión `Permiso` que se versiona. */
export const PERMISOS = resolve(AQUI, "../../../contratos/permisos.json");
export const SALIDA_PERMISOS = resolve(AQUI, "../src/api/generado/permisos.ts");

/** Texto del archivo con la unión `Permiso` (claves ordenadas del catálogo JSON en `ruta`). */
export function generarPermisos(ruta = PERMISOS) {
  const { claves } = JSON.parse(readFileSync(ruta, "utf8"));
  const union = claves.map((c) => `  | ${JSON.stringify(c)}`).join("\n");
  const encabezado =
    "/**\n * Generado desde contratos/permisos.json (npm run api:generar). No editar a mano.\n */\n\n";
  const doc = "/** permission_key válidos: el catálogo del backend. Una clave inexistente no compila. */";
  return `${encabezado}${doc}\nexport type Permiso =\n${union};\n`;
}

/** `true` si el archivo de la unión `Permiso` existe y es igual al que sale del catálogo. */
export function permisosAlDia(catalogo = PERMISOS, salida = SALIDA_PERMISOS) {
  if (!existsSync(salida)) return false;
  return readFileSync(salida, "utf8").replace(/\r\n/g, "\n") === generarPermisos(catalogo);
}

/** `true` si el archivo de tipos existe y es igual al que sale del contrato. */
export async function estaAlDia(contrato = CONTRATO, salida = SALIDA) {
  if (!existsSync(salida)) return false;
  return readFileSync(salida, "utf8").replace(/\r\n/g, "\n") === (await generarTipos(contrato));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  if (process.argv.includes("--verificar")) {
    if ((await estaAlDia()) && permisosAlDia()) {
      console.log("Los tipos de la API y de los permisos coinciden con los contratos.");
    } else {
      console.error("Los tipos de la API o de los permisos no coinciden con los contratos. Regeneralos con: npm run api:generar");
      process.exit(1);
    }
  } else {
    writeFileSync(SALIDA, await generarTipos(), { encoding: "utf8" });
    writeFileSync(SALIDA_PERMISOS, generarPermisos(), { encoding: "utf8" });
    console.log(`Tipos escritos en ${SALIDA} y ${SALIDA_PERMISOS}`);
  }
}
