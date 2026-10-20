import { useState } from "react";
import { listarProfesionales } from "../../api/catalogo";
import { Estado } from "../../compartido/Estado";
import { useCarga } from "../../compartido/useCarga";
import { usePermiso } from "../../compartido/usePermiso";
import { Bloqueos } from "./Bloqueos";
import { ReglasSemanales } from "./ReglasSemanales";

export function PaginaDisponibilidad() {
  const puedeEditar = usePermiso("manage_schedule");
  const { carga, recargar } = useCarga(listarProfesionales);
  const [elegido, setElegido] = useState<string | null>(null);

  return (
    <section>
      <h1>Disponibilidad</h1>
      {!puedeEditar && <p>Solo podés ver la disponibilidad: modificarla requiere un permiso que tu usuario no tiene.</p>}
      <Estado carga={carga} reintentar={recargar}>
        {(profesionales) => {
          const profesionalId = elegido ?? profesionales[0]?.id ?? null;
          return (
            <>
              {profesionalId === null ? (
                <p>Primero cargá un profesional en la sección Profesionales.</p>
              ) : (
                <>
                  <label>
                    Profesional
                    <select value={profesionalId} onChange={(e) => setElegido(e.target.value)}>
                      {profesionales.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                          {p.is_active ? "" : " (inactivo)"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <ReglasSemanales key={profesionalId} profesionalId={profesionalId} puedeEditar={puedeEditar} />
                </>
              )}
              <Bloqueos profesionales={profesionales} puedeEditar={puedeEditar} />
            </>
          );
        }}
      </Estado>
    </section>
  );
}
