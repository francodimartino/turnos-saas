import { useCallback, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import {
  asignarServicio,
  borrarProfesional,
  crearProfesional,
  listarProfesionales,
  listarServicios,
  modificarProfesional,
  quitarServicio,
  serviciosDeProfesional,
  type DatosProfesional,
  type Profesional,
} from "../../api/catalogo";
import { PanelCalendar } from "../calendar/PanelCalendar";
import { avisoDeResultado } from "../calendar/resultados";
import { BotonBorrar } from "../../shared/BotonBorrar";
import { Campo } from "../../shared/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../shared/errores";
import { Estado } from "../../shared/Estado";
import { useCarga } from "../../shared/useCarga";
import { usePermiso } from "../../shared/usePermiso";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface PropsFormulario {
  inicial: Profesional | null;
  alGuardar: () => void;
  alCancelar: () => void;
}

function FormularioProfesional({ inicial, alGuardar, alCancelar }: PropsFormulario) {
  const [nombre, setNombre] = useState(inicial?.name ?? "");
  const [usuario, setUsuario] = useState(inicial?.tenant_user_id ?? "");
  const [activo, setActivo] = useState(inicial?.is_active ?? true);
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const campos: Record<string, string> = {};
    if (nombre.trim() === "") campos["name"] = "Ingresá el nombre del profesional";
    if (usuario.trim() !== "" && !UUID.test(usuario.trim())) campos["tenant_user_id"] = "El identificador de usuario no tiene un formato válido";
    if (Object.keys(campos).length > 0) {
      setErrores({ campos, general: null });
      return;
    }
    const datos: DatosProfesional = {
      name: nombre.trim(),
      tenant_user_id: usuario.trim() === "" ? null : usuario.trim(),
      is_active: activo,
    };
    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      if (inicial) await modificarProfesional(inicial.id, datos);
      else await crearProfesional(datos);
      alGuardar();
    } catch (err) {
      setErrores(erroresDeApi(err, { reglas: [[/cuenta de staff/i, "tenant_user_id"]] }));
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" aria-label={inicial ? "Editar profesional" : "Nuevo profesional"} onSubmit={(e) => void enviar(e)} noValidate>
      <Campo etiqueta="Nombre" error={errores.campos["name"]}>
        {(a) => <input {...a} value={nombre} maxLength={200} onChange={(e) => setNombre(e.target.value)} />}
      </Campo>
      <Campo
        etiqueta="Usuario vinculado (opcional)"
        ayuda="Identificador de la cuenta de staff que atiende con este perfil. Dejalo vacío si no tiene cuenta."
        error={errores.campos["tenant_user_id"]}
      >
        {(a) => <input {...a} value={usuario} onChange={(e) => setUsuario(e.target.value)} />}
      </Campo>
      <label className="casilla">
        <input type="checkbox" checked={activo} onChange={(e) => setActivo(e.target.checked)} />
        Activo (recibe turnos)
      </label>
      {errores.general && <p role="alert">{errores.general}</p>}
      <div className="acciones">
        <button type="submit" disabled={enviando}>
          Guardar
        </button>
        <button type="button" onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}

const SIN_ASIGNAR = "ninguno";

/** Qué servicios hace un profesional y con qué modo de confirmación. `deposit` no se ofrece en el piloto. */
function ServiciosDelProfesional({ profesional, puedeEditar }: { profesional: Profesional; puedeEditar: boolean }) {
  const cargar = useCallback(
    () => Promise.all([listarServicios(), serviciosDeProfesional(profesional.id)]),
    [profesional.id],
  );
  const { carga, recargar } = useCarga(cargar);
  const [error, setError] = useState<string | null>(null);

  async function cambiar(servicioId: string, valor: string) {
    setError(null);
    try {
      if (valor === SIN_ASIGNAR) await quitarServicio(profesional.id, servicioId);
      else if (valor === "auto" || valor === "approval") await asignarServicio(profesional.id, servicioId, valor);
      recargar();
    } catch (e) {
      setError(erroresDeApi(e).general ?? "No se pudo guardar el cambio");
    }
  }

  return (
    <div className="panel" role="region" aria-label={`Servicios de ${profesional.name}`}>
      <h2>Servicios de {profesional.name}</h2>
      <Estado carga={carga} reintentar={recargar}>
        {([servicios, asignados]) =>
          servicios.length === 0 ? (
            <p>Primero cargá servicios en la sección Servicios.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Servicio</th>
                  <th>Modo de confirmación</th>
                </tr>
              </thead>
              <tbody>
                {servicios.map((s) => {
                  const actual = asignados.find((a) => a.service_id === s.id)?.confirmation_mode ?? SIN_ASIGNAR;
                  return (
                    <tr key={s.id}>
                      <td>{s.name}</td>
                      <td>
                        <select
                          aria-label={`Modo de ${s.name}`}
                          value={actual}
                          disabled={!puedeEditar}
                          onChange={(e) => void cambiar(s.id, e.target.value)}
                        >
                          <option value={SIN_ASIGNAR}>No lo hace</option>
                          <option value="auto">Confirmación automática</option>
                          <option value="approval">Requiere aprobación</option>
                          {actual === "deposit" && (
                            <option value="deposit" disabled>
                              Con seña (no disponible)
                            </option>
                          )}
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        }
      </Estado>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

export function PaginaProfesionales() {
  const puedeEditar = usePermiso("manage_staff");
  const { carga, recargar } = useCarga(listarProfesionales);
  const [editando, setEditando] = useState<Profesional | "nuevo" | null>(null);
  const [verServicios, setVerServicios] = useState<Profesional | null>(null);
  const [verCalendar, setVerCalendar] = useState<Profesional | null>(null);
  // Vuelta de Google (redirect del backend o página de callback): `?google_calendar=<resultado>&profesional=<id>`.
  const [consulta, setConsulta] = useSearchParams();
  const resultadoCalendar = consulta.get("google_calendar");
  const profesionalDeVuelta = consulta.get("profesional");
  const avisoCalendar = resultadoCalendar === null ? null : avisoDeResultado(resultadoCalendar);

  function cerrarAviso() {
    setConsulta({}, { replace: true });
  }

  function guardado() {
    setEditando(null);
    recargar();
  }

  return (
    <section>
      <h1>Profesionales</h1>
      {!puedeEditar && <p>Solo podés ver los profesionales: modificarlos requiere un permiso que tu usuario no tiene.</p>}
      {avisoCalendar && (
        <div role={avisoCalendar.tipo === "exito" ? "status" : "alert"} className="aviso">
          {avisoCalendar.texto}{" "}
          <button type="button" onClick={cerrarAviso}>
            Cerrar aviso
          </button>
        </div>
      )}
      <Estado carga={carga} reintentar={recargar}>
        {(profesionales) => (
          <>
            {profesionales.length === 0 ? (
              <p>Todavía no cargaste profesionales.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Nombre</th>
                    <th>Usuario</th>
                    <th>Estado</th>
                    <th>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {profesionales.map((p) => (
                    <tr key={p.id}>
                      <td>{p.name}</td>
                      <td>{p.tenant_user_id ? "Vinculado" : "Sin usuario"}</td>
                      <td>{p.is_active ? "Activo" : "Inactivo"}</td>
                      <td>
                        <button type="button" onClick={() => setVerServicios(p)}>
                          Servicios de {p.name}
                        </button>{" "}
                        <button type="button" onClick={() => setVerCalendar(p)}>
                          Google Calendar de {p.name}
                        </button>
                        {puedeEditar && (
                          <>
                            {" "}
                            <button type="button" onClick={() => setEditando(p)}>
                              Editar {p.name}
                            </button>{" "}
                            <BotonBorrar
                              nombre={p.name}
                              borrar={() => borrarProfesional(p.id)}
                              alBorrar={() => {
                                setVerServicios((v) => (v?.id === p.id ? null : v));
                                setVerCalendar((v) => (v?.id === p.id ? null : v));
                                recargar();
                              }}
                            />
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {puedeEditar && editando === null && (
              <button type="button" onClick={() => setEditando("nuevo")}>
                Nuevo profesional
              </button>
            )}
            {puedeEditar && editando !== null && (
              <FormularioProfesional
                key={editando === "nuevo" ? "nuevo" : editando.id}
                inicial={editando === "nuevo" ? null : editando}
                alGuardar={guardado}
                alCancelar={() => setEditando(null)}
              />
            )}
            {(() => {
              const calendar = verCalendar ?? profesionales.find((p) => p.id === profesionalDeVuelta) ?? null;
              return calendar && <PanelCalendar key={calendar.id} profesional={calendar} />;
            })()}
            {verServicios && <ServiciosDelProfesional key={verServicios.id} profesional={verServicios} puedeEditar={puedeEditar} />}
          </>
        )}
      </Estado>
    </section>
  );
}
