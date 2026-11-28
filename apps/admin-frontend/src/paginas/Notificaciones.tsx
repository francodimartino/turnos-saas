import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { problemasNotificaciones, type CanalNotificacion } from "../api/panel";
import { ConCarga } from "../compartido/ConCarga";
import { useCarga } from "../compartido/useCarga";
import { fechaHora } from "../formato";
import { DescartarLimpieza } from "./DescartarLimpieza";

const esPendiente = (accion: string) => accion === "calendar_scrub_failed" || accion === "calendar_scrub_retry_failed";

const CANALES: Record<CanalNotificacion, string> = { telegram: "Telegram", whatsapp: "WhatsApp", email: "Email" };

/** Etiqueta legible del canal; un canal que el panel no conoce se muestra tal cual, sin romper la fila. */
export function etiquetaCanal(canal: string): string {
  return canal in CANALES ? CANALES[canal as CanalNotificacion] : canal;
}

function esCanal(valor: string): valor is CanalNotificacion {
  return valor in CANALES;
}

/** El backend manda una entrada por evento (su fila más nueva): la acción dice en qué quedó. */
const ESTADO_LIMPIEZA: Record<string, string> = {
  calendar_scrub_failed: "Sin resolver",
  calendar_scrub_retry_failed: "Reintento fallido",
  calendar_scrub_resolved: "Resuelta",
  calendar_scrub_discarded: "Descartada",
};

/** Notificaciones fallidas o atascadas (todos los tenants) y limpiezas de Google Calendar que fallaron. */
export function Notificaciones() {
  const [canal, setCanal] = useState<CanalNotificacion | "">("");
  const cargar = useCallback(() => problemasNotificaciones(canal === "" ? undefined : canal), [canal]);
  const { carga } = useCarga(cargar);
  const [descartadas, setDescartadas] = useState<ReadonlySet<string>>(new Set());
  return (
    <section>
      <h1>Notificaciones con problemas</h1>
      <label className="campo">
        Canal
        <select value={canal} onChange={(e) => setCanal(esCanal(e.target.value) ? e.target.value : "")}>
          <option value="">Todos</option>
          {Object.entries(CANALES).map(([valor, etiqueta]) => (
            <option key={valor} value={valor}>
              {etiqueta}
            </option>
          ))}
        </select>
        <small>Filtra las notificaciones; las limpiezas de Calendar no dependen del canal.</small>
      </label>
      <ConCarga
        carga={carga}
        hijos={({ notificaciones, limpiezas_calendar: todas }) => {
          const limpiezas_calendar = todas.filter((l) => !descartadas.has(l.id));
          return (
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
                      <td>{etiquetaCanal(n.channel)}</td>
                      <td>{n.status === "failed" ? "Fallida" : "Pendiente"}</td>
                      <td>{n.retry_count}</td>
                      <td>{n.last_error ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h2>Limpiezas de Google Calendar fallidas</h2>
            <p>
              Eventos que no se pudieron anonimizar. El job diario los reintenta; cada evento queda sin resolver hasta
              que una fila lo marca como resuelto.
            </p>
            {limpiezas_calendar.length === 0 ? (
              <p>No hay limpiezas pendientes.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Fecha (Buenos Aires)</th>
                    <th>Estado</th>
                    <th>Tenant</th>
                    <th>Detalle</th>
                    <th>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {limpiezas_calendar.map((l) => (
                    <tr key={l.id}>
                      <td>{fechaHora(l.created_at)}</td>
                      <td>
                        {ESTADO_LIMPIEZA[l.action] ?? l.action}
                        {typeof l.metadata?.intentos === "number" && l.action !== "calendar_scrub_resolved" && l.action !== "calendar_scrub_discarded"
                          ? ` (${l.metadata.intentos} intentos)`
                          : ""}
                      </td>
                      <td>{l.tenant_id ? <Link to={`/tenants/${l.tenant_id}`}>Ver tenant</Link> : "—"}</td>
                      <td>
                        <code>{l.metadata ? JSON.stringify(l.metadata) : "—"}</code>
                      </td>
                      <td>
                        {esPendiente(l.action) && l.tenant_id && typeof l.metadata?.event_id === "string" ? (
                          <DescartarLimpieza
                            tenantId={l.tenant_id}
                            eventId={l.metadata.event_id}
                            alDescartar={() => setDescartadas((prev) => new Set(prev).add(l.id))}
                          />
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
          );
        }}
      />
    </section>
  );
}
