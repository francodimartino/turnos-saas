import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { listarTenants } from "../../api/panel";
import { ConCarga } from "../../compartido/ConCarga";
import { EstadoTenantBadge, ETIQUETAS_ESTADO } from "../../compartido/EstadoTenant";
import { useCarga } from "../../compartido/useCarga";
import { fechaCalendario, fechaHora } from "../../formato";

export function ListaTenants() {
  const [estado, setEstado] = useState("");
  const cargar = useCallback(() => listarTenants(estado || undefined), [estado]);
  const { carga } = useCarga(cargar);

  return (
    <section>
      <h1>Tenants</h1>
      <div className="fila">
        <label>
          Estado
          <select value={estado} onChange={(e) => setEstado(e.target.value)}>
            <option value="">Todos</option>
            {Object.entries(ETIQUETAS_ESTADO).map(([valor, etiqueta]) => (
              <option key={valor} value={valor}>
                {etiqueta}
              </option>
            ))}
          </select>
        </label>
        <Link to="/tenants/nuevo">Nuevo tenant</Link>
      </div>
      <ConCarga
        carga={carga}
        hijos={(tenants) =>
          tenants.length === 0 ? (
            <p>No hay tenants para mostrar.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Nombre</th>
                  <th>Slug</th>
                  <th>Owner</th>
                  <th>Estado</th>
                  <th>Pagado hasta</th>
                  <th>Alta</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <Link to={`/tenants/${t.id}`}>{t.name}</Link>
                      {t.invitacion_pendiente && <span className="badge badge-aviso"> Invitación pendiente</span>}
                    </td>
                    <td>{t.slug}</td>
                    <td>
                      {t.owner_nombre ?? "Sin nombre"}
                      {t.owner_email && (
                        <>
                          <br />
                          <small>{t.owner_email}</small>
                        </>
                      )}
                    </td>
                    <td>
                      <EstadoTenantBadge estado={t.status} />
                    </td>
                    <td>{fechaCalendario(t.paid_until)}</td>
                    <td>{fechaHora(t.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      />
    </section>
  );
}
