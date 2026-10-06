import { useCallback, useState, type FormEvent } from "react";
import { guardarFacturacion, obtenerFacturacion, type Facturacion } from "../../api/panel";
import { ConCarga } from "../../compartido/ConCarga";
import { mensajeDeError, useCarga } from "../../compartido/useCarga";

/** Estado de pago manual: `paid_until` y `billing_notes`. Vaciar un campo lo borra. */
export function SeccionFacturacion({ tenantId }: { tenantId: string }) {
  const cargar = useCallback(() => obtenerFacturacion(tenantId), [tenantId]);
  const { carga } = useCarga(cargar);
  return (
    <div className="panel">
      <h2>Facturación</h2>
      <ConCarga carga={carga} hijos={(f) => <FormularioFacturacion tenantId={tenantId} inicial={f} />} />
    </div>
  );
}

function FormularioFacturacion({ tenantId, inicial }: { tenantId: string; inicial: Facturacion }) {
  const [pagadoHasta, setPagadoHasta] = useState(inicial.paid_until ?? "");
  const [notas, setNotas] = useState(inicial.billing_notes ?? "");
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  async function guardar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setAviso(null);
    setOcupado(true);
    try {
      const r = await guardarFacturacion(tenantId, {
        paid_until: pagadoHasta || null,
        billing_notes: notas.trim() || null,
      });
      setPagadoHasta(r.paid_until ?? "");
      setNotas(r.billing_notes ?? "");
      setAviso("Facturación guardada.");
    } catch (err) {
      setError(mensajeDeError(err));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <form onSubmit={(e) => void guardar(e)}>
      <label className="campo">
        Pagado hasta
        <input type="date" value={pagadoHasta} onChange={(e) => setPagadoHasta(e.target.value)} />
      </label>
      <label className="campo">
        Notas de facturación
        <textarea rows={3} value={notas} onChange={(e) => setNotas(e.target.value)} />
      </label>
      <button type="submit" disabled={ocupado}>
        Guardar facturación
      </button>
      {aviso && <p role="status">{aviso}</p>}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
