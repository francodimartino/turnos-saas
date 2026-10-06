import { describe, expect, it } from "vitest";
import { diaDe, horaDe, inicioSemana, rango, sumarDias } from "./fechas";

describe("fechas de la agenda", () => {
  it("suma días cruzando mes y año", () => {
    expect(sumarDias("2026-12-31", 1)).toBe("2027-01-01");
    expect(sumarDias("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("la semana empieza el lunes", () => {
    expect(inicioSemana("2026-10-05")).toBe("2026-10-05"); // lunes
    expect(inicioSemana("2026-10-11")).toBe("2026-10-05"); // domingo
    expect(inicioSemana("2026-10-07")).toBe("2026-10-05");
  });

  it("el rango va de medianoche a medianoche de Buenos Aires", () => {
    expect(rango("2026-10-07", "dia")).toEqual({
      desde: "2026-10-07T00:00:00-03:00",
      hasta: "2026-10-08T00:00:00-03:00",
      dias: ["2026-10-07"],
    });
    const semana = rango("2026-10-07", "semana");
    expect(semana.desde).toBe("2026-10-05T00:00:00-03:00");
    expect(semana.hasta).toBe("2026-10-12T00:00:00-03:00");
    expect(semana.dias).toHaveLength(7);
  });

  it("un instante de la noche UTC cae en el día anterior de Buenos Aires", () => {
    expect(diaDe("2026-10-08T01:30:00+00:00")).toBe("2026-10-07");
    expect(horaDe("2026-10-08T01:30:00+00:00")).toBe("22:30");
  });
});
