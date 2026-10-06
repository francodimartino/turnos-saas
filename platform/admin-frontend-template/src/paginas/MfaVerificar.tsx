import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { verificarMfa } from "../api/auth";
import { ErrorApi } from "../api/cliente";
import { useSesion } from "../auth/SesionContext";

/** Segundo paso del login de un usuario que ya tiene un factor TOTP verificado. */
export function MfaVerificar() {
  const { pasoMfa, definirPasoMfa, recargar } = useSesion();
  const navegar = useNavigate();
  const [codigo, setCodigo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [terminado, setTerminado] = useState(false);

  if (terminado) return null;
  if (pasoMfa?.paso !== "verificar") return <Navigate to="/login" replace />;
  const { factorId } = pasoMfa;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      await verificarMfa(factorId, codigo);
      // La navegación de React Router es una transición (se aplica después): mientras tanto esta pantalla no
      // tiene que mandar a login por haber limpiado el paso de MFA.
      setTerminado(true);
      await recargar();
      definirPasoMfa(null);
      navegar("/", { replace: true });
    } catch (err) {
      setTerminado(false);
      setError(err instanceof ErrorApi ? err.message : "No se pudo verificar el código");
      setEnviando(false);
    }
  }

  return (
    <main className="tarjeta">
      <h1>Verificación en dos pasos</h1>
      <p>Escribí el código de 6 dígitos de tu app autenticadora.</p>
      <form onSubmit={(e) => void enviar(e)}>
        <label>
          Código de 6 dígitos
          <input
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            required
            value={codigo}
            onChange={(e) => setCodigo(e.target.value.trim())}
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={enviando}>
          Verificar
        </button>
      </form>
    </main>
  );
}
