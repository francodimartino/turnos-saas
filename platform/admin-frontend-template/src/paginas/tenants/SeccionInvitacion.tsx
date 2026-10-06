import { useState, type FormEvent } from "react";
import { reenviarInvitacion } from "../../api/panel";
import { mensajeDeError } from "../../compartido/useCarga";

/** Invitación pendiente del owner. Nombre y email del owner se pueden corregir (opcional); si no, se reenvía con lo guardado. */
export function SeccionInvitacion({
  tenantId,
  pendiente,
  alCambiar,
}: {
  tenantId: string;
  pendiente: boolean;
  alCambiar: () => void;
}) {
  const [email, setEmail] = useState("");
  const [nombre, setNombre] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setAviso(null);
    setOcupado(true);
    try {
      const r = await reenviarInvitacion(tenantId, {
        ownerEmail: email.trim() || undefined,
        ownerNombre: nombre.trim() || undefined,
      });
      setEmail("");
      setNombre("");
      if (r.estado_invitacion === "ok") setAviso("Invitación enviada.");
      else setError("La invitación volvió a fallar. Probá de nuevo más tarde o corregí el email.");
    } catch (err) {
      setError(mensajeDeError(err));
    } finally {
      setOcupado(false);
      alCambiar();
    }
  }

  // Una vez enviada deja de estar pendiente: se sigue mostrando solo el aviso de éxito.
  if (!pendiente && !aviso) return null;

  return (
    <form className="panel" onSubmit={(e) => void enviar(e)}>
      <h2>Invitación al owner</h2>
      {pendiente && (
        <>
          <p>La invitación al owner no se pudo enviar. El nombre y el email ya están guardados: alcanza con reenviarla.</p>
          <label className="campo">
            Corregir el nombre del owner (opcional)
            <input maxLength={120} autoComplete="off" value={nombre} onChange={(e) => setNombre(e.target.value)} />
          </label>
          <label className="campo">
            Corregir el email del owner (opcional)
            <input
              type="email"
              maxLength={254}
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <button type="submit" disabled={ocupado}>
            Reenviar invitación
          </button>
        </>
      )}
      {aviso && <p role="status">{aviso}</p>}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
