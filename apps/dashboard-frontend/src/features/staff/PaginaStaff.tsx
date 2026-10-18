import { useState } from "react";
import { useSesion } from "../../auth/SesionContext";
import {
  cambiarOwner,
  cambiarPermisos,
  desactivarStaff,
  listarStaff,
  reactivarStaff,
  type MiembroStaff,
} from "../../api/staff";
import { erroresDeApi } from "../../shared/errores";
import { Estado } from "../../shared/Estado";
import { formatearFechaHora } from "../../shared/formato";
import { useCarga } from "../../shared/useCarga";
import { esPermisoConocido, etiquetaDePermiso } from "./catalogoPermisos";
import { FormularioInvitacion } from "./FormularioInvitacion";
import { SelectorPermisos } from "./SelectorPermisos";

interface PropsEditor {
  nombre: string;
  miembro: MiembroStaff;
  alGuardar: () => void;
  alCancelar: () => void;
}

function EditorPermisos({ nombre, miembro, alGuardar, alCancelar }: PropsEditor) {
  const [seleccion, setSeleccion] = useState<string[]>(miembro.permission_keys.filter(esPermisoConocido));
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function guardar() {
    setEnviando(true);
    setError(null);
    try {
      await cambiarPermisos(miembro.id, seleccion);
      alGuardar();
    } catch (e) {
      setError(erroresDeApi(e).general ?? "No se pudieron guardar los permisos");
      setEnviando(false);
    }
  }

  return (
    <div className="panel" role="group" aria-label={`Permisos de ${nombre}`}>
      <SelectorPermisos legend={`Permisos de ${nombre}`} seleccion={seleccion} alCambiar={setSeleccion} />
      {error && <p role="alert">{error}</p>}
      <div className="acciones">
        <button type="button" disabled={enviando} onClick={() => void guardar()}>
          Guardar permisos
        </button>
        <button type="button" onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </div>
  );
}

/** Cómo se nombra a una persona en botones y avisos: su nombre, o su email si todavía no tiene cuenta. */
const etiquetaDe = (m: MiembroStaff, i: number) => m.nombre ?? m.email ?? `Persona ${i + 1}`;

export function PaginaStaff() {
  const { estado } = useSesion();
  const soyOwner = estado.estado === "activa" && estado.sesion.isOwner;
  const { carga, recargar } = useCarga(listarStaff);
  const [invitando, setInvitando] = useState(false);
  const [editando, setEditando] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ejecutar(accion: () => Promise<unknown>, exito: string) {
    setError(null);
    setAviso(null);
    try {
      await accion();
      setAviso(exito);
      recargar();
    } catch (e) {
      // Un 409 `ultimo_owner` trae su propio mensaje: explica qué hacer antes.
      setError(erroresDeApi(e).general ?? "No se pudo completar la operación");
    }
  }

  return (
    <section>
      <h1>Equipo</h1>
      {!soyOwner && (
        <p>
          Podés activar y desactivar a las personas del equipo. Invitar, cambiar permisos y dar el rol de owner es
          solo del owner del local.
        </p>
      )}
      {aviso && <p role="status">{aviso}</p>}
      {error && <p role="alert">{error}</p>}
      <Estado carga={carga} reintentar={recargar}>
        {({ staff }) => (
          <>
            <table>
              <thead>
                <tr>
                  <th>Persona</th>
                  <th>Rol</th>
                  <th>Estado</th>
                  <th>Permisos</th>
                  <th>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {staff.map((m, i) => {
                  const etiqueta = etiquetaDe(m, i);
                  const puedeTocar = !m.is_owner || soyOwner;
                  return (
                    <tr key={m.id}>
                      <td>
                        <strong>{etiqueta}</strong>
                        {m.es_yo ? " (vos)" : ""}
                        {m.nombre === null && m.email !== null && <em> (invitación pendiente)</em>}
                        {m.nombre !== null && m.email !== null && (
                          <>
                            <br />
                            <small>{m.email}</small>
                          </>
                        )}
                        <br />
                        <small>Desde {formatearFechaHora(m.created_at)}</small>
                      </td>
                      <td>{m.is_owner ? "Owner" : "Staff"}</td>
                      <td>{m.is_active ? "Activo" : "Desactivado"}</td>
                      <td>
                        {m.is_owner
                          ? "Todos (es owner)"
                          : m.permission_keys.length === 0
                            ? "Ninguno"
                            : m.permission_keys.map(etiquetaDePermiso).join(", ")}
                      </td>
                      <td>
                        {puedeTocar &&
                          (m.is_active ? (
                            <button
                              type="button"
                              onClick={() => void ejecutar(() => desactivarStaff(m.id), `${etiqueta} fue desactivada.`)}
                            >
                              Desactivar a {etiqueta}
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() => void ejecutar(() => reactivarStaff(m.id), `${etiqueta} fue reactivada.`)}
                            >
                              Reactivar a {etiqueta}
                            </button>
                          ))}
                        {soyOwner && !m.is_owner && (
                          <>
                            {" "}
                            <button type="button" onClick={() => setEditando(m.id)}>
                              Editar permisos de {etiqueta}
                            </button>
                          </>
                        )}
                        {soyOwner && (m.is_owner || m.is_active) && (
                          <>
                            {" "}
                            <button
                              type="button"
                              onClick={() =>
                                void ejecutar(
                                  () => cambiarOwner(m.id, !m.is_owner),
                                  m.is_owner ? `${etiqueta} ya no es owner.` : `${etiqueta} ahora es owner.`,
                                )
                              }
                            >
                              {m.is_owner ? `Quitar owner a ${etiqueta}` : `Hacer owner a ${etiqueta}`}
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {soyOwner &&
              staff.map((m, i) =>
                editando === m.id ? (
                  <EditorPermisos
                    key={m.id}
                    nombre={etiquetaDe(m, i)}
                    miembro={m}
                    alGuardar={() => {
                      setEditando(null);
                      setAviso("Permisos actualizados.");
                      recargar();
                    }}
                    alCancelar={() => setEditando(null)}
                  />
                ) : null,
              )}
          </>
        )}
      </Estado>
      {soyOwner && !invitando && (
        <button type="button" onClick={() => setInvitando(true)}>
          Invitar a una persona
        </button>
      )}
      {soyOwner && invitando && (
        <FormularioInvitacion
          alInvitar={(creado) => {
            setInvitando(false);
            setError(null);
            setAviso(
              creado
                ? "Invitación enviada. La persona va a recibir un email para crear su cuenta."
                : "Esa persona ya estaba en el equipo: no se cambiaron sus permisos.",
            );
            recargar();
          }}
          alCancelar={() => setInvitando(false)}
        />
      )}
    </section>
  );
}
