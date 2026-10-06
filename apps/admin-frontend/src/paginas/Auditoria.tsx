import { useCallback, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { listarAudit, type FiltrosAudit } from "../api/panel";
import { ConCarga } from "../compartido/ConCarga";
import { useCarga } from "../compartido/useCarga";
import { fechaHora, localABuenosAires } from "../formato";

/** Visor de `audit_log` de un tenant. Las fechas de los filtros son hora de Buenos Aires. */
export function Auditoria() {
  const { id = "" } = useParams();
  const [accion, setAccion] = useState("");
  const [actor, setActor] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [filtros, setFiltros] = useState<FiltrosAudit>({});
  const cargar = useCallback(() => listarAudit(id, filtros), [id, filtros]);
  const { carga } = useCarga(cargar);

  function filtrar(e: FormEvent) {
    e.preventDefault();
    setFiltros({
      action: accion.trim() || undefined,
      actor_type: actor || undefined,
      desde: localABuenosAires(desde),
      hasta: localABuenosAires(hasta),
    });
  }

  return (
    <section>
      <p>
        <Link to={`/tenants/${id}`}>← Ficha del tenant</Link>
      </p>
      <h1>Auditoría</h1>
      <form className="fila filtros" onSubmit={filtrar}>
        <label>
          Acción
          <input value={accion} onChange={(e) => setAccion(e.target.value)} />
        </label>
        <label>
          Actor
          <select value={actor} onChange={(e) => setActor(e.target.value)}>
            <option value="">Todos</option>
            <option value="platform_admin">Administrador de la plataforma</option>
            <option value="staff">Personal del tenant</option>
            <option value="bot">Bot</option>
            <option value="system">Sistema</option>
          </select>
        </label>
        <label>
          Desde (hora de Buenos Aires)
          <input type="datetime-local" value={desde} onChange={(e) => setDesde(e.target.value)} />
        </label>
        <label>
          Hasta (hora de Buenos Aires)
          <input type="datetime-local" value={hasta} onChange={(e) => setHasta(e.target.value)} />
        </label>
        <button type="submit">Filtrar</button>
      </form>
      <ConCarga
        carga={carga}
        hijos={(entradas) =>
          entradas.length === 0 ? (
            <p>No hay registros para esos filtros.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Fecha (Buenos Aires)</th>
                  <th>Acción</th>
                  <th>Actor</th>
                  <th>Entidad</th>
                  <th>Detalle</th>
                </tr>
              </thead>
              <tbody>
                {entradas.map((en) => (
                  <tr key={en.id}>
                    <td>{fechaHora(en.created_at)}</td>
                    <td>{en.action}</td>
                    <td>{en.actor_type}</td>
                    <td>{en.entity ?? "—"}</td>
                    <td>
                      <code>{en.metadata ? JSON.stringify(en.metadata) : "—"}</code>
                    </td>
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
