import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { ProveedorSesion } from "./auth/SesionContext";
import { iniciarSentry } from "./observabilidad/sentry";
import "./estilos.css";

iniciarSentry(import.meta.env.VITE_SENTRY_DSN);

const raiz = document.getElementById("root");
if (!raiz) throw new Error("Falta el elemento #root");

createRoot(raiz).render(
  <StrictMode>
    <BrowserRouter>
      <ProveedorSesion>
        <App />
      </ProveedorSesion>
    </BrowserRouter>
  </StrictMode>,
);
