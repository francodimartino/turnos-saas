import { useState, type FormEvent } from "react";
import { cambiarSlugTenant } from "../../api/panel";
import { ErrorApi } from "../../api/cliente";

/** Días que el slug anterior sigue redirigiendo al nuevo (lo hace cumplir el backend). */
const DIAS_DE_REDIRECCION = 180;

/**
 * Mensaje propio de cada error del backend. No incluye el slug que se escribió ni el texto que manda el servidor:
 * el mensaje depende solo del código. Un código desconocido (o un fallo de red o un 500) usa el mensaje genérico.
 */
const MENSAJES_DE_ERROR: Readonly<Record<string, string>> = {
  slug_invalido: "El slug no es válido: usá solo minúsculas, números y guiones, entre 3 y 40 caracteres.",
  slug_reservado: "Ese slug está reservado por la plataforma: elegí otro.",
  slug_en_uso: "Ese slug ya lo usa otro negocio, o es el enlace anterior de otro negocio y todavía está vigente: elegí otro.",
  slug_sin_cambio: "El negocio ya tiene ese slug: escribí uno distinto.",
  cambio_slug_concurrente: "Otro cambio de slug se cruzó con este. Revisá el slug vigente en la ficha y volvé a intentar.",
  purga_en_curso: "No se puede cambiar el slug: el negocio está en proceso de purga.",
  transicion_invalida: "No se puede cambiar el slug en el estado actual del negocio.",
  tenant_no_encontrado: "El negocio ya no existe.",
};

const MENSAJE_GENERICO = "No se pudo cambiar el slug. Probá de nuevo en unos minutos.";

export function mensajeDeCambioSlug(e: unknown): string {
  return (e instanceof ErrorApi && MENSAJES_DE_ERROR[e.codigo]) || MENSAJE_GENERICO;
}

interface Props {
  tenantId: string;
  slugActual: string;
  alCambiar: () => void;
}

/**
 * Cambio del slug (la dirección pública `agendemos.ar/<slug>`) de un negocio. Pide confirmación explícita: el link
 * viejo deja de ser el principal pero sigue redirigiendo durante 180 días.
 */
export function SeccionSlug({ tenantId, slugActual, alCambiar }: Props) {
  const [nuevo, setNuevo] = useState("");
  const [confirmando, setConfirmando] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exito, setExito] = useState<{ anterior: string; nuevo: string } | null>(null);

  const candidato = nuevo.trim();

  function pedirConfirmacion(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setExito(null);
    setConfirmando(true);
  }

  async function confirmar() {
    if (enviando) return;
    setEnviando(true);
    setError(null);
    try {
      const r = await cambiarSlugTenant(tenantId, candidato);
      setExito({ anterior: r.slug_anterior, nuevo: r.slug_nuevo });
      setConfirmando(false);
      setNuevo("");
      alCambiar();
    } catch (e) {
      setError(mensajeDeCambioSlug(e));
    } finally {
      setEnviando(false);
    }
  }

  function cancelar() {
    setConfirmando(false);
    setError(null);
  }

  return (
    <div className="panel">
      <h2>Slug del negocio</h2>
      <p>
        Es la dirección pública del negocio. Slug vigente: <code>{slugActual}</code>
      </p>
      <form onSubmit={pedirConfirmacion}>
        <label className="campo">
          Slug nuevo
          <input
            value={nuevo}
            onChange={(e) => setNuevo(e.target.value)}
            autoComplete="off"
            disabled={confirmando}
          />
          <small>Minúsculas, números y guiones, de 3 a 40 caracteres.</small>
        </label>
        <button type="submit" disabled={confirmando || candidato === ""}>
          Cambiar slug
        </button>
      </form>
      {confirmando && (
        <div role="alertdialog" aria-labelledby="titulo-slug" className="confirmacion">
          <h3 id="titulo-slug">Confirmar el cambio de slug</h3>
          <p>
            La dirección pasa de <code>{slugActual}</code> a <code>{candidato}</code>.
          </p>
          <p>
            El link viejo sigue funcionando: redirige al nuevo durante {DIAS_DE_REDIRECCION} días. Pasado ese plazo
            queda libre y deja de redirigir, así que conviene actualizar los links y los códigos QR que ya se repartieron.
          </p>
          <div className="acciones">
            <button type="button" disabled={enviando} onClick={() => void confirmar()}>
              Confirmar cambio
            </button>
            <button type="button" disabled={enviando} onClick={cancelar}>
              Cancelar
            </button>
          </div>
          {error && <p role="alert">{error}</p>}
        </div>
      )}
      {exito && (
        <p role="status">
          Slug cambiado a <code>{exito.nuevo}</code>. El link anterior (<code>{exito.anterior}</code>) sigue
          redirigiendo al nuevo durante {DIAS_DE_REDIRECCION} días.
        </p>
      )}
    </div>
  );
}
