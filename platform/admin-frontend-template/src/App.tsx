import { Navigate, Route, Routes } from "react-router-dom";
import { RutaProtegida } from "./auth/RutaProtegida";
import { Layout } from "./layout/Layout";
import { Inicio } from "./paginas/Inicio";
import { Login } from "./paginas/Login";
import { MfaEnrolar } from "./paginas/MfaEnrolar";
import { MfaVerificar } from "./paginas/MfaVerificar";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/mfa/enrolar" element={<MfaEnrolar />} />
      <Route path="/mfa/verificar" element={<MfaVerificar />} />
      <Route element={<RutaProtegida />}>
        <Route element={<Layout />}>
          <Route index element={<Inicio />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
