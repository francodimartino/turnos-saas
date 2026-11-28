import { Link } from "react-router-dom";
import { Marco } from "../compartido/Marco";

/** Ruta que no corresponde a ningún negocio ni a una página de la web (nombres reservados sin página incluidos). */
export function NoEncontrada() {
  return (
    <Marco>
      <h1>No encontramos esta página</h1>
      <p>La dirección no existe. Podés volver al inicio.</p>
      <p>
        <Link to="/" className="boton">
          Ir al inicio
        </Link>
      </p>
    </Marco>
  );
}
