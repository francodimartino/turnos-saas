import { describe, expect, it } from "vitest";
import { armarCredencial } from "./SeccionTelegram";

const TOKEN = "123456:ABC";

describe("armarCredencial: largo del secreto del webhook", () => {
  it("con 31 caracteres devuelve error de mínimo 32", () => {
    const r = armarCredencial(TOKEN, "a".repeat(31));
    expect(r).toHaveProperty("error");
    expect((r as { error: string }).error).toMatch(/32/);
  });

  it.each([32, 256])("con %i caracteres devuelve el secret", (largo) => {
    const elegido = "aB3_-".repeat(60).slice(0, largo);
    const r = armarCredencial(TOKEN, elegido);
    expect(r).toHaveProperty("secret");
    expect(JSON.parse((r as { secret: string }).secret)).toEqual({ bot_token: TOKEN, webhook_secret: elegido });
  });

  it("con más de 256 caracteres devuelve error", () => {
    expect(armarCredencial(TOKEN, "a".repeat(257))).toHaveProperty("error");
  });

  it("vacío genera uno de 40 caracteres", () => {
    const r = armarCredencial(TOKEN, "");
    expect((r as { webhookSecret: string }).webhookSecret).toMatch(/^[A-Za-z0-9_-]{40}$/);
  });
});
