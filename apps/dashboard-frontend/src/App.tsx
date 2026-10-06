import { Navigate, Route, Routes } from "react-router-dom";
import { RutaProtegida } from "./auth/RutaProtegida";
import { Layout } from "./layout/Layout";
import { PaginaProfesionales } from "./features/catalogo/PaginaProfesionales";
import { PaginaServicios } from "./features/catalogo/PaginaServicios";
import { PaginaDisponibilidad } from "./features/disponibilidad/PaginaDisponibilidad";
import { PaginaLocal } from "./features/local/PaginaLocal";
import { AceptarInvitacion } from "./paginas/AceptarInvitacion";
import { Inicio } from "./paginas/Inicio";
import { Login } from "./paginas/Login";
import { MfaEnrolar } from "./paginas/MfaEnrolar";
import { MfaVerificar } from "./paginas/MfaVerificar";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/invitacion" element={<AceptarInvitacion />} />
      <Route path="/mfa/enrolar" element={<MfaEnrolar />} />
      <Route path="/mfa/verificar" element={<MfaVerificar />} />
      <Route element={<RutaProtegida />}>
        <Route element={<Layout />}>
          <Route index element={<Inicio />} />
          <Route path="servicios" element={<PaginaServicios />} />
          <Route path="profesionales" element={<PaginaProfesionales />} />
          <Route path="disponibilidad" element={<PaginaDisponibilidad />} />
          <Route path="local" element={<PaginaLocal />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
