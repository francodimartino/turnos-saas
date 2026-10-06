import { Navigate, Route, Routes } from "react-router-dom";
import { RutaProtegida } from "./auth/RutaProtegida";
import { Layout } from "./layout/Layout";
import { Auditoria } from "./paginas/Auditoria";
import { Impersonacion } from "./paginas/impersonacion/Impersonacion";
import { Inicio } from "./paginas/Inicio";
import { Login } from "./paginas/Login";
import { MfaEnrolar } from "./paginas/MfaEnrolar";
import { MfaVerificar } from "./paginas/MfaVerificar";
import { Notificaciones } from "./paginas/Notificaciones";
import { Settings } from "./paginas/Settings";
import { FichaTenant } from "./paginas/tenants/FichaTenant";
import { ListaTenants } from "./paginas/tenants/ListaTenants";
import { NuevoTenant } from "./paginas/tenants/NuevoTenant";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/mfa/enrolar" element={<MfaEnrolar />} />
      <Route path="/mfa/verificar" element={<MfaVerificar />} />
      <Route element={<RutaProtegida />}>
        <Route element={<Layout />}>
          <Route index element={<Inicio />} />
          <Route path="/tenants" element={<ListaTenants />} />
          <Route path="/tenants/nuevo" element={<NuevoTenant />} />
          <Route path="/tenants/:id" element={<FichaTenant />} />
          <Route path="/tenants/:id/auditoria" element={<Auditoria />} />
          <Route path="/tenants/:id/impersonar" element={<Impersonacion />} />
          <Route path="/notificaciones" element={<Notificaciones />} />
          <Route path="/configuracion" element={<Settings />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
