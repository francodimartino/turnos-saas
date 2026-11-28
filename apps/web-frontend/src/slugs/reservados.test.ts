import { describe, expect, it } from "vitest";
import { clasificarSlug, SLUGS_RESERVADOS } from "./reservados";

describe("slugs reservados", () => {
  it("incluye los nombres de la landing y los técnicos", () => {
    for (const nombre of ["privacidad", "precios", "contacto", "terminos", "admin", "app", "api", "login"]) {
      expect(SLUGS_RESERVADOS).toContain(nombre);
    }
  });

  it("no repite nombres y todos tienen formato de slug", () => {
    expect(new Set(SLUGS_RESERVADOS).size).toBe(SLUGS_RESERVADOS.length);
    for (const nombre of SLUGS_RESERVADOS) expect(clasificarSlug(nombre)).not.toBe("invalido");
  });

  it("un reservado nunca es un negocio", () => {
    for (const nombre of SLUGS_RESERVADOS) expect(clasificarSlug(nombre)).toBe("reservado");
  });

  it("distingue un posible negocio de algo que no puede ser un slug", () => {
    expect(clasificarSlug("barberia-del-centro")).toBe("negocio");
    expect(clasificarSlug("estudio2")).toBe("negocio");
    for (const malo of ["Barberia", "con espacio", "-guion", "guion-", "dos--guiones", "a_b", "ñandú"]) {
      expect(clasificarSlug(malo)).toBe("invalido");
    }
  });
});
