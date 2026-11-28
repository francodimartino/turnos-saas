import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** Los frontends comparten nombres de carpeta: `compartido/` (lo usado por 2+ páginas) y `paginas/` (una por pantalla). */

const SRC = import.meta.dirname;

describe("estructura de carpetas de src", () => {
  it("usa compartido/ y paginas/", () => {
    expect(existsSync(join(SRC, "compartido"))).toBe(true);
    expect(existsSync(join(SRC, "paginas"))).toBe(true);
  });

  it("no usa shared/ ni features/ en lugar de compartido/ y paginas/", () => {
    expect(existsSync(join(SRC, "shared"))).toBe(false);
    expect(existsSync(join(SRC, "features"))).toBe(false);
  });
});
