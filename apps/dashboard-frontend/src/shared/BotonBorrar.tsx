import { useState } from "react";
import { erroresDeApi } from "./errores";

interface Props {
  /** Nombre del registro, para los textos ("Corte"). */
  nombre: string;
  borrar: () => Promise<void>;
  alBorrar: () => void;
}

/** Borrado en dos pasos. Un 409 `en_uso` se explica en lugar de fallar en silencio. */
export function BotonBorrar({ nombre, borrar, alBorrar }: Props) {
  const [confirmando, setConfirmando] = useState(false);
  const [trabajando, setTrabajando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmar() {
    setTrabajando(true);
    setError(null);
    try {
      await borrar();
      alBorrar();
    } catch (e) {
      setError(erroresDeApi(e).general);
      setConfirmando(false);
      setTrabajando(false);
    }
  }

  if (confirmando) {
    return (
      <span className="confirmar">
        ¿Borrar {nombre}?{" "}
        <button type="button" disabled={trabajando} onClick={() => void confirmar()}>
          Confirmar borrado de {nombre}
        </button>{" "}
        <button type="button" onClick={() => setConfirmando(false)}>
          Cancelar
        </button>
      </span>
    );
  }
  return (
    <span>
      <button type="button" onClick={() => setConfirmando(true)}>
        Borrar {nombre}
      </button>
      {error && <p role="alert">{error}</p>}
    </span>
  );
}
