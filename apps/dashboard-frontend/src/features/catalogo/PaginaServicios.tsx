import { useState, type FormEvent } from "react";
import {
  borrarServicio,
  crearServicio,
  listarServicios,
  modificarServicio,
  type DatosServicio,
  type Servicio,
} from "../../api/catalogo";
import { BotonBorrar } from "../../shared/BotonBorrar";
import { Campo } from "../../shared/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../shared/errores";
import { Estado } from "../../shared/Estado";
import { formatearDinero } from "../../shared/formato";
import { useCarga } from "../../shared/useCarga";
import { usePermiso } from "../../shared/usePermiso";

const REGLAS_ERROR = [
  [/duraci/i, "duration_minutes"],
  [/precio/i, "price"],
] as const;

interface PropsFormulario {
  inicial: Servicio | null;
  alGuardar: () => void;
  alCancelar: () => void;
}

function FormularioServicio({ inicial, alGuardar, alCancelar }: PropsFormulario) {
  const [nombre, setNombre] = useState(inicial?.name ?? "");
  const [duracion, setDuracion] = useState(inicial ? String(inicial.duration_minutes) : "");
  const [precio, setPrecio] = useState(inicial ? String(inicial.price) : "");
  const [activo, setActivo] = useState(inicial?.is_active ?? true);
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const campos: Record<string, string> = {};
    const minutos = Number(duracion);
    const precioNormalizado = precio.trim().replace(",", ".");
    if (nombre.trim() === "") campos["name"] = "Ingresá el nombre del servicio";
    if (!/^\d+$/.test(duracion.trim()) || minutos < 1) campos["duration_minutes"] = "Ingresá una duración en minutos (entero mayor a 0)";
    if (!/^\d+(\.\d{1,2})?$/.test(precioNormalizado)) campos["price"] = "Ingresá un precio válido (por ejemplo 15000 o 15000,50)";
    if (Object.keys(campos).length > 0) {
      setErrores({ campos, general: null });
      return;
    }
    const datos: DatosServicio = {
      name: nombre.trim(),
      duration_minutes: minutos,
      price: precioNormalizado,
      is_active: activo,
    };
    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      if (inicial) await modificarServicio(inicial.id, datos);
      else await crearServicio(datos);
      alGuardar();
    } catch (err) {
      setErrores(erroresDeApi(err, { reglas: REGLAS_ERROR }));
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" aria-label={inicial ? "Editar servicio" : "Nuevo servicio"} onSubmit={(e) => void enviar(e)} noValidate>
      <Campo etiqueta="Nombre" error={errores.campos["name"]}>
        {(a) => <input {...a} value={nombre} maxLength={200} onChange={(e) => setNombre(e.target.value)} />}
      </Campo>
      <Campo etiqueta="Duración (minutos)" error={errores.campos["duration_minutes"]}>
        {(a) => <input {...a} inputMode="numeric" value={duracion} onChange={(e) => setDuracion(e.target.value)} />}
      </Campo>
      <Campo etiqueta="Precio (ARS)" error={errores.campos["price"]}>
        {(a) => <input {...a} inputMode="decimal" value={precio} onChange={(e) => setPrecio(e.target.value)} />}
      </Campo>
      <label className="casilla">
        <input type="checkbox" checked={activo} onChange={(e) => setActivo(e.target.checked)} />
        Activo (se ofrece a los clientes)
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

export function PaginaServicios() {
  const puedeEditar = usePermiso("edit_prices");
  const { carga, recargar } = useCarga(listarServicios);
  const [editando, setEditando] = useState<Servicio | "nuevo" | null>(null);

  function guardado() {
    setEditando(null);
    recargar();
  }

  return (
    <section>
      <h1>Servicios</h1>
      {!puedeEditar && <p>Solo podés ver los servicios: modificar precios requiere un permiso que tu usuario no tiene.</p>}
      <Estado carga={carga} reintentar={recargar}>
        {(servicios) => (
          <>
            {servicios.length === 0 ? (
              <p>Todavía no cargaste servicios.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Nombre</th>
                    <th>Duración</th>
                    <th>Precio</th>
                    <th>Estado</th>
                    {puedeEditar && <th>Acciones</th>}
                  </tr>
                </thead>
                <tbody>
                  {servicios.map((s) => (
                    <tr key={s.id}>
                      <td>{s.name}</td>
                      <td>{s.duration_minutes} min</td>
                      <td>{formatearDinero(s.price)}</td>
                      <td>{s.is_active ? "Activo" : "Inactivo"}</td>
                      {puedeEditar && (
                        <td>
                          <button type="button" onClick={() => setEditando(s)}>
                            Editar {s.name}
                          </button>{" "}
                          <BotonBorrar nombre={s.name} borrar={() => borrarServicio(s.id)} alBorrar={recargar} />
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {puedeEditar && editando === null && (
              <button type="button" onClick={() => setEditando("nuevo")}>
                Nuevo servicio
              </button>
            )}
            {puedeEditar && editando !== null && (
              <FormularioServicio
                key={editando === "nuevo" ? "nuevo" : editando.id}
                inicial={editando === "nuevo" ? null : editando}
                alGuardar={guardado}
                alCancelar={() => setEditando(null)}
              />
            )}
          </>
        )}
      </Estado>
    </section>
  );
}
