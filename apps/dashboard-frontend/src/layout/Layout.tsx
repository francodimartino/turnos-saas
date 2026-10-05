import { NavLink, Outlet } from "react-router-dom";
import { useSesion } from "../auth/SesionContext";
import { seccionesVisibles, type Seccion } from "./permisos";
import { SECCIONES } from "./secciones";

interface Props {
  secciones?: readonly Seccion[];
}

/** Marco de las pantallas privadas: navegación filtrada por permisos y cierre de sesión. */
export function Layout({ secciones = SECCIONES }: Props) {
  const { estado, salir } = useSesion();
  if (estado.estado !== "activa") return null;
  const visibles = seccionesVisibles(secciones, estado.sesion);

  return (
    <div className="marco">
      <header>
        <nav aria-label="Secciones">
          <ul>
            {visibles.map((s) => (
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
