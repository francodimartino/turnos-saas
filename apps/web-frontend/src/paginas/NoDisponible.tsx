import { Link } from "react-router-dom";
import { Marco } from "../compartido/Marco";

/** Página de un negocio que no se puede mostrar: no existe, está dado de baja o todavía no atiende por la web. */
export function NoDisponible() {
  return (
    <Marco>
      <h1>Esta página no está disponible</h1>
      <p>Revisá que la dirección esté bien escrita o consultá con el negocio por otro medio.</p>
      <p>
        <Link to="/" className="boton">
          Ir al inicio
        </Link>
      </p>
    </Marco>
  );
}
