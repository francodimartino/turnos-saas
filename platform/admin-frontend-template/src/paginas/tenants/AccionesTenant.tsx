import { useState } from "react";
import {
  CLAVE_PERIODO_GRACIA,
  listarSettings,
  transicionarTenant,
  type EstadoTenant,
  type Tenant,
  type Transicion,
} from "../../api/panel";
import { ErrorApi } from "../../api/cliente";
import { mensajeDeError } from "../../compartido/useCarga";

interface Accion {
  transicion: Transicion;
  etiqueta: string;
  ok: string;
}

/** Acciones permitidas por estado (ARCHITECTURE.md §2.2). `purging` (purga en curso) y `purged` (terminal): sin acciones. */
const ACCIONES: Record<EstadoTenant, readonly Accion[]> = {
  active: [
    { transicion: "suspender", etiqueta: "Suspender", ok: "Tenant suspendido." },
    { transicion: "dar-de-baja", etiqueta: "Dar de baja", ok: "Tenant dado de baja." },
  ],
  suspended: [
    { transicion: "levantar-suspension", etiqueta: "Levantar suspensión", ok: "Suspensión levantada." },
    { transicion: "dar-de-baja", etiqueta: "Dar de baja", ok: "Tenant dado de baja." },
  ],
  inactive: [{ transicion: "reactivar", etiqueta: "Reactivar", ok: "Tenant reactivado." }],
  purging: [],
  purged: [],
};

export function accionesPermitidas(estado: EstadoTenant): readonly Accion[] {
  return ACCIONES[estado] ?? [];
}

/** Botones de ciclo de vida del tenant. Dar de baja pide una confirmación fuerte (hay que escribir el slug). */
export function AccionesTenant({ tenant, alCambiar }: { tenant: Tenant; alCambiar: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [confirmando, setConfirmando] = useState(false);
  const [dias, setDias] = useState<number | null>(null);
  const [escrito, setEscrito] = useState("");

  async function ejecutar(a: Accion) {
    setError(null);
    setAviso(null);
    setOcupado(true);
    try {
      await transicionarTenant(tenant.id, a.transicion);
      setConfirmando(false);
      setEscrito("");
      setAviso(a.ok);
    } catch (e) {
      // 409: otro admin (o el job de purga) ya cambió el estado; se recarga para mostrar el real.
      setError(
        e instanceof ErrorApi && e.codigo === "purga_en_curso"
          ? "La purga de este negocio está en curso: no admite cambios de estado."
          : mensajeDeError(e),
      );
    } finally {
      setOcupado(false);
      alCambiar();
    }
  }

  async function pedirBaja() {
    setError(null);
    setAviso(null);
    setEscrito("");
    setConfirmando(true);
    try {
      const s = await listarSettings();
      const valor = s[CLAVE_PERIODO_GRACIA];
      setDias(typeof valor === "number" ? valor : null);
    } catch {
      setDias(null);
    }
  }

  const acciones = accionesPermitidas(tenant.status);
  const baja = acciones.find((a) => a.transicion === "dar-de-baja");

  return (
    <div className="panel">
      <h2>Ciclo de vida</h2>
      {tenant.status === "purged" && (
        <p>Este tenant fue purgado: sus datos personales se anonimizaron y no admite ninguna acción.</p>
      )}
      {tenant.status === "purging" && (
        <p>La purga de este negocio está en curso: no admite ninguna acción hasta que termine.</p>
      )}
      {tenant.status === "inactive" && (
        <p>Está de baja y dentro del período de gracia: se puede reactivar hasta que el job de purga lo anonimice.</p>
      )}
      {acciones.length > 0 && (
        <div className="acciones">
          {acciones.map((a) =>
            a.transicion === "dar-de-baja" ? (
              <button key={a.transicion} type="button" className="peligro" disabled={ocupado} onClick={() => void pedirBaja()}>
                {a.etiqueta}
              </button>
            ) : (
              <button key={a.transicion} type="button" disabled={ocupado} onClick={() => void ejecutar(a)}>
                {a.etiqueta}
              </button>
            ),
          )}
        </div>
      )}
      {confirmando && baja && (
        <div role="alertdialog" aria-labelledby="titulo-baja" className="confirmacion">
          <h3 id="titulo-baja">Confirmar la baja de {tenant.name}</h3>
          <p>
            El bot deja de responder y el dashboard queda bloqueado de inmediato. Los datos se conservan durante el
            período de gracia
            {dias === null ? " (configurable en Configuración)" : ` de ${dias} días`}, contado desde hoy; en ese plazo se
            puede reactivar.
          </p>
          <p>
            <strong>Pasado el período de gracia, la purga es irreversible:</strong> se anonimizan los clientes y el
            personal, se eliminan las credenciales de las integraciones y se borran las cuentas de acceso.
          </p>
          <label className="campo">
            Para confirmar, escribí el slug: <code>{tenant.slug}</code>
            <input value={escrito} onChange={(e) => setEscrito(e.target.value)} autoComplete="off" />
          </label>
          <div className="acciones">
            <button
              type="button"
              className="peligro"
              disabled={ocupado || escrito.trim() !== tenant.slug}
              onClick={() => void ejecutar(baja)}
            >
              Confirmar baja
            </button>
            <button type="button" disabled={ocupado} onClick={() => setConfirmando(false)}>
              Cancelar
            </button>
          </div>
        </div>
      )}
      {aviso && <p role="status">{aviso}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
