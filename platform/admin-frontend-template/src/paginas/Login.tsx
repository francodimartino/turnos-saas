import { useState, type FormEvent } from "react";
import { login, mensajeDeErrorLogin } from "../api/auth";
import { useContinuarTrasLogin } from "../auth/useContinuarTrasLogin";
import { WidgetTurnstile } from "../compartido/WidgetTurnstile";

/** Site key pública de Turnstile. Vacía = sin captcha: no se carga el script ni se envía `turnstile_token`. */
function siteKeyTurnstile(): string {
  return (import.meta.env.VITE_TURNSTILE_SITE_KEY ?? "").trim();
}

export function Login() {
  const continuar = useContinuarTrasLogin();
  const siteKey = siteKeyTurnstile();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState<string | null>(null);
  // Cada token de Turnstile se usa una sola vez: tras un intento fallido se vuelve a montar el widget.
  const [intento, setIntento] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const faltaCaptcha = siteKey !== "" && token === null;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (faltaCaptcha) return;
    setError(null);
    setEnviando(true);
    try {
      await continuar(await login(email, password, siteKey !== "" ? (token ?? undefined) : undefined));
    } catch (err) {
      setError(mensajeDeErrorLogin(err));
      setToken(null);
      setIntento((n) => n + 1);
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
        {siteKey !== "" && <WidgetTurnstile key={intento} siteKey={siteKey} alCambiarToken={setToken} />}
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={enviando || faltaCaptcha}>
          Ingresar
        </button>
      </form>
    </main>
  );
}
