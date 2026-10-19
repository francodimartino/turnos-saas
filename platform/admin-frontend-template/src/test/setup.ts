import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";
import { reiniciarEstadoDeRefresh } from "../api/cliente";

// findBy*/waitFor esperan 1 s por defecto: bajo carga una espera correcta puede vencer sin que haya un bug.
// 10 s sigue fallando antes que el límite de 30 s del test entero, así que una regresión real se ve como error de aserción y no como timeout.
configure({ asyncUtilTimeout: 10_000 });

afterEach(() => {
  cleanup();
  reiniciarEstadoDeRefresh();
});
