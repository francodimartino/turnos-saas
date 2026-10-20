import { useCallback, useState } from "react";
import type { ResultadoTurno } from "../../api/turnos";
import { Estado } from "../../compartido/Estado";
import { useCarga } from "../../compartido/useCarga";
import { usePermiso } from "../../compartido/usePermiso";
import { Agenda } from "./Agenda";
import { cargarBase } from "./base";
import { avisoDeResultado } from "./errores";
import { FormularioTurno } from "./FormularioTurno";
import { Packs } from "./Packs";
import { Pendientes } from "./Pendientes";

export function PaginaTurnos() {
  const puedeGestionar = usePermiso("approve_bookings");
  const { carga, recargar } = useCarga(cargarBase);
  // Cada cambio confirmado sube la versión: la agenda y los pendientes se vuelven a pedir al servidor.
  const [version, setVersion] = useState(0);
  const [aviso, setAviso] = useState<string | null>(null);

  const alCambiar = useCallback((r?: ResultadoTurno) => {
    setAviso(avisoDeResultado(r));
    setVersion((v) => v + 1);
  }, []);

  return (
    <section>
      <h1>Turnos</h1>
      {!puedeGestionar && <p>Solo podés ver los turnos: gestionarlos requiere un permiso que tu usuario no tiene.</p>}
      {aviso && <p role="status">{aviso}</p>}
      <Estado carga={carga} reintentar={recargar}>
        {(base) => (
          <>
            {puedeGestionar && <FormularioTurno base={base} alCambiar={alCambiar} />}
            <Pendientes puedeGestionar={puedeGestionar} version={version} alCambiar={alCambiar} />
            <Agenda
              profesionales={base.profesionales}
              puedeGestionar={puedeGestionar}
              version={version}
              alCambiar={alCambiar}
            />
            <Packs base={base} puedeGestionar={puedeGestionar} />
          </>
        )}
      </Estado>
    </section>
  );
}
