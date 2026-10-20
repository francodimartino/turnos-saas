import { useState, type FormEvent } from "react";
import { asignarPack, listarPacks } from "../../api/turnos";
import { Campo } from "../../compartido/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../compartido/errores";
import { Estado } from "../../compartido/Estado";
import { formatearFechaHora, localAIso } from "../../compartido/formato";
import { useCarga } from "../../compartido/useCarga";
import { nombreCliente, type Base } from "./base";

interface Props {
  base: Base;
  puedeGestionar: boolean;
}

const ORIGEN: Record<string, string> = { manual_grant: "Asignado por el local", purchase: "Compra" };

/** Packs de sesiones de los clientes y asignación manual (sin cobro). */
export function Packs({ base, puedeGestionar }: Props) {
  const { carga, recargar } = useCarga(listarPacks);
  const [clienteId, setClienteId] = useState("");
  const [servicioId, setServicioId] = useState("");
  const [sesiones, setSesiones] = useState("");
  const [vence, setVence] = useState("");
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [trabajando, setTrabajando] = useState(false);
  const [asignado, setAsignado] = useState(false);

  const clientes = base.clientes.filter((c) => !c.sin_cliente_identificable);
  const servicios = base.servicios.filter((s) => s.is_active);
  const cliente = (id: string) => {
    const c = base.clientes.find((x) => x.id === id);
    return c ? nombreCliente(c) : "Cliente";
  };
  const servicio = (id: string) => base.servicios.find((s) => s.id === id)?.name ?? "Servicio";

  async function enviar(ev: FormEvent) {
    ev.preventDefault();
    setAsignado(false);
    const total = Number(sesiones);
    const faltantes: Record<string, string> = {};
    if (!clienteId) faltantes["end_customer_id"] = "Elegí un cliente";
    if (!servicioId) faltantes["service_id"] = "Elegí un servicio";
    if (!Number.isInteger(total) || total < 1) faltantes["total_sessions"] = "Indicá cuántas sesiones (un entero de 1 en adelante)";
    if (Object.keys(faltantes).length > 0) {
      setErrores({ campos: faltantes, general: null });
      return;
    }
    setTrabajando(true);
    setErrores(SIN_ERRORES);
    try {
      await asignarPack({
        end_customer_id: clienteId,
        service_id: servicioId,
        total_sessions: total,
        ...(vence ? { expires_at: localAIso(vence) } : {}),
      });
      setSesiones("");
      setVence("");
      setAsignado(true);
      recargar();
    } catch (e) {
      setErrores(erroresDeApi(e));
    } finally {
      setTrabajando(false);
    }
  }

  return (
    <section aria-label="Packs">
      <h2>Packs de sesiones</h2>
      <Estado carga={carga} reintentar={recargar}>
        {(packs) =>
          packs.length === 0 ? (
            <p>Todavía no hay packs.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th>Servicio</th>
                  <th>Sesiones</th>
                  <th>Origen</th>
                  <th>Vence</th>
                </tr>
              </thead>
              <tbody>
                {packs.map((p) => (
                  <tr key={p.id}>
                    <td>{cliente(p.end_customer_id)}</td>
                    <td>{servicio(p.service_id)}</td>
                    <td>
                      {p.used_sessions} de {p.total_sessions} usadas
                    </td>
                    <td>{ORIGEN[p.origin] ?? p.origin}</td>
                    <td>{p.expires_at ? formatearFechaHora(p.expires_at) : "Sin vencimiento"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Estado>
      {puedeGestionar && (
        <form className="formulario" onSubmit={(ev) => void enviar(ev)} noValidate aria-label="Asignar pack">
          <h3>Asignar un pack a un cliente</h3>
          <Campo etiqueta="Cliente del pack" error={errores.campos["end_customer_id"]}>
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
          <Campo etiqueta="Servicio del pack" error={errores.campos["service_id"]}>
            {(a) => (
              <select {...a} value={servicioId} onChange={(e) => setServicioId(e.target.value)}>
                <option value="">Elegí un servicio</option>
                {servicios.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
          </Campo>
          <Campo etiqueta="Cantidad de sesiones" error={errores.campos["total_sessions"]}>
            {(a) => <input {...a} type="number" min={1} value={sesiones} onChange={(e) => setSesiones(e.target.value)} />}
          </Campo>
          <Campo etiqueta="Vencimiento (opcional)" error={errores.campos["expires_at"]}>
            {(a) => <input {...a} type="datetime-local" value={vence} onChange={(e) => setVence(e.target.value)} />}
          </Campo>
          {errores.general && <p role="alert">{errores.general}</p>}
          {asignado && <p role="status">Pack asignado.</p>}
          <button type="submit" disabled={trabajando}>
            Asignar pack
          </button>
        </form>
      )}
    </section>
  );
}
