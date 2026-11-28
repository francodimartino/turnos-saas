import { describe, expect, it } from "vitest";
import { ErrorApi } from "../../api/cliente";
import { mensajeDeGuardarApariencia, mensajeDeQuitarLogo, mensajeDeSubirLogo } from "./mensajes";

const error = (codigo: string, estado = 422) => new ErrorApi(estado, codigo, "TEXTO-DEL-BACKEND");

describe("mensajes de la apariencia", () => {
  it.each(["sin_conexion", "no_encontrado", "permiso_denegado", "almacenamiento_no_disponible"])(
    "el código %s no comparte texto entre acciones distintas",
    (codigo) => {
      const subir = mensajeDeSubirLogo(error(codigo));
      const quitar = mensajeDeQuitarLogo(error(codigo));
      const guardar = mensajeDeGuardarApariencia(error(codigo));
      // `sin_conexion` es el mismo problema de red para cualquier acción: es el único texto compartido.
      expect(new Set([subir, quitar, guardar]).size).toBe(codigo === "sin_conexion" ? 1 : 3);
      for (const m of [subir, quitar, guardar]) expect(m).not.toContain("TEXTO-DEL-BACKEND");
    },
  );

  it("los mensajes genéricos de cada acción también son distintos", () => {
    const genericos = [mensajeDeSubirLogo(new Error("x")), mensajeDeQuitarLogo(new Error("x")), mensajeDeGuardarApariencia(new Error("x"))];
    expect(new Set(genericos).size).toBe(3);
  });
});
