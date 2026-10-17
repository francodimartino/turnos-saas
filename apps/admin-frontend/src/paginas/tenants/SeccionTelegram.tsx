import { useState, type FormEvent } from "react";
import { cargarTelegram } from "../../api/panel";
import { mensajeDeError } from "../../compartido/useCarga";

const FORMATO_TOKEN = /^\d+:[A-Za-z0-9_-]+$/;
const FORMATO_SECRETO = /^[A-Za-z0-9_-]{32,256}$/;
const ALFABETO = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";

/** Secreto aleatorio de 40 caracteres [A-Za-z0-9_-] (64 símbolos: 256 es múltiplo de 64, sin sesgo). */
export function generarSecretoWebhook(largo = 40): string {
  const bytes = crypto.getRandomValues(new Uint8Array(largo));
  return Array.from(bytes, (b) => ALFABETO[b % ALFABETO.length]).join("");
}

/**
 * Valida el formulario y arma el `secret` que espera el bot: el JSON `{bot_token, webhook_secret}`.
 * Si el secreto del webhook viene vacío se genera uno. Devuelve el error de validación o el resultado.
 */
export function armarCredencial(
  token: string,
  secretoWebhook: string,
): { error: string } | { secret: string; webhookSecret: string } {
  const bot_token = token.trim();
  if (!FORMATO_TOKEN.test(bot_token)) {
    return {
      error: "El token del bot no tiene el formato esperado: números, dos puntos y letras, como 123456:ABC… (lo da @BotFather).",
    };
  }
  const elegido = secretoWebhook.trim();
  if (elegido !== "" && !FORMATO_SECRETO.test(elegido)) {
    return { error: "El secreto del webhook debe tener entre 32 y 256 caracteres: solo letras, números, _ y -. Mínimo 32." };
  }
  const webhook_secret = elegido === "" ? generarSecretoWebhook() : elegido;
  return { secret: JSON.stringify({ bot_token, webhook_secret }), webhookSecret: webhook_secret };
}

/**
 * Carga de la credencial del bot de Telegram del tenant. Es de solo escritura: los valores se mandan y los campos se
 * vacían; el backend nunca los devuelve. Tras guardar se muestran una sola vez el identificador del webhook y el
 * secreto del webhook usado, porque hay que registrarlo en Telegram y no se puede volver a ver.
 */
export function SeccionTelegram({ tenantId }: { tenantId: string }) {
  const [token, setToken] = useState("");
  const [secretoWebhook, setSecretoWebhook] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<{ webhookKey: string; webhookSecret: string } | null>(null);
  const [ocupado, setOcupado] = useState(false);

  async function guardar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResultado(null);
    const credencial = armarCredencial(token, secretoWebhook);
    if ("error" in credencial) {
      setError(credencial.error);
      return;
    }
    setOcupado(true);
    try {
      const r = await cargarTelegram(tenantId, credencial.secret);
      setResultado({ webhookKey: r.webhook_key, webhookSecret: credencial.webhookSecret });
    } catch (err) {
      setError(mensajeDeError(err));
    } finally {
      // Éxito o error, los valores no se quedan en los campos ni en el estado del formulario.
      setToken("");
      setSecretoWebhook("");
      setOcupado(false);
    }
  }

  return (
    <form className="panel" onSubmit={(e) => void guardar(e)}>
      <h2>Bot de Telegram</h2>
      <p>
        La credencial se guarda cifrada y no se puede volver a ver. Cargar una nueva reemplaza la anterior.
      </p>
      <label className="campo">
        Token del bot
        <input type="password" autoComplete="off" required value={token} onChange={(e) => setToken(e.target.value)} />
        <small>Lo da @BotFather al crear el bot. Tiene el formato 123456:ABC…</small>
      </label>
      <label className="campo">
        Secreto del webhook (opcional)
        <input
          type="password"
          autoComplete="off"
          value={secretoWebhook}
          onChange={(e) => setSecretoWebhook(e.target.value)}
        />
        <small>De 32 a 256 caracteres: letras, números, _ y -. Si lo dejás vacío se genera uno aleatorio.</small>
      </label>
      <button type="submit" disabled={ocupado || token === ""}>
        Guardar credencial
      </button>
      {resultado && (
        <div role="status">
          <p>
            Credencial guardada. Identificador del webhook: <code>{resultado.webhookKey}</code>
          </p>
          <p>
            Secreto del webhook: <code>{resultado.webhookSecret}</code>
          </p>
          <p>Copialo ahora: no se puede volver a ver. Es el que se registra en Telegram (setWebhook) y en el simulador.</p>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
