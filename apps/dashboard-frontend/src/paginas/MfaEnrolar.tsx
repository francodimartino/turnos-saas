import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { enrolarMfa, verificarMfa, type EnrolamientoMfa } from "../api/auth";
import { ErrorApi } from "../api/cliente";
import { useSesion } from "../auth/SesionContext";

/** Alta del factor TOTP de un owner sin MFA: muestra el QR, pide el primer código y abre la sesión aal2. */
export function MfaEnrolar() {
  const { pasoMfa, definirPasoMfa, recargar } = useSesion();
  const navegar = useNavigate();
  const [factor, setFactor] = useState<EnrolamientoMfa | null>(null);
  const [codigo, setCodigo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [terminado, setTerminado] = useState(false);
  const pedido = useRef(false);
  const habilitado = pasoMfa?.paso === "enrolar";

  useEffect(() => {
    // Cada enrolamiento crea un factor nuevo en el servidor: se pide una sola vez por visita.
    if (!habilitado || pedido.current) return;
    pedido.current = true;
    enrolarMfa()
      .then(setFactor)
      .catch((err: unknown) =>
        setError(err instanceof ErrorApi ? err.message : "No se pudo iniciar la configuración de MFA"),
      );
  }, [habilitado]);

  if (terminado) return null;
  if (!habilitado) return <Navigate to="/login" replace />;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (!factor) return;
    setError(null);
    setEnviando(true);
    try {
      await verificarMfa(factor.factor_id, codigo);
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
      <h1>Configurá la verificación en dos pasos</h1>
      <p>Escaneá el código QR con tu app autenticadora y escribí el código de 6 dígitos que muestra.</p>
      {factor && (
        <>
          <img src={factor.qr_code} alt="Código QR para la app autenticadora" width={200} height={200} />
          <p>
            ¿No podés escanearlo? Cargá esta clave a mano: <code>{factor.secret}</code>
          </p>
        </>
      )}
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
        <button type="submit" disabled={!factor || enviando}>
          Activar
        </button>
      </form>
    </main>
  );
}
