import type { ResultadoTurno, Turno } from "../../api/turnos";
import { formatearFechaHora } from "../../compartido/formato";
import { AccionesTurno } from "./AccionesTurno";
import { horaDe } from "./fechas";

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
  alCambiar: (r?: ResultadoTurno) => void;
}

export function TablaTurnos({ turnos, conFecha = false, conProfesional = false, puedeGestionar, alCambiar }: Props) {
  return (
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
  );
}
