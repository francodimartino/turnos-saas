import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { useContinuarTrasLogin } from "./useContinuarTrasLogin";

vi.mock("./SesionContext", () => ({
  useSesion: () => ({ definirPasoMfa: vi.fn(), recargar: vi.fn().mockResolvedValue(undefined) }),
}));

async function destinoTras(desde: string | undefined): Promise<string> {
  const envoltorio = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[{ pathname: "/login", state: desde === undefined ? null : { desde } }]}>
      {children}
    </MemoryRouter>
  );
  const { result } = renderHook(() => ({ continuar: useContinuarTrasLogin(), ubicacion: useLocation() }), {
    wrapper: envoltorio,
  });
  await act(async () => {
    await result.current.continuar({ siguiente_paso: "listo", expira_en: 0 });
  });
  return result.current.ubicacion.pathname;
}

describe("useContinuarTrasLogin: redirect posterior al login", () => {
  it("navega a una ruta interna válida", async () => {
    expect(await destinoTras("/turnos")).toBe("/turnos");
  });

  it.each(["//evil.com", "/\\evil.com", "https://evil.com", ""])("rechaza %j y va a la raíz", async (desde) => {
    expect(await destinoTras(desde)).toBe("/");
  });

  it("sin 'desde' va a la raíz", async () => {
    expect(await destinoTras(undefined)).toBe("/");
  });
});
