import { useState, type FormEvent } from "react";
import { login } from "../api/auth";
import { useContinuarTrasLogin } from "../auth/useContinuarTrasLogin";
import { mensajeDeError, useTurnstile } from "../shared/Turnstile";

export function Login() {
  const continuar = useContinuarTrasLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const turnstile = useTurnstile();

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      await continuar(await login(email, password, turnstile.token ?? undefined));
    } catch (err) {
      setError(mensajeDeError(err, "No se pudo iniciar sesión"));
      turnstile.reiniciar();
      setEnviando(false);
    }
  }

  return (
    <main className="tarjeta">
      <h1>Iniciar sesión</h1>
      <form onSubmit={(e) => void enviar(e)}>
        <label>
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Contraseña
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {turnstile.widget}
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={enviando || (turnstile.activo && !turnstile.token)}>
          Ingresar
        </button>
      </form>
    </main>
  );
}
