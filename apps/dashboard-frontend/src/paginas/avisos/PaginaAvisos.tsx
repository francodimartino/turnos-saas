import { useRef, useState } from "react";
import { guardarAvisos, leerAvisos, type AvisosNegocio, type CambioAvisos, type DestinatarioAvisos } from "../../api/avisos";
import { Estado } from "../../compartido/Estado";
import { useCarga } from "../../compartido/useCarga";
import { usePermiso } from "../../compartido/usePermiso";
import { mensajeDeAvisos, type AccionAvisos } from "./mensajes";

interface Preferencia {
  recibe: boolean;
  email: boolean;
}

const borradorDe = (datos: AvisosNegocio): Record<string, Preferencia> =>
  Object.fromEntries(datos.staff.map((p) => [p.id, { recibe: p.recibe_avisos, email: p.canal_email }]));

const etiquetaDe = (p: DestinatarioAvisos, i: number) => p.nombre ?? p.email ?? `Persona ${i + 1}`;

function ConfiguracionAvisos({ inicial }: { inicial: AvisosNegocio }) {
  const [datos, setDatos] = useState(inicial);
  const [borrador, setBorrador] = useState(() => borradorDe(inicial));
  const [enCurso, setEnCurso] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const bloqueado = useRef(false);

  const emailDisponible = datos.canales.find((c) => c.canal === "email")?.disponible ?? false;
  const cambios: CambioAvisos[] = datos.staff.flatMap((p) => {
    const b = borrador[p.id];
    if (b === undefined || (b.recibe === p.recibe_avisos && b.email === p.canal_email)) return [];
    return [{ staff_id: p.id, recibe_avisos: b.recibe, canal_email: b.email }];
  });
  const hayCambios = cambios.length > 0;

  async function aplicar(accion: AccionAvisos, pedido: CambioAvisos[], exito: string) {
    if (bloqueado.current) return;
    bloqueado.current = true;
    setEnCurso(true);
    setError(null);
    setAviso(null);
    try {
      const nuevos = await guardarAvisos(pedido);
      setDatos(nuevos);
      setBorrador(borradorDe(nuevos));
      setAviso(exito);
    } catch (e) {
      setError(mensajeDeAvisos(e, accion));
    } finally {
      bloqueado.current = false;
      setEnCurso(false);
    }
  }

  function cambiar(p: DestinatarioAvisos, parte: Partial<Preferencia>) {
    setAviso(null);
    setBorrador((b) => ({ ...b, [p.id]: { recibe: p.recibe_avisos, email: p.canal_email, ...b[p.id], ...parte } }));
  }

  return (
    <>
      <p>
        Elegí quién del equipo recibe los avisos del negocio (por ejemplo, un turno nuevo para aprobar). Por defecto los
        reciben, por email, el owner y quienes aprueban turnos. El negocio siempre tiene que tener al menos una persona
        que los reciba por email.
      </p>
      <p>WhatsApp todavía no está disponible: por ahora los avisos llegan solo por email.</p>
      <table>
        <thead>
          <tr>
            <th>Persona</th>
            <th>Recibe avisos</th>
            <th>Email</th>
            <th>WhatsApp</th>
            <th>Configuración</th>
          </tr>
        </thead>
        <tbody>
          {datos.staff.map((p, i) => {
            const etiqueta = etiquetaDe(p, i);
            const b = borrador[p.id] ?? { recibe: p.recibe_avisos, email: p.canal_email };
            return (
              <tr key={p.id}>
                <td>
                  <strong>{etiqueta}</strong>
                  {p.es_yo === true ? " (vos)" : ""}
                  {p.nombre === null && p.email !== null && <em> (invitación pendiente)</em>}
                  {p.nombre !== null && p.email !== null && (
                    <>
                      <br />
                      <small>{p.email}</small>
                    </>
                  )}
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Recibe avisos: ${etiqueta}`}
                    checked={b.recibe}
                    disabled={enCurso}
                    onChange={(e) => cambiar(p, { recibe: e.target.checked })}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Email: ${etiqueta}`}
                    checked={b.email}
                    disabled={enCurso || !emailDisponible || !b.recibe}
                    onChange={(e) => cambiar(p, { email: e.target.checked })}
                  />
                </td>
                <td>
                  <input type="checkbox" aria-label={`WhatsApp: ${etiqueta}`} checked={false} disabled />
                </td>
                <td>
                  {p.es_default ? (
                    "Según el rol (por defecto)"
                  ) : (
                    <>
                      Personalizada{" "}
                      <button
                        type="button"
                        disabled={enCurso || hayCambios}
                        onClick={() =>
                          void aplicar(
                            "volver_al_default",
                            [{ staff_id: p.id, por_defecto: true }],
                            `${etiqueta} volvió a la configuración por defecto.`,
                          )
                        }
                      >
                        Volver al default: {etiqueta}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {hayCambios && <p>Tenés cambios sin guardar: guardalos o descartalos para poder volver a alguien al default.</p>}
      <div className="acciones">
        <button type="button" disabled={!hayCambios || enCurso} onClick={() => void aplicar("guardar", cambios, "Cambios guardados.")}>
          Guardar cambios
        </button>
        <button type="button" disabled={!hayCambios || enCurso} onClick={() => setBorrador(borradorDe(datos))}>
          Descartar cambios
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {aviso && <p role="status">{aviso}</p>}
    </>
  );
}

function ConPermiso() {
  const { carga, recargar } = useCarga(leerAvisos);
  return (
    <Estado carga={carga} reintentar={recargar}>
      {(datos) => <ConfiguracionAvisos inicial={datos} />}
    </Estado>
  );
}

/** Avisos al negocio: quién del equipo los recibe y por qué canal (requiere `manage_staff`). */
export function PaginaAvisos() {
  const puede = usePermiso("manage_staff");
  return (
    <section>
      <h1>Avisos al negocio</h1>
      {puede ? <ConPermiso /> : <p>No tenés permiso para configurar los avisos del negocio.</p>}
    </section>
  );
}
