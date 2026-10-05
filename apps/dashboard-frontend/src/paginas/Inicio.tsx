import { useSesion } from "../auth/SesionContext";

export function Inicio() {
  const { estado } = useSesion();
  if (estado.estado !== "activa") return null;
  const { isOwner, aal } = estado.sesion;
  return (
    <section>
      <h1>Panel</h1>
      <p>
        Sesión iniciada como {isOwner ? "dueño" : "personal"} del negocio (verificación {aal === "aal2" ? "en dos pasos" : "simple"}).
      </p>
    </section>
  );
}
