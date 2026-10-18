import { useState } from "react";
import { verificarCadenaPlataforma, verificarCadenaTenant, type VerificacionCadena } from "../api/panel";
import { mensajeDeError } from "../compartido/useCarga";

/** Acción "Verificar integridad" de la cadena de auditoría: de un tenant o de la plataforma (`tenantId` ausente). */
export function VerificarIntegridad({ tenantId }: { tenantId?: string }) {
  const [trabajando, setTrabajando] = useState(false);
  const [resultado, setResultado] = useState<VerificacionCadena | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alcance = tenantId ? "del tenant" : "de la plataforma";

  async function verificar() {
    setTrabajando(true);
    setError(null);
    setResultado(null);
    try {
      setResultado(await (tenantId ? verificarCadenaTenant(tenantId) : verificarCadenaPlataforma()));
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo verificar la integridad"));
    } finally {
      setTrabajando(false);
    }
  }

  return (
    <div className="fila">
      <button type="button" onClick={verificar} disabled={trabajando}>
        {tenantId ? "Verificar integridad" : "Verificar integridad de la plataforma"}
      </button>
      {resultado &&
        (resultado.integra ? (
          <p role="status">La cadena de auditoría {alcance} está íntegra.</p>
        ) : (
          <p role="alert">
            La cadena de auditoría {alcance} NO está íntegra: la primera fila inválida es la número{" "}
            {resultado.fila_invalida_chain_seq ?? "desconocida"}
            {resultado.fila_invalida_id ? ` (id ${resultado.fila_invalida_id})` : ""}.
          </p>
        ))}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
