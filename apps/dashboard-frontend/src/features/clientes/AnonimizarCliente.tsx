import { useState } from "react";
import { anonimizarCliente, type Cliente, type ResultadoAnonimizar } from "../../api/clientes";
import { erroresDeApi } from "../../shared/errores";

/** Palabra que hay que escribir para confirmar: anonimizar no se puede deshacer. */
export const PALABRA_CONFIRMACION = "ANONIMIZAR";

interface Props {
  cliente: Cliente;
  alAnonimizar: (resultado: ResultadoAnonimizar) => void;
}

function avisoCalendario(r: ResultadoAnonimizar): string | null {
  const { calendario } = r;
  if (calendario.estado === "error") {
    return "El cliente se anonimizó, pero no pudimos terminar de limpiar sus turnos de Google Calendar. Quedó registrado para revisarlo.";
  }
  if ((calendario.no_limpiados ?? 0) > 0) {
    return `El cliente se anonimizó, pero ${calendario.no_limpiados} de ${calendario.eventos} evento(s) de Google Calendar no se pudieron limpiar. Quedó registrado para revisarlo.`;
  }
  return null;
}

/** Mensaje tras anonimizar; vive fuera del botón porque la ficha se recarga y el botón desaparece. */
export function AvisoAnonimizado({ resultado }: { resultado: ResultadoAnonimizar }) {
  const aviso = avisoCalendario(resultado);
  return (
    <div>
      <p role="status">El cliente fue anonimizado. Ya no se puede identificar a la persona.</p>
      {aviso && <p role="alert">{aviso}</p>}
    </div>
  );
}

/** Derecho al olvido: explica qué pasa, exige escribir una palabra y recién ahí llama al backend. */
export function AnonimizarCliente({ cliente, alAnonimizar }: Props) {
  const [abierto, setAbierto] = useState(false);
  const [texto, setTexto] = useState("");
  const [trabajando, setTrabajando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmar() {
    setTrabajando(true);
    setError(null);
    try {
      const r = await anonimizarCliente(cliente.id);
      setAbierto(false);
      alAnonimizar(r);
    } catch (e) {
      setError(erroresDeApi(e).general ?? "No se pudo anonimizar");
    }
    setTrabajando(false);
  }

  if (!abierto) {
    return (
      <button type="button" onClick={() => setAbierto(true)}>
        Anonimizar a {cliente.name ?? "este cliente"}
      </button>
    );
  }

  const coincide = texto.trim() === PALABRA_CONFIRMACION;
  return (
    <div className="panel" role="group" aria-label="Confirmar anonimización">
      <h3>Anonimizar cliente (derecho al olvido)</h3>
      <p>
        <strong>Esta acción es irreversible.</strong> Se borran el nombre, el teléfono y los datos adicionales del
        cliente, y ya no se podrá saber quién es.
      </p>
      <ul>
        <li>Los turnos futuros que estén pendientes de aprobación se cancelan y liberan el horario.</li>
        <li>
          Los turnos ya confirmados y los pasados se conservan en el historial, pero se muestran sin cliente
          identificable.
        </li>
        <li>Los eventos de sus turnos se quitan de Google Calendar.</li>
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (coincide) void confirmar();
        }}
      >
        <div className="campo">
          <label htmlFor="confirmacion-anonimizar">Para confirmar, escribí {PALABRA_CONFIRMACION}</label>
          <input
            id="confirmacion-anonimizar"
            value={texto}
            autoComplete="off"
            onChange={(e) => setTexto(e.target.value)}
          />
        </div>
        {error && <p role="alert">{error}</p>}
        <div className="acciones">
          <button type="submit" disabled={!coincide || trabajando}>
            Confirmar anonimización
          </button>
          <button type="button" onClick={() => setAbierto(false)}>
            Cancelar
          </button>
        </div>
      </form>
    </div>
  );
}
