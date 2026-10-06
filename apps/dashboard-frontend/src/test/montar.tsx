import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { ProveedorSesion } from "../auth/SesionContext";

/** Monta una pantalla dentro del proveedor de sesión (que consulta `GET /auth/sesion` al backend simulado). */
export function montarPantalla(pantalla: ReactElement, ruta = "/") {
  return render(
    <MemoryRouter initialEntries={[ruta]}>
      <ProveedorSesion>{pantalla}</ProveedorSesion>
    </MemoryRouter>,
  );
}
