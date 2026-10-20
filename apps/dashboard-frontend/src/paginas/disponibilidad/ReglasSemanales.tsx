import { useCallback, useState, type FormEvent } from "react";
import {
  borrarRegla,
  crearRegla,
  listarReglas,
  modificarRegla,
  type DatosRegla,
  type ReglaDisponibilidad,
} from "../../api/catalogo";
import { BotonBorrar } from "../../compartido/BotonBorrar";
import { Campo } from "../../compartido/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../compartido/errores";
import { Estado } from "../../compartido/Estado";
import { horaCorta } from "../../compartido/formato";
import { useCarga } from "../../compartido/useCarga";

/** Índice = `day_of_week` del backend (0 = domingo). */
const DIAS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"] as const;
const ORDEN_SEMANA = [1, 2, 3, 4, 5, 6, 0];

const REGLAS_ERROR = [
  [/fin|inicio/i, "end_time"],
  [/d[ií]a/i, "day_of_week"],
] as const;

interface PropsFormulario {
  profesionalId: string;
  inicial: ReglaDisponibilidad | null;
  alGuardar: () => void;
  alCancelar: () => void;
}

function FormularioRegla({ profesionalId, inicial, alGuardar, alCancelar }: PropsFormulario) {
  const [dia, setDia] = useState(String(inicial?.day_of_week ?? 1));
  const [desde, setDesde] = useState(inicial ? horaCorta(inicial.start_time) : "09:00");
  const [hasta, setHasta] = useState(inicial ? horaCorta(inicial.end_time) : "18:00");
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const campos: Record<string, string> = {};
    if (desde === "") campos["start_time"] = "Ingresá la hora de inicio";
    if (hasta === "") campos["end_time"] = "Ingresá la hora de fin";
    if (desde !== "" && hasta !== "" && hasta <= desde) campos["end_time"] = "La hora de fin debe ser posterior a la de inicio";
    if (Object.keys(campos).length > 0) {
      setErrores({ campos, general: null });
      return;
    }
    const datos: DatosRegla = {
      professional_id: profesionalId,
      day_of_week: Number(dia),
      start_time: desde,
      end_time: hasta,
    };
    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      if (inicial) await modificarRegla(inicial.id, { day_of_week: datos.day_of_week, start_time: desde, end_time: hasta });
      else await crearRegla(datos);
      alGuardar();
    } catch (err) {
      setErrores(erroresDeApi(err, { reglas: REGLAS_ERROR }));
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" aria-label={inicial ? "Editar horario semanal" : "Nuevo horario semanal"} onSubmit={(e) => void enviar(e)} noValidate>
      <Campo etiqueta="Día" error={errores.campos["day_of_week"]}>
        {(a) => (
          <select {...a} value={dia} onChange={(e) => setDia(e.target.value)}>
            {ORDEN_SEMANA.map((d) => (
              <option key={d} value={d}>
                {DIAS[d]}
              </option>
            ))}
          </select>
        )}
      </Campo>
      <Campo etiqueta="Desde" error={errores.campos["start_time"]}>
        {(a) => <input {...a} type="time" value={desde} onChange={(e) => setDesde(e.target.value)} />}
      </Campo>
      <Campo etiqueta="Hasta" error={errores.campos["end_time"]}>
        {(a) => <input {...a} type="time" value={hasta} onChange={(e) => setHasta(e.target.value)} />}
      </Campo>
      {errores.general && <p role="alert">{errores.general}</p>}
      <div className="acciones">
        <button type="submit" disabled={enviando}>
          Guardar horario
        </button>
        <button type="button" onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}

/** Reglas semanales de un profesional: en qué franjas de cada día atiende (hora local del negocio). */
export function ReglasSemanales({ profesionalId, puedeEditar }: { profesionalId: string; puedeEditar: boolean }) {
  const cargar = useCallback(() => listarReglas(profesionalId), [profesionalId]);
  const { carga, recargar } = useCarga(cargar);
  const [editando, setEditando] = useState<ReglaDisponibilidad | "nueva" | null>(null);

  function guardada() {
    setEditando(null);
    recargar();
  }

  return (
    <div role="region" aria-label="Horarios semanales">
      <h2>Horarios semanales</h2>
      <Estado carga={carga} reintentar={recargar}>
        {(reglas) => (
          <>
            {reglas.length === 0 ? (
              <p>Este profesional no tiene horarios cargados: no se le ofrecen turnos.</p>
            ) : (
              <ul>
                {ORDEN_SEMANA.flatMap((d) =>
                  reglas
                    .filter((r) => r.day_of_week === d)
                    .sort((x, y) => x.start_time.localeCompare(y.start_time))
                    .map((r) => (
                      <li key={r.id}>
                        {DIAS[d]}: {horaCorta(r.start_time)} a {horaCorta(r.end_time)}
                        {puedeEditar && (
                          <>
                            {" "}
                            <button type="button" onClick={() => setEditando(r)}>
                              Editar horario del {DIAS[d]} {horaCorta(r.start_time)}
                            </button>{" "}
                            <BotonBorrar
                              nombre={`horario del ${DIAS[d]} ${horaCorta(r.start_time)}`}
                              borrar={() => borrarRegla(r.id)}
                              alBorrar={recargar}
                            />
                          </>
                        )}
                      </li>
                    )),
                )}
              </ul>
            )}
            {puedeEditar && editando === null && (
              <button type="button" onClick={() => setEditando("nueva")}>
                Agregar horario
              </button>
            )}
            {puedeEditar && editando !== null && (
              <FormularioRegla
                key={editando === "nueva" ? "nueva" : editando.id}
                profesionalId={profesionalId}
                inicial={editando === "nueva" ? null : editando}
                alGuardar={guardada}
                alCancelar={() => setEditando(null)}
              />
            )}
          </>
        )}
      </Estado>
    </div>
  );
}
