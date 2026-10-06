import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { instalarBackend } from "../test/falsoBackend";
import { CalendarCallback } from "./CalendarCallback";

afterEach(() => vi.unstubAllGlobals());

function Destino() {
  const [q] = useSearchParams();
  return <p>Profesionales: {q.toString()}</p>;
}

function montar(ruta: string) {
  return render(
    <MemoryRouter initialEntries={[ruta]}>
      <Routes>
        <Route path="/calendar/callback" element={<CalendarCallback />} />
        <Route path="/profesionales" element={<Destino />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CalendarCallback", () => {
  it("manda code y state al backend y vuelve a profesionales con el resultado", async () => {
    const backend = instalarBackend({
      "POST /calendar/callback": () => ({ cuerpo: { resultado: "ok", profesional_id: "p-1" } }),
    });
    montar("/calendar/callback?code=abc&state=xyz");
    expect(await screen.findByText("Profesionales: google_calendar=ok&profesional=p-1")).toBeInTheDocument();
    expect(backend.cuantas("POST /calendar/callback")).toBe(1);
    const llamada = backend.llamadas[0];
    expect(llamada?.cuerpo).toEqual({ code: "abc", state: "xyz", error: "" });
    expect(llamada?.credentials).toBe("include");
  });

  it("si el usuario canceló en Google reenvía el error al backend y muestra su resultado", async () => {
    const backend = instalarBackend({
      "POST /calendar/callback": () => ({
        status: 400,
        cuerpo: { detail: { codigo: "cancelado", mensaje: "No se concedió el acceso a Google Calendar." } },
      }),
    });
    montar("/calendar/callback?error=access_denied&state=xyz");
    expect(await screen.findByText("Profesionales: google_calendar=cancelado")).toBeInTheDocument();
    expect(backend.llamadas[0]?.cuerpo).toEqual({ code: "", state: "xyz", error: "access_denied" });
  });

  it("un state vencido vuelve con state_invalido", async () => {
    instalarBackend({
      "POST /calendar/callback": () => ({ status: 400, cuerpo: { detail: { codigo: "state_invalido", mensaje: "x" } } }),
    });
    montar("/calendar/callback?code=abc&state=viejo");
    expect(await screen.findByText("Profesionales: google_calendar=state_invalido")).toBeInTheDocument();
  });

  it("un error inesperado (o sin conexión) vuelve con un resultado genérico", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("red")));
    montar("/calendar/callback?code=abc&state=xyz");
    expect(await screen.findByText("Profesionales: google_calendar=error")).toBeInTheDocument();
  });
});
