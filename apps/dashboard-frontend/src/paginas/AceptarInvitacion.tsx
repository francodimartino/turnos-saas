import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { aceptarInvitacion } from "../api/auth";
import { ErrorApi } from "../api/cliente";
import { useContinuarTrasLogin } from "../auth/useContinuarTrasLogin";

const LARGO_MINIMO = 8;

/** Destino del mail de invitación (`?token_hash=...`): el invitado elige su contraseña y queda con sesión abierta. */
export function AceptarInvitacion() {
  const [params] = useSearchParams();
  const tokenHash = params.get("token_hash");
  const continuar = useContinuarTrasLogin();
  const [password, setPassword] = useState("");
  const [repetida, setRepetida] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  if (!tokenHash) {
    return (
      <main className="tarjeta">
        <h1>Invitación</h1>
        <p role="alert">El enlace de invitación no es válido. Pedí que te envíen una invitación nueva.</p>
      </main>
    );
  }

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < LARGO_MINIMO) {
      setError(`La contraseña debe tener al menos ${LARGO_MINIMO} caracteres`);
      return;
    }
    if (password !== repetida) {
      setError("Las contraseñas no coinciden");
      return;
    }
    setEnviando(true);
    try {
      await continuar(await aceptarInvitacion(tokenHash!, password));
    } catch (err) {
      setError(err instanceof ErrorApi ? err.message : "No se pudo aceptar la invitación");
      setEnviando(false);
    }
  }

  return (
    <main className="tarjeta">
      <h1>Aceptar invitación</h1>
      <p>Elegí una contraseña para tu cuenta.</p>
      <form onSubmit={(e) => void enviar(e)}>
        <label>
          Contraseña nueva
          <input
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label>
          Repetí la contraseña
          <input
            type="password"
            autoComplete="new-password"
            required
            value={repetida}
            onChange={(e) => setRepetida(e.target.value)}
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={enviando}>
          Crear cuenta
        </button>
      </form>
    </main>
  );
}
