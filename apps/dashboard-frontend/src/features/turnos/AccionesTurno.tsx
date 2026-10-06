import { useState } from "react";
import {
  aprobarTurno,
  cancelarTurno,
  completarTurno,
  moverTurno,
  rechazarTurno,
  type ResultadoTurno,
  type Turno,
} from "../../api/turnos";
import { ErrorApi } from "../../api/cliente";
import { isoALocal, localAIso } from "../../shared/formato";
import { textoError } from "./errores";

interface Props {
  turno: Turno;
  /** Se llama tras un cambio confirmado por el servidor (o sin argumento si solo hay que refrescar la lista). */
  alCambiar: (r?: ResultadoTurno) => void;
}

type Modo = "mover" | "cancelar" | "rechazar" | null;

/** Acciones sobre un turno según su estado. Las que mueven o cancelan piden confirmación. */
export function AccionesTurno({ turno, alCambiar }: Props) {
  const [modo, setModo] = useState<Modo>(null);
  const [inicio, setInicio] = useState(isoALocal(turno.start_at));
  const [trabajando, setTrabajando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ejecutar(accion: () => Promise<ResultadoTurno>) {
    setTrabajando(true);
    setError(null);
    try {
      const r = await accion();
      setModo(null);
      alCambiar(r);
    } catch (e) {
      setError(textoError(e));
      // Otro usuario ya cambió el turno: se refresca la lista para mostrar su estado real.
      if (e instanceof ErrorApi && e.codigo === "estado_invalido") alCambiar();
    } finally {
      setTrabajando(false);
    }
  }

  if (turno.status === "cancelled" || turno.status === "completed") return null;

  return (
    <div className="acciones">
      {modo === null && turno.status === "pending" && (
        <>
          <button type="button" disabled={trabajando} onClick={() => void ejecutar(() => aprobarTurno(turno.id))}>
            Aprobar
          </button>{" "}
          <button type="button" disabled={trabajando} onClick={() => setModo("rechazar")}>
            Rechazar
          </button>
        </>
      )}
      {modo === null && turno.status === "confirmed" && (
        <>
          <button type="button" disabled={trabajando} onClick={() => void ejecutar(() => completarTurno(turno.id))}>
            Marcar completado
          </button>{" "}
          <button type="button" disabled={trabajando} onClick={() => setModo("mover")}>
            Mover
          </button>{" "}
          <button type="button" disabled={trabajando} onClick={() => setModo("cancelar")}>
            Cancelar turno
          </button>
        </>
      )}
      {modo === "mover" && (
        <form
          onSubmit={(ev) => {
            ev.preventDefault();
            if (inicio) void ejecutar(() => moverTurno(turno.id, localAIso(inicio)));
          }}
        >
          <label>
            Nuevo horario
            <input type="datetime-local" required value={inicio} onChange={(e) => setInicio(e.target.value)} />
          </label>{" "}
          <button type="submit" disabled={trabajando}>
            Confirmar movimiento
          </button>{" "}
          <button type="button" onClick={() => setModo(null)}>
            Volver
          </button>
        </form>
      )}
      {(modo === "cancelar" || modo === "rechazar") && (
        <span className="confirmar">
          {modo === "cancelar" ? "¿Cancelar este turno?" : "¿Rechazar esta solicitud?"} Se le avisa al cliente.{" "}
          <button
            type="button"
            disabled={trabajando}
            onClick={() => void ejecutar(() => (modo === "cancelar" ? cancelarTurno(turno.id) : rechazarTurno(turno.id)))}
          >
            {modo === "cancelar" ? "Confirmar cancelación" : "Confirmar rechazo"}
          </button>{" "}
          <button type="button" onClick={() => setModo(null)}>
            Volver
          </button>
        </span>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
