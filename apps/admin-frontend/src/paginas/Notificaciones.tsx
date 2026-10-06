import { Link } from "react-router-dom";
import { problemasNotificaciones } from "../api/panel";
import { ConCarga } from "../compartido/ConCarga";
import { useCarga } from "../compartido/useCarga";
import { fechaHora } from "../formato";

const cargar = () => problemasNotificaciones();

/** Notificaciones fallidas o atascadas (todos los tenants) y limpiezas de Google Calendar que fallaron. */
export function Notificaciones() {
  const { carga } = useCarga(cargar);
  return (
    <section>
      <h1>Notificaciones con problemas</h1>
      <ConCarga
        carga={carga}
        hijos={({ notificaciones, limpiezas_calendar }) => (
          <>
            <h2>Notificaciones fallidas o pendientes hace más de 1 hora</h2>
            {notificaciones.length === 0 ? (
              <p>No hay notificaciones con problemas.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Fecha (Buenos Aires)</th>
                    <th>Tenant</th>
                    <th>Canal</th>
                    <th>Estado</th>
                    <th>Reintentos</th>
                    <th>Último error</th>
                  </tr>
                </thead>
                <tbody>
                  {notificaciones.map((n) => (
                    <tr key={n.id}>
                      <td>{fechaHora(n.created_at)}</td>
                      <td>
                        <Link to={`/tenants/${n.tenant_id}`}>Ver tenant</Link>
                      </td>
                      <td>{n.channel}</td>
                      <td>{n.status === "failed" ? "Fallida" : "Pendiente"}</td>
                      <td>{n.retry_count}</td>
                      <td>{n.last_error ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h2>Limpiezas de Google Calendar fallidas</h2>
            <p>Eventos que no se pudieron anonimizar y hay que resolver a mano.</p>
            {limpiezas_calendar.length === 0 ? (
              <p>No hay limpiezas pendientes.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Fecha (Buenos Aires)</th>
                    <th>Tenant</th>
                    <th>Detalle</th>
                  </tr>
                </thead>
                <tbody>
                  {limpiezas_calendar.map((l) => (
                    <tr key={l.id}>
                      <td>{fechaHora(l.created_at)}</td>
                      <td>{l.tenant_id ? <Link to={`/tenants/${l.tenant_id}`}>Ver tenant</Link> : "—"}</td>
                      <td>
                        <code>{l.metadata ? JSON.stringify(l.metadata) : "—"}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      />
    </section>
  );
}
