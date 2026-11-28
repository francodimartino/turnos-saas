import { useCallback } from "react";
import { listarCambiosPendientes, LIMITE_TURNOS, type ResultadoTurno } from "../../api/turnos";
import { Estado } from "../../compartido/Estado";
import { useCarga } from "../../compartido/useCarga";
import { TablaTurnos } from "./TablaTurnos";

interface Props {
  version: number;
  alCambiar: (r?: ResultadoTurno, error?: string) => void;
}

/** Turnos cuyo cliente pidió otro horario y esperan la decisión del negocio. Solo se muestra con permiso para aprobar turnos. */
export function CambiosPendientes({ version, alCambiar }: Props) {
  const cargar = useCallback(
    () => listarCambiosPendientes(),
    // `version` fuerza la recarga tras un cambio hecho en otra parte de la pantalla.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );
  const { carga, recargar } = useCarga(cargar);

  return (
    <section aria-label="Cambios de horario pendientes">
      <h2>Cambios de horario pendientes</h2>
      <Estado carga={carga} reintentar={recargar}>
        {(turnos) =>
          turnos.length === 0 ? (
            <p>No hay cambios de horario pendientes.</p>
          ) : (
            <>
              {turnos.length >= LIMITE_TURNOS && <p>Se muestran los primeros {LIMITE_TURNOS} cambios.</p>}
              <TablaTurnos turnos={turnos} conFecha conProfesional puedeGestionar alCambiar={alCambiar} />
            </>
          )
        }
      </Estado>
    </section>
  );
}
