import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/** Cabecera, contenido principal y pie comunes a todas las páginas públicas. */
export function Marco({ children }: { children: ReactNode }) {
  return (
    <>
      <a className="saltar" href="#contenido">
        Saltar al contenido
      </a>
      <header className="cabecera">
        <Link to="/" className="marca">
          Agendemos
        </Link>
      </header>
      <main id="contenido" tabIndex={-1}>
        {children}
      </main>
      <footer className="pie">
        <Link to="/privacidad">Política de privacidad</Link>
      </footer>
    </>
  );
}
