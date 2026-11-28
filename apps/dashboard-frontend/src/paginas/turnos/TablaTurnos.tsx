import type { ResultadoTurno, Turno } from "../../api/turnos";
import { formatearFechaHora } from "../../compartido/formato";
import { AccionesTurno } from "./AccionesTurno";
import { cambioVencido, horaDe, rangoLegible } from "./fechas";

export const ETIQUETA_ESTADO: Record<Turno["status"], string> = {
  pending: "Pendiente de aprobación",
  confirmed: "Confirmado",
  cancelled: "Cancelado",
  completed: "Completado",
};

interface Props {
  turnos: readonly Turno[];
  /** Muestra fecha y hora completas (listas que mezclan días) en lugar de solo la hora. */
  conFecha?: boolean;
  conProfesional?: boolean;
  puedeGestionar: boolean;
  alCambiar: (r?: ResultadoTurno, error?: string) => void;
}

/** Qué se pidió cambiar de un turno: horario actual, horario propuesto y hasta cuándo se puede resolver. */
function CambioPendiente({ turno }: { turno: Turno }) {
  const vencido = cambioVencido(turno);
  return (
    <div className={vencido ? "cambio-horario cambio-vencido" : "cambio-horario"}>
      <strong>{vencido ? "Cambio de horario vencido" : "Cambio de horario pendiente"}</strong>
      <dl>
        <dt>Horario actual</dt>
        <dd>{rangoLegible(turno.start_at, turno.end_at)}</dd>
        <dt>Horario propuesto</dt>
        <dd>{rangoLegible(turno.starts_at_propuesto ?? "", turno.ends_at_propuesto ?? "")}</dd>
        {turno.cambio_pendiente_expira_at && (
          <>
            <dt>{vencido ? "Venció" : "Vence"}</dt>
            <dd>{formatearFechaHora(turno.cambio_pendiente_expira_at)}</dd>
          </>
        )}
      </dl>
      {vencido && <small>Ya no se puede aprobar: solo rechazarlo.</small>}
    </div>
  );
}

export function TablaTurnos({ turnos, conFecha = false, conProfesional = false, puedeGestionar, alCambiar }: Props) {
  return (
    <div className="tabla-desplazable">
      <table>
        <thead>
          <tr>
            <th>{conFecha ? "Fecha y hora" : "Hora"}</th>
            <th>Cliente</th>
            <th>Servicio</th>
            {conProfesional && <th>Profesional</th>}
            <th>Estado</th>
            {puedeGestionar && <th>Acciones</th>}
          </tr>
        </thead>
        <tbody>
          {turnos.map((t) => (
            <tr key={t.id} className={t.status === "pending" ? "turno-pendiente" : undefined}>
              <td>{conFecha ? formatearFechaHora(t.start_at) : `${horaDe(t.start_at)} a ${horaDe(t.end_at)}`}</td>
              <td>{t.sin_cliente_identificable ? <em>sin cliente identificable</em> : (t.customer_name ?? "Sin nombre")}</td>
              <td>{t.service_name}</td>
              {conProfesional && <td>{t.professional_name}</td>}
              <td>
                <span className={`estado-turno estado-${t.status}`}>{ETIQUETA_ESTADO[t.status]}</span>
                {t.status === "pending" && t.pending_expires_at && (
                  <small> (vence {formatearFechaHora(t.pending_expires_at)})</small>
                )}
                {t.starts_at_propuesto !== null && t.ends_at_propuesto !== null && <CambioPendiente turno={t} />}
              </td>
              {puedeGestionar && (
                <td>
                  <AccionesTurno turno={t} alCambiar={alCambiar} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
