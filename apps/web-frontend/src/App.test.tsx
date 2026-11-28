import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { NoDisponible } from "./paginas/NoDisponible";
import { VERSION_POLITICA } from "./politica";

function irA(ruta: string) {
  return render(
    <MemoryRouter initialEntries={[ruta]}>
      <App />
    </MemoryRouter>,
  );
}

describe("páginas públicas", () => {
  it("/ muestra la landing con propuesta, cómo funciona y contacto", () => {
    irA("/");
    expect(screen.getByRole("heading", { level: 1, name: "Turnos online para tu negocio" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cómo funciona" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Contacto" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("/privacidad muestra el aviso de texto provisorio y la versión de la política", () => {
    irA("/privacidad");
    expect(screen.getByRole("heading", { level: 1, name: "Política de privacidad" })).toBeInTheDocument();
    expect(screen.getByText("TEXTO PROVISORIO: reemplazar por la política redactada")).toBeVisible();
    expect(screen.getByTestId("version-politica")).toHaveTextContent(VERSION_POLITICA);
  });

  it("un nombre reservado sin página propia no se trata como negocio", () => {
    irA("/precios");
    expect(screen.getByRole("heading", { name: "No encontramos esta página" })).toBeInTheDocument();
    expect(screen.queryByText("Esta página no está disponible")).not.toBeInTheDocument();
  });

  it("/<slug> muestra siempre la página no disponible", () => {
    irA("/barberia-del-centro");
    expect(screen.getByRole("heading", { name: "Esta página no está disponible" })).toBeInTheDocument();
  });

  it("un slug con formato inválido o una ruta más larga no es un negocio", () => {
    irA("/Barberia");
    expect(screen.getByRole("heading", { name: "No encontramos esta página" })).toBeInTheDocument();
  });

  it("una ruta de más de un tramo muestra no encontrada", () => {
    irA("/barberia/algo");
    expect(screen.getByRole("heading", { name: "No encontramos esta página" })).toBeInTheDocument();
  });
});

describe("NoDisponible", () => {
  it("se renderiza con un enlace para volver al inicio", () => {
    render(
      <MemoryRouter>
        <NoDisponible />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Esta página no está disponible" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ir al inicio" })).toHaveAttribute("href", "/");
  });
});
