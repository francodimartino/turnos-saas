import { useCallback, useState } from "react";
import { ESTADOS_TURNO, listarTurnos, LIMITE_TURNOS, type EstadoTurno, type ResultadoTurno, type Turno } from "../../api/turnos";
import type { Profesional } from "../../api/catalogo";
import { Estado } from "../../shared/Estado";
import { useCarga } from "../../shared/useCarga";
import { diaDe, etiquetaDia, hoy, rango, sumarDias, type Vista } from "./fechas";
import { ETIQUETA_ESTADO, TablaTurnos } from "./TablaTurnos";

interface Props {
  profesionales: readonly Profesional[];
  puedeGestionar: boolean;
  version: number;
  alCambiar: (r?: ResultadoTurno) => void;
}

/** Turnos agrupados por día y, dentro de cada día, por profesional. */
function agrupar(turnos: readonly Turno[], diasDelRango: readonly string[]) {
  // Un turno que arranca antes del rango pero lo pisa se muestra bajo su día de inicio.
  const dias = [...new Set([...diasDelRango, ...turnos.map((t) => diaDe(t.start_at))])].sort();
  return dias.map((dia) => {
    const porProfesional = new Map<string, { nombre: string; turnos: Turno[] }>();
    for (const t of turnos.filter((x) => diaDe(x.start_at) === dia)) {
      const grupo = porProfesional.get(t.professional_id) ?? { nombre: t.professional_name, turnos: [] };
      grupo.turnos.push(t);
      porProfesional.set(t.professional_id, grupo);
    }
    const grupos = [...porProfesional.entries()]
      .map(([id, g]) => ({ id, ...g }))
      .sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
    return { dia, grupos };
  });
}

export function Agenda({ profesionales, puedeGestionar, version, alCambiar }: Props) {
  const [dia, setDia] = useState(hoy);
  const [vista, setVista] = useState<Vista>("dia");
  const [profesionalId, setProfesionalId] = useState("");
  const [estado, setEstado] = useState<EstadoTurno | "">("");
  const { desde, hasta, dias } = rango(dia, vista);

  const cargar = useCallback(
    () =>
      listarTurnos({
        desde,
        hasta,
        ...(estado ? { estados: [estado] } : {}),
        ...(profesionalId ? { profesionalId } : {}),
      }),
    // `version` fuerza la recarga tras un cambio hecho en otra parte de la pantalla.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [desde, hasta, estado, profesionalId, version],
  );
  const { carga, recargar } = useCarga(cargar);
  const paso = vista === "dia" ? 1 : 7;

  return (
    <section aria-label="Agenda">
      <h2>Agenda</h2>
      <div className="filtros">
        <button type="button" onClick={() => setDia(sumarDias(dia, -paso))}>
          Anterior
        </button>
        <button type="button" onClick={() => setDia(hoy())}>
          Hoy
        </button>
        <button type="button" onClick={() => setDia(sumarDias(dia, paso))}>
          Siguiente
        </button>
        <label>
          Fecha
          <input type="date" value={dia} onChange={(e) => e.target.value && setDia(e.target.value)} />
        </label>
        <label>
          Vista
          <select value={vista} onChange={(e) => setVista(e.target.value as Vista)}>
            <option value="dia">Día</option>
            <option value="semana">Semana</option>
          </select>
        </label>
        <label>
          Profesional
          <select value={profesionalId} onChange={(e) => setProfesionalId(e.target.value)}>
            <option value="">Todos</option>
            {profesionales.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Estado
          <select value={estado} onChange={(e) => setEstado(e.target.value as EstadoTurno | "")}>
            <option value="">Todos</option>
            {ESTADOS_TURNO.map((e) => (
              <option key={e} value={e}>
                {ETIQUETA_ESTADO[e]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Estado carga={carga} reintentar={recargar}>
        {(turnos) => (
          <>
            {turnos.length >= LIMITE_TURNOS && (
              <p>Se muestran los primeros {LIMITE_TURNOS} turnos: acotá el filtro para ver el resto.</p>
            )}
            {agrupar(turnos, dias).map(({ dia: d, grupos }) => (
              <div key={d} className="dia-agenda">
                <h3>{etiquetaDia(d)}</h3>
                {grupos.length === 0 && <p>Sin turnos.</p>}
                {grupos.map((g) => (
                  <div key={g.id}>
                    <h4>{g.nombre}</h4>
                    <TablaTurnos turnos={g.turnos} puedeGestionar={puedeGestionar} alCambiar={alCambiar} />
                  </div>
                ))}
              </div>
            ))}
          </>
        )}
      </Estado>
    </section>
  );
}
