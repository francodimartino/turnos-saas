import { describe, expect, it } from "vitest";
import { cambioVencido, diaDe, horaDe, inicioSemana, rango, rangoLegible, sumarDias } from "./fechas";

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

describe("cambio de horario pendiente", () => {
  const AHORA = Date.parse("2026-10-08T12:00:00Z");
  const cambio = {
    start_at: "2026-10-09T13:00:00Z",
    starts_at_propuesto: "2026-10-10T13:00:00Z",
    cambio_pendiente_expira_at: "2026-10-08T20:00:00Z",
  };

  it("un cambio vigente se puede aprobar", () => {
    expect(cambioVencido(cambio, AHORA)).toBe(false);
  });

  it("vence cuando pasa su vencimiento", () => {
    expect(cambioVencido({ ...cambio, cambio_pendiente_expira_at: "2026-10-08T12:00:00Z" }, AHORA)).toBe(true);
  });

  it("ya no se aprueba si el turno original o el horario propuesto ya empezaron", () => {
    expect(cambioVencido({ ...cambio, start_at: "2026-10-08T11:00:00Z" }, AHORA)).toBe(true);
    expect(cambioVencido({ ...cambio, starts_at_propuesto: "2026-10-08T11:00:00Z" }, AHORA)).toBe(true);
  });

  it("sin cambio propuesto no hay nada vencido", () => {
    expect(cambioVencido({ ...cambio, starts_at_propuesto: null }, AHORA)).toBe(false);
  });

  it("arma el rango en hora de Buenos Aires y repite el día si el cierre es otro", () => {
    expect(rangoLegible("2026-10-08T13:00:00Z", "2026-10-08T13:30:00Z")).toMatch(/08\/10\/2026.*10:00 a 10:30/);
    expect(rangoLegible("2026-10-08T13:00:00Z", "2026-10-09T13:30:00Z")).toMatch(/10:00 a 09\/10\/2026.*10:30/);
  });
});
