import type { ErrorEvent } from "@sentry/react";
import { describe, expect, it } from "vitest";
import { iniciarSentry, limpiarEvento, redactarTexto } from "./sentry";

describe("Sentry sin PII", () => {
  it("limpiarEvento quita usuario, request, breadcrumbs y extras, y redacta emails y teléfonos", () => {
    const evento = {
      type: undefined,
      message: "falló para ana@negocio.com",
      user: { id: "1", email: "ana@negocio.com", ip_address: "1.2.3.4" },
      request: { url: "https://x/?tel=5491155551234", cookies: { dash_acceso: "t" }, headers: { Cookie: "a" } },
      breadcrumbs: [{ message: "click en Ana Pérez" }],
      extra: { telefono: "+54 9 11 5555-1234" },
      exception: { values: [{ type: "Error", value: "cliente +54 9 11 5555-1234 no encontrado" }] },
    } as ErrorEvent;

    const limpio = limpiarEvento(evento);

    expect(limpio.user).toBeUndefined();
    expect(limpio.request).toBeUndefined();
    expect(limpio.breadcrumbs).toBeUndefined();
    expect(limpio.extra).toBeUndefined();
    expect(limpio.message).toBe("falló para [email]");
    expect(limpio.exception?.values?.[0]?.value).toBe("cliente [telefono] no encontrado");
  });

  it("redactarTexto deja intacto el texto sin datos personales", () => {
    expect(redactarTexto("Error al cargar el panel")).toBe("Error al cargar el panel");
  });

  it("sin DSN no se inicia", () => {
    expect(iniciarSentry(undefined)).toBe(false);
    expect(iniciarSentry("")).toBe(false);
  });
});
