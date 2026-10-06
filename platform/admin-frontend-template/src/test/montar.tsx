import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { ProveedorSesion } from "../auth/SesionContext";

/** Monta la app completa en `ruta`, con la sesión resuelta por el backend simulado (`GET /auth/sesion`). */
export function montarEn(ruta: string) {
  return render(
    <MemoryRouter initialEntries={[ruta]}>
      <ProveedorSesion>
        <App />
      </ProveedorSesion>
    </MemoryRouter>,
  );
}
