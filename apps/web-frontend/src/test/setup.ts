import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// findBy*/waitFor esperan 1 s por defecto: bajo carga una espera correcta puede vencer sin que haya un bug.
configure({ asyncUtilTimeout: 10_000 });

afterEach(() => {
  cleanup();
});
