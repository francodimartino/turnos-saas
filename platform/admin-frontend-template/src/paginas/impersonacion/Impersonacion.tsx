import { useCallback, useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  impersonarBookings,
  impersonarClientes,
  impersonarPacks,
  impersonarProfesionales,
  impersonarServicios,
  obtenerTenant,
} from "../../api/panel";
import { ConCarga } from "../../compartido/ConCarga";
import { useCarga } from "../../compartido/useCarga";
import { fechaHora, hoyBuenosAires, sumarDias } from "../../formato";

type Vista = "turnos" | "clientes" | "servicios" | "profesionales" | "packs";

const VISTAS: readonly { id: Vista; etiqueta: string }[] = [
  { id: "turnos", etiqueta: "Turnos" },
  { id: "clientes", etiqueta: "Clientes" },
  { id: "servicios", etiqueta: "Servicios" },
  { id: "profesionales", etiqueta: "Profesionales" },
  { id: "packs", etiqueta: "Packs" },
];

/**
 * Vista de impersonación (§2.11.4): lo que ve el dashboard de un tenant, **solo lectura**. Esta pantalla no tiene
 * formularios de edición ni botones de acción sobre los datos; el backend además solo expone GET para esto.
 */
export function Impersonacion() {
  const { id = "" } = useParams();
  const [vista, setVista] = useState<Vista>("turnos");
  const cargarTenant = useCallback(() => obtenerTenant(id), [id]);
  const { carga } = useCarga(cargarTenant);
  const nombre = carga.tipo === "lista" ? carga.datos.name : null;

  return (
    <section>
      <div className="banner-solo-lectura" role="status">
        <strong>Modo solo lectura</strong>
        {nombre ? ` — viendo ${nombre}.` : "."} No se puede modificar nada desde esta vista. Cada consulta queda
        registrada en la auditoría.
      </div>
      <p>
        <Link to={`/tenants/${id}`}>← Salir de la vista (ficha del tenant)</Link>
      </p>
      <h1>Vista del tenant</h1>
      <nav aria-label="Datos del tenant" className="acciones">
        {VISTAS.map((v) => (
          <button key={v.id} type="button" aria-pressed={vista === v.id} onClick={() => setVista(v.id)}>
            {v.etiqueta}
          </button>
        ))}
      </nav>
      {vista === "turnos" && <Turnos tenantId={id} />}
      {vista === "clientes" && <Clientes tenantId={id} />}
      {vista === "servicios" && <Servicios tenantId={id} />}
      {vista === "profesionales" && <Profesionales tenantId={id} />}
      {vista === "packs" && <Packs tenantId={id} />}
    </section>
  );
}

function Tabla({ columnas, hijos, vacio }: { columnas: string[]; hijos: ReactNode; vacio: boolean }) {
  if (vacio) return <p>No hay datos para mostrar.</p>;
  return (
    <table>
      <thead>
        <tr>
          {columnas.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>{hijos}</tbody>
    </table>
  );
}

const ESTADOS_TURNO: Record<string, string> = {
  pending: "Pendiente",
  confirmed: "Confirmado",
  cancelled: "Cancelado",
  completed: "Completado",
};

function Turnos({ tenantId }: { tenantId: string }) {
  const hoy = hoyBuenosAires();
  const [desde, setDesde] = useState(sumarDias(hoy, -7));
  const [hasta, setHasta] = useState(sumarDias(hoy, 30));
  const [rango, setRango] = useState({ desde: sumarDias(hoy, -7), hasta: sumarDias(hoy, 30) });
  const cargar = useCallback(
    () => impersonarBookings(tenantId, `${rango.desde}T00:00:00-03:00`, `${rango.hasta}T23:59:59-03:00`),
    [tenantId, rango],
  );
  const { carga } = useCarga(cargar);

  function aplicar(e: FormEvent) {
    e.preventDefault();
    if (desde && hasta) setRango({ desde, hasta });
  }

  return (
    <div>
      <h2>Turnos</h2>
      <form className="fila filtros" onSubmit={aplicar}>
        <label>
          Desde
          <input type="date" required value={desde} onChange={(e) => setDesde(e.target.value)} />
        </label>
        <label>
          Hasta
          <input type="date" required value={hasta} onChange={(e) => setHasta(e.target.value)} />
        </label>
        <button type="submit">Ver turnos</button>
      </form>
      <ConCarga
        carga={carga}
        hijos={(turnos) => (
          <Tabla
            columnas={["Inicio (Buenos Aires)", "Cliente", "Servicio", "Profesional", "Estado"]}
            vacio={turnos.length === 0}
            hijos={turnos.map((t) => (
              <tr key={t.id}>
                <td>{fechaHora(t.start_at)}</td>
                <td>{t.customer_name}</td>
                <td>{t.service_name}</td>
                <td>{t.professional_name}</td>
                <td>{ESTADOS_TURNO[t.status] ?? t.status}</td>
              </tr>
            ))}
          />
        )}
      />
    </div>
  );
}

function Clientes({ tenantId }: { tenantId: string }) {
  const cargar = useCallback(() => impersonarClientes(tenantId), [tenantId]);
  const { carga } = useCarga(cargar);
  return (
    <div>
      <h2>Clientes</h2>
      <ConCarga
        carga={carga}
        hijos={(clientes) => (
          <Tabla
            columnas={["Nombre", "Teléfono", "Canal"]}
            vacio={clientes.length === 0}
            hijos={clientes.map((c) => (
              <tr key={c.id}>
                <td>{c.deleted_at ? "(anonimizado)" : (c.name ?? "—")}</td>
                <td>{c.phone ?? "—"}</td>
                <td>{c.channel}</td>
              </tr>
            ))}
          />
        )}
      />
    </div>
  );
}

function Servicios({ tenantId }: { tenantId: string }) {
  const cargar = useCallback(() => impersonarServicios(tenantId), [tenantId]);
  const { carga } = useCarga(cargar);
  return (
    <div>
      <h2>Servicios</h2>
      <ConCarga
        carga={carga}
        hijos={(servicios) => (
          <Tabla
            columnas={["Nombre", "Duración (min)", "Precio"]}
            vacio={servicios.length === 0}
            hijos={servicios.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td>{s.duration_minutes}</td>
                <td>{s.price ?? "—"}</td>
              </tr>
            ))}
          />
        )}
      />
    </div>
  );
}

function Profesionales({ tenantId }: { tenantId: string }) {
  const cargar = useCallback(() => impersonarProfesionales(tenantId), [tenantId]);
  const { carga } = useCarga(cargar);
  return (
    <div>
      <h2>Profesionales</h2>
      <ConCarga
        carga={carga}
        hijos={(profesionales) => (
          <Tabla
            columnas={["Nombre", "Activo"]}
            vacio={profesionales.length === 0}
            hijos={profesionales.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td>{p.is_active ? "Sí" : "No"}</td>
              </tr>
            ))}
          />
        )}
      />
    </div>
  );
}

function Packs({ tenantId }: { tenantId: string }) {
  const cargar = useCallback(() => impersonarPacks(tenantId), [tenantId]);
  const { carga } = useCarga(cargar);
  return (
    <div>
      <h2>Packs</h2>
      <ConCarga
        carga={carga}
        hijos={(packs) => (
          <Tabla
            columnas={["Sesiones usadas", "Pago", "Vence (Buenos Aires)"]}
            vacio={packs.length === 0}
            hijos={packs.map((p) => (
              <tr key={p.id}>
                <td>
                  {p.used_sessions} de {p.total_sessions}
                </td>
                <td>{p.payment_status}</td>
                <td>{fechaHora(p.expires_at)}</td>
              </tr>
            ))}
          />
        )}
      />
    </div>
  );
}
