import { useState, type FormEvent } from "react";
import { login } from "../api/auth";
import { ErrorApi } from "../api/cliente";
import { useContinuarTrasLogin } from "../auth/useContinuarTrasLogin";

export function Login() {
  const continuar = useContinuarTrasLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      await continuar(await login(email, password));
    } catch (err) {
      // El mensaje del backend ya distingue credenciales incorrectas de una cuenta que no es administradora.
      setError(err instanceof ErrorApi ? err.message : "No se pudo iniciar sesión");
      setEnviando(false);
    }
  }

  return (
    <main className="tarjeta">
      <h1>Iniciar sesión</h1>
      <p>Administración de la plataforma</p>
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
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={enviando}>
          Ingresar
        </button>
      </form>
    </main>
  );
}
