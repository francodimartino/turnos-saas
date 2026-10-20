import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** Los dos frontends comparten nombres de carpeta (`compartido/`, `paginas/`) y el widget de Turnstile. */

const SRC = import.meta.dirname;

describe("estructura de carpetas de src", () => {
  it("usa compartido/ y paginas/", () => {
    expect(existsSync(join(SRC, "compartido"))).toBe(true);
    expect(existsSync(join(SRC, "paginas"))).toBe(true);
  });

  it("no queda ninguna carpeta con los nombres viejos", () => {
    expect(existsSync(join(SRC, "shared"))).toBe(false);
    expect(existsSync(join(SRC, "features"))).toBe(false);
  });

  it("Turnstile vive en compartido/WidgetTurnstile.tsx", () => {
    expect(existsSync(join(SRC, "compartido", "WidgetTurnstile.tsx"))).toBe(true);
    expect(existsSync(join(SRC, "compartido", "Turnstile.tsx"))).toBe(false);
  });
});
