import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { crearTenant, type EstadoInvitacion } from "../../api/panel";
import { ErrorApi } from "../../api/cliente";

interface Creado {
  tenantId: string;
  estado: EstadoInvitacion;
}

/** Alta de tenant con el email del owner (se le manda la invitación). El email no se vuelve a mostrar. */
export function NuevoTenant() {
  const [nombre, setNombre] = useState("");
  const [slug, setSlug] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [errores, setErrores] = useState<Readonly<Record<string, string>>>({});
  const [enviando, setEnviando] = useState(false);
  const [creado, setCreado] = useState<Creado | null>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setErrores({});
    setEnviando(true);
    try {
      const r = await crearTenant({ name: nombre.trim(), slug: slug.trim(), owner_email: email.trim() });
      setCreado({ tenantId: r.tenant_id, estado: r.estado_invitacion });
      setEmail("");
    } catch (err) {
      if (err instanceof ErrorApi) {
        setError(err.message);
        setErrores(err.campos);
      } else {
        setError("No se pudo crear el tenant");
      }
    } finally {
      setEnviando(false);
    }
  }

  if (creado) {
    return (
      <section>
        <h1>Tenant creado</h1>
        {creado.estado === "ok" ? (
          <p role="status">El tenant se creó y la invitación al owner fue enviada.</p>
        ) : (
          <p role="alert">
            El tenant se creó, pero la invitación al owner no se pudo enviar. El email quedó guardado: reenviá la
            invitación desde la ficha del tenant.
          </p>
        )}
        <Link to={`/tenants/${creado.tenantId}`}>Ir a la ficha del tenant</Link>
      </section>
    );
  }

  return (
    <section>
      <h1>Nuevo tenant</h1>
      <form className="formulario" onSubmit={(e) => void enviar(e)}>
        <label className="campo">
          Nombre del negocio
          <input required maxLength={200} value={nombre} onChange={(e) => setNombre(e.target.value)} />
          {errores["name"] && <span role="alert">{errores["name"]}</span>}
        </label>
        <label className="campo">
          Slug
          <input
            required
            maxLength={100}
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
          />
          <span className="ayuda">Minúsculas, números y guiones. No se puede repetir.</span>
          {errores["slug"] && <span role="alert">{errores["slug"]}</span>}
        </label>
        <label className="campo">
          Email del owner
          <input
            type="email"
            required
            maxLength={254}
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <span className="ayuda">Recibe la invitación para entrar al dashboard.</span>
          {errores["owner_email"] && <span role="alert">{errores["owner_email"]}</span>}
        </label>
        {error && <p role="alert">{error}</p>}
        <div className="acciones">
          <button type="submit" disabled={enviando}>
            Crear tenant
          </button>
          <Link to="/tenants">Cancelar</Link>
        </div>
      </form>
    </section>
  );
}
