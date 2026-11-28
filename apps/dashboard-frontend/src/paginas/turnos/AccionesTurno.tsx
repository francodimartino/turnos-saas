import { useRef, useState } from "react";
import {
  aprobarCambioTurno,
  aprobarTurno,
  cancelarTurno,
  completarTurno,
  moverTurno,
  rechazarCambioTurno,
  rechazarTurno,
  type ResultadoTurno,
  type Turno,
} from "../../api/turnos";
import { ErrorApi } from "../../api/cliente";
import { isoALocal, localAIso } from "../../compartido/formato";
import { textoError } from "./errores";
import { cambioVencido, rangoLegible } from "./fechas";

/** Conflictos de un cambio de horario tras los cuales la lista ya no refleja la realidad: se vuelve a pedir. */
const CODIGOS_CAMBIO_DESACTUALIZADO = ["cambio_no_pendiente", "cambio_vencido"];

interface Props {
  turno: Turno;
  /** Se llama tras un cambio confirmado por el servidor (o sin argumento si solo hay que refrescar la lista). */
  alCambiar: (r?: ResultadoTurno, error?: string) => void;
}

type Modo = "mover" | "cancelar" | "rechazar" | "aprobar_cambio" | "rechazar_cambio" | null;

/** Acciones sobre un turno según su estado. Las que mueven o cancelan piden confirmación. */
export function AccionesTurno({ turno, alCambiar }: Props) {
  const [modo, setModo] = useState<Modo>(null);
  const [inicio, setInicio] = useState(isoALocal(turno.start_at));
  const [trabajando, setTrabajando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Evita el doble envío aunque el segundo clic llegue antes de que React vuelva a dibujar los botones.
  const enVuelo = useRef(false);

  async function ejecutar(accion: () => Promise<ResultadoTurno>) {
    if (enVuelo.current) return;
    enVuelo.current = true;
    setTrabajando(true);
    setError(null);
    try {
      const r = await accion();
      setModo(null);
      alCambiar(r);
    } catch (e) {
      if (e instanceof ErrorApi && CODIGOS_CAMBIO_DESACTUALIZADO.includes(e.codigo)) {
        // El cambio ya se resolvió o venció: se refresca la lista y el mensaje queda arriba, porque la fila puede desaparecer.
        setModo(null);
        alCambiar(undefined, textoError(e));
      } else {
        setError(textoError(e));
        // Otro usuario ya cambió el turno: se refresca la lista para mostrar su estado real.
        if (e instanceof ErrorApi && e.codigo === "estado_invalido") alCambiar();
      }
    } finally {
      enVuelo.current = false;
      setTrabajando(false);
    }
  }

  if (turno.status === "cancelled" || turno.status === "completed") return null;

  const hayCambio = turno.starts_at_propuesto !== null && turno.ends_at_propuesto !== null;
  const vencido = hayCambio && cambioVencido(turno);

  return (
    <div className="acciones">
      {modo === null && hayCambio && (
        <p className="acciones-cambio">
          {!vencido && (
            <>
              <button type="button" disabled={trabajando} onClick={() => setModo("aprobar_cambio")}>
                Aprobar cambio de horario
              </button>{" "}
            </>
          )}
          <button type="button" disabled={trabajando} onClick={() => setModo("rechazar_cambio")}>
            Rechazar cambio de horario
          </button>
        </p>
      )}
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
          <button type="button" disabled={trabajando} onClick={() => setModo(null)}>
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
          <button type="button" disabled={trabajando} onClick={() => setModo(null)}>
            Volver
          </button>
        </span>
      )}
      {(modo === "aprobar_cambio" || modo === "rechazar_cambio") && hayCambio && (
        <span className="confirmar">
          {modo === "aprobar_cambio"
            ? `¿Aprobar el cambio? El turno pasa a ${rangoLegible(turno.starts_at_propuesto ?? "", turno.ends_at_propuesto ?? "")}.`
            : "¿Rechazar el cambio? El turno conserva su horario actual."}{" "}
          Se le avisa al cliente.{" "}
          <button
            type="button"
            disabled={trabajando}
            onClick={() =>
              void ejecutar(() => (modo === "aprobar_cambio" ? aprobarCambioTurno(turno.id) : rechazarCambioTurno(turno.id)))
            }
          >
            {modo === "aprobar_cambio" ? "Confirmar aprobación del cambio" : "Confirmar rechazo del cambio"}
          </button>{" "}
          <button type="button" disabled={trabajando} onClick={() => setModo(null)}>
            Volver
          </button>
        </span>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
