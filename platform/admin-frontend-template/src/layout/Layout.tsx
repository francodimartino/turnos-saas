import { NavLink, Outlet } from "react-router-dom";
import { useSesion } from "../auth/SesionContext";
import { SECCIONES } from "./secciones";

/** Marco de las pantallas privadas: navegación y cierre de sesión. */
export function Layout() {
  const { estado, salir } = useSesion();
  if (estado.estado !== "activa") return null;

  return (
    <div className="marco">
      <header>
        <nav aria-label="Secciones">
          <ul>
            {SECCIONES.map((s) => (
              <li key={s.ruta}>
                <NavLink to={s.ruta} end={s.ruta === "/"}>
                  {s.etiqueta}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <button type="button" onClick={() => void salir()}>
          Cerrar sesión
        </button>
      </header>
      <Outlet />
    </div>
  );
}
