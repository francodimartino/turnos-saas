import { useCallback } from "react";
import { listarTurnos, LIMITE_TURNOS, type ResultadoTurno } from "../../api/turnos";
import { localAIso } from "../../compartido/formato";
import { Estado } from "../../compartido/Estado";
import { useCarga } from "../../compartido/useCarga";
import { hoy, sumarDias } from "./fechas";
import { TablaTurnos } from "./TablaTurnos";

interface Props {
  puedeGestionar: boolean;
  version: number;
  alCambiar: (r?: ResultadoTurno) => void;
}

/** Solicitudes que esperan aprobación del negocio, de ayer en adelante (las anteriores ya vencieron). */
export function Pendientes({ puedeGestionar, version, alCambiar }: Props) {
  const cargar = useCallback(
    () =>
      listarTurnos({
        desde: localAIso(`${sumarDias(hoy(), -1)}T00:00`),
        hasta: localAIso(`${sumarDias(hoy(), 180)}T00:00`),
        estados: ["pending"],
      }),
    // `version` fuerza la recarga tras un cambio hecho en otra parte de la pantalla.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );
  const { carga, recargar } = useCarga(cargar);

  return (
    <section aria-label="Pendientes de aprobación">
      <h2>Pendientes de aprobación</h2>
      <Estado carga={carga} reintentar={recargar}>
        {(turnos) =>
          turnos.length === 0 ? (
            <p>No hay solicitudes pendientes.</p>
          ) : (
            <>
              {turnos.length >= LIMITE_TURNOS && <p>Se muestran las primeras {LIMITE_TURNOS} solicitudes.</p>}
              <TablaTurnos turnos={turnos} conFecha conProfesional puedeGestionar={puedeGestionar} alCambiar={alCambiar} />
            </>
          )
        }
      </Estado>
    </section>
  );
}
