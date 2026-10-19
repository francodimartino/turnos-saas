import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";
import { reiniciarEstadoDeRefresh } from "../api/cliente";

// findBy*/waitFor esperan 1 s por defecto: bajo carga una espera correcta puede vencer sin que haya un bug.
// 5 s sigue fallando rápido ante una regresión real, y el test entero tiene su propio límite de 15 s.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  reiniciarEstadoDeRefresh();
});
