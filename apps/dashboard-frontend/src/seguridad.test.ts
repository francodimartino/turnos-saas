import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** Reglas de ARCHITECTURE.md §3.1.5 que no deben romperse sin que falle algo: el navegador no habla con Supabase. */

const RAIZ = join(import.meta.dirname, "..");

function archivos(dir: string): string[] {
  return readdirSync(dir).flatMap((nombre) => {
    const ruta = join(dir, nombre);
    return statSync(ruta).isDirectory() ? archivos(ruta) : [ruta];
  });
}

const fuentes = archivos(join(RAIZ, "src")).filter(
  (f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f) && !f.includes(`${join("src", "test")}`),
);

describe("restricciones de seguridad del frontend", () => {
  it("no importa ningún paquete de Supabase ni lo declara como dependencia", () => {
    const paquete = JSON.parse(readFileSync(join(RAIZ, "package.json"), "utf8")) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
    const declaradas = Object.keys({ ...paquete.dependencies, ...paquete.devDependencies });
    expect(declaradas.filter((d) => d.includes("supabase"))).toEqual([]);
    for (const f of fuentes) {
      expect(readFileSync(f, "utf8"), f).not.toMatch(/from\s+["']@?supabase|require\(["']@?supabase/);
    }
  });

  it("no usa localStorage ni sessionStorage para la sesión", () => {
    for (const f of fuentes) {
      expect(readFileSync(f, "utf8"), f).not.toMatch(/localStorage|sessionStorage/);
    }
  });

  it("las únicas variables VITE_ son la URL del API, el DSN público de Sentry y la site key pública de Turnstile", () => {
    const permitidas = new Set(["VITE_API_URL", "VITE_SENTRY_DSN", "VITE_TURNSTILE_SITE_KEY"]);
    for (const f of [...fuentes, join(RAIZ, ".env.example")]) {
      const usadas = readFileSync(f, "utf8").match(/VITE_[A-Z0-9_]+/g) ?? [];
      for (const v of usadas) expect(permitidas.has(v), `${v} en ${f}`).toBe(true);
    }
  });

  it("la CSP admite Turnstile solo en script-src y frame-src, sin aflojar el resto", () => {
    const vercel = JSON.parse(readFileSync(join(RAIZ, "vercel.json"), "utf8")) as {
      headers: { headers: { key: string; value: string }[] }[];
    };
    const csp = vercel.headers
      .flatMap((h) => h.headers)
      .find((h) => h.key === "Content-Security-Policy")?.value;
    expect(csp).toBeDefined();
    const directivas = Object.fromEntries(
      csp!.split(";").map((d) => {
        const [nombre, ...valores] = d.trim().split(/\s+/);
        return [nombre, valores];
      }),
    ) as Record<string, string[]>;
    const CF = "https://challenges.cloudflare.com";
    expect(directivas["script-src"]).toEqual(["'self'", CF]);
    expect(directivas["frame-src"]).toEqual([CF]);
    expect(directivas["default-src"]).toEqual(["'self'"]);
    expect(directivas["connect-src"]).toEqual(["'self'", "https:"]);
    expect(csp).not.toMatch(/unsafe-eval|script-src[^;]*unsafe-inline/);
    expect(directivas["frame-ancestors"]).toEqual(["'none'"]);
  });
});
