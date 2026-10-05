import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProveedorSesion } from "../auth/SesionContext";
import { instalarBackend, sesionDe } from "../test/falsoBackend";
import { Layout } from "./Layout";
import { seccionesVisibles, type Seccion } from "./permisos";

afterEach(() => {
  vi.unstubAllGlobals();
});

const SECCIONES: Seccion[] = [
  { ruta: "/", etiqueta: "Inicio" },
  { ruta: "/turnos", etiqueta: "Turnos", permiso: "view_bookings" },
  { ruta: "/reportes", etiqueta: "Reportes", permiso: "view_reports" },
  { ruta: "/equipo", etiqueta: "Equipo", permiso: "manage_staff" },
];

function montar() {
  return render(
    <MemoryRouter>
      <ProveedorSesion>
        <Routes>
          <Route element={<Layout secciones={SECCIONES} />}>
            <Route index element={<p>contenido</p>} />
          </Route>
        </Routes>
      </ProveedorSesion>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  it("oculta las secciones para las que el usuario no tiene permiso", async () => {
    instalarBackend({ "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1", permisos: ["view_bookings"] })] });
    montar();
    expect(await screen.findByRole("link", { name: "Turnos" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Inicio" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Reportes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Equipo" })).not.toBeInTheDocument();
  });

  it("un usuario sin permisos asignados solo ve las secciones abiertas", async () => {
    instalarBackend({ "GET /auth/sesion": [sesionDe({ is_owner: false, aal: "aal1" })] });
    montar();
    expect(await screen.findByRole("link", { name: "Inicio" })).toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("el owner ve todas las secciones", async () => {
    instalarBackend({ "GET /auth/sesion": [sesionDe({ is_owner: true, aal: "aal2" })] });
    montar();
    expect(await screen.findByRole("link", { name: "Equipo" })).toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(SECCIONES.length);
  });

  it("cerrar sesión llama al backend y deja de mostrar el marco", async () => {
    const backend = instalarBackend({
      "GET /auth/sesion": [sesionDe({ is_owner: true, aal: "aal2" })],
      "POST /auth/logout": [{ status: 204 }],
    });
    const usuario = userEvent.setup();
    montar();
    await usuario.click(await screen.findByRole("button", { name: "Cerrar sesión" }));
    await vi.waitFor(() => expect(screen.queryByRole("navigation")).not.toBeInTheDocument());
    expect(backend.cuantas("POST /auth/logout")).toBe(1);
  });
});

describe("seccionesVisibles", () => {
  it("filtra por permission_key", () => {
    const sesion = { userId: "u", tenantId: "t", isOwner: false, aal: "aal1" as const, permisos: ["view_reports"] };
    expect(seccionesVisibles(SECCIONES, sesion).map((s) => s.etiqueta)).toEqual(["Inicio", "Reportes"]);
  });
});

