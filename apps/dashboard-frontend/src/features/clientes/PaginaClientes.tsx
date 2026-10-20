import { useCallback, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { detalleCliente, LIMITE_CLIENTES, listarClientes, type Cliente, type ResultadoAnonimizar } from "../../api/clientes";
import { cargarCamposExtra, type CampoExtra } from "../../shared/camposExtra";
import { Estado } from "../../shared/Estado";
import { formatearFechaHora } from "../../shared/formato";
import { useCarga } from "../../shared/useCarga";
import { usePermiso } from "../../shared/usePermiso";
import { AnonimizarCliente, AvisoAnonimizado } from "./AnonimizarCliente";
import { FormularioCliente } from "./FormularioCliente";

function nombreVisible(c: Pick<Cliente, "sin_cliente_identificable" | "name">): string {
  return c.sin_cliente_identificable ? "Cliente anonimizado" : (c.name ?? "Sin nombre");
}

interface PropsDetalle {
  id: string;
  camposExtra: readonly CampoExtra[];
  puedeEditar: boolean;
  puedeAnonimizar: boolean;
  alCambiar: () => void;
  alCerrar: () => void;
}

function DetalleCliente({ id, camposExtra, puedeEditar, puedeAnonimizar, alCambiar, alCerrar }: PropsDetalle) {
  const cargar = useCallback(() => detalleCliente(id), [id]);
  const { carga, recargar } = useCarga(cargar);
  const [editando, setEditando] = useState(false);
  const [anonimizado, setAnonimizado] = useState<ResultadoAnonimizar | null>(null);

  return (
    <div className="panel" role="region" aria-label="Detalle del cliente">
      <Estado carga={carga} reintentar={recargar}>
        {(c) => (
          <>
            <h2>{nombreVisible(c)}</h2>
            {c.sin_cliente_identificable ? (
              <p>
                Este cliente fue anonimizado
                {c.deleted_at ? ` el ${formatearFechaHora(c.deleted_at)}` : ""}: no quedan datos que lo identifiquen.
              </p>
            ) : (
              <>
                <dl>
                  <dt>Teléfono</dt>
                  <dd>{c.phone ?? "-"}</dd>
                  {camposExtra.map((x) => (
                    <div key={x.key}>
                      <dt>{x.label}</dt>
                      <dd>{String(c.extra_data?.[x.key] ?? "-")}</dd>
                    </div>
                  ))}
                </dl>
                {editando ? (
                  <FormularioCliente
                    inicial={c}
                    camposExtra={camposExtra}
                    alGuardar={() => {
                      setEditando(false);
                      recargar();
                      alCambiar();
                    }}
                    alCancelar={() => setEditando(false)}
                    alVerExistente={() => setEditando(false)}
                  />
                ) : (
                  <div className="acciones">
                    {puedeEditar && (
                      <button type="button" onClick={() => setEditando(true)}>
                        Editar cliente
                      </button>
                    )}
                    {puedeAnonimizar && (
                      <AnonimizarCliente
                        cliente={c}
                        alAnonimizar={(r) => {
                          setAnonimizado(r);
                          recargar();
                          alCambiar();
                        }}
                      />
                    )}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </Estado>
      {anonimizado && <AvisoAnonimizado resultado={anonimizado} />}
      <button type="button" onClick={alCerrar}>
        Cerrar detalle
      </button>
    </div>
  );
}

export function PaginaClientes() {
  const puedeEditar = usePermiso("manage_customers");
  const puedeAnonimizar = usePermiso("delete_customers");
  const [consulta, setConsulta] = useSearchParams();
  const seleccionado = consulta.get("cliente");

  const [escrito, setEscrito] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [creando, setCreando] = useState(false);
  // Los campos accesorios los define cada local en sus ajustes; si no se pueden leer, no se ofrecen.
  const { carga: cargaExtras } = useCarga(cargarCamposExtra);
  const camposExtra = cargaExtras.estado === "lista" ? cargaExtras.datos : [];

  // `version` no se usa dentro: hace que `cargar` cambie y la lista se vuelva a pedir tras cada alta o edición.
  const cargar = useCallback(() => {
    void version;
    return listarClientes(busqueda, offset);
  }, [busqueda, offset, version]);
  const { carga, recargar } = useCarga(cargar);

  function buscar(e: FormEvent) {
    e.preventDefault();
    setOffset(0);
    setBusqueda(escrito);
  }

  function seleccionar(id: string | null) {
    setConsulta(id === null ? {} : { cliente: id }, { replace: true });
  }

  return (
    <section>
      <h1>Clientes</h1>
      {!puedeEditar && <p>Solo podés ver los clientes: modificarlos requiere un permiso que tu usuario no tiene.</p>}
      <form className="filtros" role="search" onSubmit={buscar}>
        <label>
          Buscar por nombre o teléfono
          <input value={escrito} maxLength={100} onChange={(e) => setEscrito(e.target.value)} />
        </label>
        <button type="submit">Buscar</button>
      </form>
      <Estado carga={carga} reintentar={recargar}>
        {({ clientes }) => (
          <>
            {clientes.length === 0 ? (
              <p>{busqueda === "" ? "Todavía no hay clientes." : "No se encontraron clientes con esa búsqueda."}</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Nombre</th>
                    <th>Teléfono</th>
                    <th>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {clientes.map((c) => (
                    <tr key={c.id}>
                      <td>{nombreVisible(c)}</td>
                      <td>{c.phone ?? "-"}</td>
                      <td>
                        <button type="button" onClick={() => seleccionar(c.id)}>
                          Ver {nombreVisible(c)}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="acciones">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - LIMITE_CLIENTES))}
              >
                Anteriores
              </button>
              <button
                type="button"
                disabled={clientes.length < LIMITE_CLIENTES}
                onClick={() => setOffset(offset + LIMITE_CLIENTES)}
              >
                Siguientes
              </button>
            </div>
          </>
        )}
      </Estado>
      {puedeEditar && !creando && (
        <button type="button" onClick={() => setCreando(true)}>
          Nuevo cliente
        </button>
      )}
      {puedeEditar && creando && (
        <FormularioCliente
          inicial={null}
          camposExtra={camposExtra}
          alGuardar={() => {
            setCreando(false);
            setVersion((v) => v + 1);
          }}
          alCancelar={() => setCreando(false)}
          alVerExistente={() => setCreando(false)}
        />
      )}
      {seleccionado && (
        <DetalleCliente
          key={seleccionado}
          id={seleccionado}
          camposExtra={camposExtra}
          puedeEditar={puedeEditar}
          puedeAnonimizar={puedeAnonimizar}
          alCambiar={() => setVersion((v) => v + 1)}
          alCerrar={() => seleccionar(null)}
        />
      )}
    </section>
  );
}
