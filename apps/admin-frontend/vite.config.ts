import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    // Bajo carga (otra suite o varios procesos en paralelo) el límite por defecto de 5 s
    // vence en tests que son correctos pero lentos de renderizar: 15 s da margen sin ocultar cuelgues.
    testTimeout: 15_000,
    hookTimeout: 15_000,
    // Procesos aislados y pocos: evita "Worker exited unexpectedly" y "heap out of memory"
    // cuando la máquina corre varios procesos pesados a la vez.
    pool: "forks",
    maxWorkers: 2,
  },
});
