import { Navigate, Route, Routes } from "react-router-dom";
import { RutaProtegida } from "./auth/RutaProtegida";
import { Layout } from "./layout/Layout";
import { PaginaProfesionales } from "./paginas/catalogo/PaginaProfesionales";
import { PaginaClientes } from "./paginas/clientes/PaginaClientes";
import { PaginaStaff } from "./paginas/staff/PaginaStaff";
import { PaginaServicios } from "./paginas/catalogo/PaginaServicios";
import { PaginaDisponibilidad } from "./paginas/disponibilidad/PaginaDisponibilidad";
import { PaginaTurnos } from "./paginas/turnos/PaginaTurnos";
import { PaginaLocal } from "./paginas/local/PaginaLocal";
import { CalendarCallback } from "./paginas/CalendarCallback";
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
          <Route path="turnos" element={<PaginaTurnos />} />
          <Route path="clientes" element={<PaginaClientes />} />
          <Route path="equipo" element={<PaginaStaff />} />
          <Route path="calendar/callback" element={<CalendarCallback />} />
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
