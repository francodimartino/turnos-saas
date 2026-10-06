import { useState, type FormEvent } from "react";
import { cargarTelegram, type ResultadoIntegracion } from "../../api/panel";
import { mensajeDeError } from "../../compartido/useCarga";

/**
 * Carga de la credencial del bot de Telegram del tenant. Es de solo escritura: el valor se manda y el campo se
 * vacía; el backend nunca lo devuelve, así que no hay nada que mostrar después de guardar.
 */
export function SeccionTelegram({ tenantId }: { tenantId: string }) {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<ResultadoIntegracion | null>(null);
  const [ocupado, setOcupado] = useState(false);

  async function guardar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResultado(null);
    setOcupado(true);
    try {
      const r = await cargarTelegram(tenantId, secret);
      setResultado(r);
    } catch (err) {
      setError(mensajeDeError(err));
    } finally {
      // Éxito o error, el valor no se queda en pantalla ni en el estado.
      setSecret("");
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
        Credencial del bot
        <input type="password" autoComplete="off" required value={secret} onChange={(e) => setSecret(e.target.value)} />
      </label>
      <button type="submit" disabled={ocupado || secret === ""}>
        Guardar credencial
      </button>
      {resultado && (
        <p role="status">
          Credencial guardada. Identificador del webhook: <code>{resultado.webhook_key}</code>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
