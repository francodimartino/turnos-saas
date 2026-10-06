import { useCallback, useState, type FormEvent } from "react";
import {
  borrarBloqueo,
  crearBloqueo,
  listarBloqueos,
  modificarBloqueo,
  type Bloqueo,
  type DatosBloqueo,
  type Profesional,
} from "../../api/catalogo";
import { BotonBorrar } from "../../shared/BotonBorrar";
import { Campo } from "../../shared/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../shared/errores";
import { Estado } from "../../shared/Estado";
import { formatearFechaHora, isoALocal, localAIso } from "../../shared/formato";
import { useCarga } from "../../shared/useCarga";

const TODO_EL_LOCAL = "local";

interface PropsFormulario {
  profesionales: readonly Profesional[];
  inicial: Bloqueo | null;
  alGuardar: () => void;
  alCancelar: () => void;
}

function FormularioBloqueo({ profesionales, inicial, alGuardar, alCancelar }: PropsFormulario) {
  const [alcance, setAlcance] = useState(inicial?.professional_id ?? TODO_EL_LOCAL);
  const [desde, setDesde] = useState(inicial ? isoALocal(inicial.start_at) : "");
  const [hasta, setHasta] = useState(inicial ? isoALocal(inicial.end_at) : "");
  const [motivo, setMotivo] = useState(inicial?.reason ?? "");
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const campos: Record<string, string> = {};
    if (desde === "") campos["start_at"] = "Ingresá cuándo empieza el bloqueo";
    if (hasta === "") campos["end_at"] = "Ingresá cuándo termina el bloqueo";
    if (desde !== "" && hasta !== "" && hasta <= desde) campos["end_at"] = "El fin debe ser posterior al inicio";
    if (Object.keys(campos).length > 0) {
      setErrores({ campos, general: null });
      return;
    }
    const base = { start_at: localAIso(desde), end_at: localAIso(hasta), reason: motivo.trim() === "" ? null : motivo.trim() };
    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      if (inicial) {
        // El alcance (profesional o todo el local) no es editable: para cambiarlo se borra y se crea otro.
        await modificarBloqueo(inicial.id, base);
      } else {
        const datos: DatosBloqueo = { professional_id: alcance === TODO_EL_LOCAL ? null : alcance, ...base };
        await crearBloqueo(datos);
      }
      alGuardar();
    } catch (err) {
      setErrores(erroresDeApi(err, { porDefecto: "end_at" }));
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" aria-label={inicial ? "Editar bloqueo" : "Nuevo bloqueo"} onSubmit={(e) => void enviar(e)} noValidate>
      <Campo etiqueta="Aplica a" error={errores.campos["professional_id"]}>
        {(a) => (
          <select {...a} value={alcance} disabled={inicial !== null} onChange={(e) => setAlcance(e.target.value)}>
            <option value={TODO_EL_LOCAL}>Todo el local</option>
            {profesionales.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
      </Campo>
      <Campo etiqueta="Desde (hora de Buenos Aires)" error={errores.campos["start_at"]}>
        {(a) => <input {...a} type="datetime-local" value={desde} onChange={(e) => setDesde(e.target.value)} />}
      </Campo>
      <Campo etiqueta="Hasta (hora de Buenos Aires)" error={errores.campos["end_at"]}>
        {(a) => <input {...a} type="datetime-local" value={hasta} onChange={(e) => setHasta(e.target.value)} />}
      </Campo>
      <Campo etiqueta="Motivo (opcional)" error={errores.campos["reason"]}>
        {(a) => <input {...a} value={motivo} maxLength={500} onChange={(e) => setMotivo(e.target.value)} />}
      </Campo>
      {errores.general && <p role="alert">{errores.general}</p>}
      <div className="acciones">
        <button type="submit" disabled={enviando}>
          Guardar bloqueo
        </button>
        <button type="button" onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}

/** Bloqueos vigentes o futuros (feriados, vacaciones): de un profesional o de todo el local. */
export function Bloqueos({ profesionales, puedeEditar }: { profesionales: readonly Profesional[]; puedeEditar: boolean }) {
  // Se fija al montar: recalcular `ahora` en cada render cambiaría `cargar` y recargaría en bucle.
  const [ahora] = useState(() => new Date());
  const cargar = useCallback(() => listarBloqueos(ahora), [ahora]);
  const { carga, recargar } = useCarga(cargar);
  const [editando, setEditando] = useState<Bloqueo | "nuevo" | null>(null);

  function guardado() {
    setEditando(null);
    recargar();
  }

  const nombreDe = (id: string | null) =>
    id === null ? "Todo el local" : (profesionales.find((p) => p.id === id)?.name ?? "Profesional");

  return (
    <div role="region" aria-label="Bloqueos">
      <h2>Bloqueos (feriados y vacaciones)</h2>
      <Estado carga={carga} reintentar={recargar}>
        {(bloqueos) => (
          <>
            {bloqueos.length === 0 ? (
              <p>No hay bloqueos cargados.</p>
            ) : (
              <ul>
                {bloqueos.map((b) => {
                  const rotulo = `${formatearFechaHora(b.start_at)} a ${formatearFechaHora(b.end_at)}`;
                  return (
                    <li key={b.id}>
                      {nombreDe(b.professional_id)}: {rotulo}
                      {b.reason ? ` (${b.reason})` : ""}
                      {puedeEditar && (
                        <>
                          {" "}
                          <button type="button" onClick={() => setEditando(b)}>
                            Editar bloqueo {rotulo}
                          </button>{" "}
                          <BotonBorrar nombre={`bloqueo ${rotulo}`} borrar={() => borrarBloqueo(b.id)} alBorrar={recargar} />
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            {puedeEditar && editando === null && (
              <button type="button" onClick={() => setEditando("nuevo")}>
                Agregar bloqueo
              </button>
            )}
            {puedeEditar && editando !== null && (
              <FormularioBloqueo
                key={editando === "nuevo" ? "nuevo" : editando.id}
                profesionales={profesionales}
                inicial={editando === "nuevo" ? null : editando}
                alGuardar={guardado}
                alCancelar={() => setEditando(null)}
              />
            )}
          </>
        )}
      </Estado>
    </div>
  );
}
