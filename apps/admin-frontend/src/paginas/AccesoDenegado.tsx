import { useSesion } from "../auth/SesionContext";

/** Sesión válida de una cuenta que no es administradora de la plataforma: se explica y se ofrece salir. */
export function AccesoDenegado() {
  const { salir } = useSesion();
  return (
    <main className="tarjeta">
      <h1>Sin acceso</h1>
      <p role="alert">Esta cuenta no es administradora de la plataforma, por eso no puede entrar al panel.</p>
      <button type="button" onClick={() => void salir()}>
        Cerrar sesión
      </button>
    </main>
  );
}
