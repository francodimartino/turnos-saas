import { useState, type FormEvent } from "react";
import { ErrorApi } from "../../api/cliente";
import { crearTurno, type ResultadoTurno } from "../../api/turnos";
import { Campo } from "../../compartido/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../compartido/errores";
import { localAIso } from "../../compartido/formato";
import { nombreCliente, type Base } from "./base";
import { MENSAJE_HORARIO_OCUPADO } from "./errores";

interface Props {
  base: Base;
  alCambiar: (r?: ResultadoTurno) => void;
}

/** Alta de un turno por el negocio: nace confirmado y dura lo que dura el servicio. */
export function FormularioTurno({ base, alCambiar }: Props) {
  const [abierto, setAbierto] = useState(false);
  const [busqueda, setBusqueda] = useState("");
  const [clienteId, setClienteId] = useState("");
  const [servicioId, setServicioId] = useState("");
  const [profesionalId, setProfesionalId] = useState("");
  const [inicio, setInicio] = useState("");
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [trabajando, setTrabajando] = useState(false);
  const [creado, setCreado] = useState(false);

  const texto = busqueda.trim().toLowerCase();
  const clientes = base.clientes.filter(
    (c) =>
      !c.sin_cliente_identificable &&
      (texto === "" || `${c.name ?? ""} ${c.phone ?? ""}`.toLowerCase().includes(texto)),
  );
  const servicios = base.servicios.filter((s) => s.is_active);
  const profesionales = base.profesionales.filter(
    (p) => p.is_active && servicioId !== "" && base.ofrece[p.id]?.includes(servicioId),
  );

  function elegirServicio(id: string) {
    setServicioId(id);
    if (profesionalId && !base.ofrece[profesionalId]?.includes(id)) setProfesionalId("");
  }

  async function enviar(ev: FormEvent) {
    ev.preventDefault();
    setCreado(false);
    const faltantes: Record<string, string> = {};
    if (!clienteId) faltantes["end_customer_id"] = "Elegí un cliente";
    if (!servicioId) faltantes["service_id"] = "Elegí un servicio";
    if (!profesionalId) faltantes["professional_id"] = "Elegí un profesional que haga ese servicio";
    if (!inicio) faltantes["start_at"] = "Indicá el día y la hora";
    if (Object.keys(faltantes).length > 0) {
      setErrores({ campos: faltantes, general: null });
      return;
    }
    setTrabajando(true);
    setErrores(SIN_ERRORES);
    try {
      const r = await crearTurno({
        end_customer_id: clienteId,
        service_id: servicioId,
        professional_id: profesionalId,
        start_at: localAIso(inicio),
      });
      setInicio("");
      setCreado(true);
      alCambiar(r);
    } catch (e) {
      setErrores(
        e instanceof ErrorApi && e.codigo === "horario_ocupado"
          ? { campos: { start_at: MENSAJE_HORARIO_OCUPADO }, general: null }
          : erroresDeApi(e),
      );
    } finally {
      setTrabajando(false);
    }
  }

  if (!abierto) {
    return (
      <section aria-label="Crear turno">
        <button type="button" onClick={() => setAbierto(true)}>
          Nuevo turno
        </button>
        {creado && <p role="status">Turno creado.</p>}
      </section>
    );
  }

  return (
    <section aria-label="Crear turno">
      <h2>Nuevo turno</h2>
      <form className="formulario" onSubmit={(ev) => void enviar(ev)} noValidate>
        <label>
          Buscar cliente
          <input type="search" value={busqueda} onChange={(e) => setBusqueda(e.target.value)} />
        </label>
        <Campo etiqueta="Cliente" error={errores.campos["end_customer_id"]}>
          {(a) => (
            <select {...a} value={clienteId} onChange={(e) => setClienteId(e.target.value)}>
              <option value="">Elegí un cliente</option>
              {clientes.map((c) => (
                <option key={c.id} value={c.id}>
                  {nombreCliente(c)}
                </option>
              ))}
            </select>
          )}
        </Campo>
        <Campo etiqueta="Servicio" error={errores.campos["service_id"]}>
          {(a) => (
            <select {...a} value={servicioId} onChange={(e) => elegirServicio(e.target.value)}>
              <option value="">Elegí un servicio</option>
              {servicios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.duration_minutes} min)
                </option>
              ))}
            </select>
          )}
        </Campo>
        <Campo
          etiqueta="Profesional"
          error={errores.campos["professional_id"]}
          {...(servicioId === "" ? { ayuda: "Primero elegí el servicio: solo se listan quienes lo hacen." } : {})}
        >
          {(a) => (
            <select {...a} value={profesionalId} onChange={(e) => setProfesionalId(e.target.value)}>
              <option value="">Elegí un profesional</option>
              {profesionales.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
        </Campo>
        <Campo etiqueta="Día y hora (Buenos Aires)" error={errores.campos["start_at"]}>
          {(a) => <input {...a} type="datetime-local" value={inicio} onChange={(e) => setInicio(e.target.value)} />}
        </Campo>
        {errores.general && <p role="alert">{errores.general}</p>}
        {creado && <p role="status">Turno creado.</p>}
        <div>
          <button type="submit" disabled={trabajando}>
            Crear turno
          </button>{" "}
          <button type="button" onClick={() => setAbierto(false)}>
            Cerrar
          </button>
        </div>
      </form>
    </section>
  );
}
