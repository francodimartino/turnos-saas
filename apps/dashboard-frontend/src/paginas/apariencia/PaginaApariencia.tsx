import { useState } from "react";
import { leerSettings } from "../../api/catalogo";
import type { SettingsMarca } from "../../api/marca";
import { Estado } from "../../compartido/Estado";
import { useCarga } from "../../compartido/useCarga";
import { usePermiso } from "../../compartido/usePermiso";
import { FormularioColoresPlazo } from "./FormularioColoresPlazo";
import { SeccionLogo } from "./SeccionLogo";
import { VistaPrevia } from "./VistaPrevia";

function Apariencia({ inicial }: { inicial: SettingsMarca }) {
  const [settings, setSettings] = useState(inicial);
  const [principal, setPrincipal] = useState(inicial.color_principal ?? "");
  const [secundario, setSecundario] = useState(inicial.color_secundario ?? "");
  const [imagen, setImagen] = useState<string | null>(null);
  const hayLogo = (settings.logo_ruta ?? null) !== null;

  return (
    <>
      <VistaPrevia principal={principal} secundario={secundario} logoUrl={imagen} hayLogoGuardado={hayLogo} />
      <SeccionLogo hayLogo={hayLogo} alCambiarSettings={setSettings} alCambiarImagen={setImagen} />
      <FormularioColoresPlazo
        settings={settings}
        principal={principal}
        secundario={secundario}
        alCambiarColores={(p, s) => {
          setPrincipal(p);
          setSecundario(s);
        }}
        alGuardar={setSettings}
      />
    </>
  );
}

function ConPermiso() {
  const { carga, recargar } = useCarga(leerSettings);
  return (
    <Estado carga={carga} reintentar={recargar}>
      {({ settings }) => <Apariencia inicial={settings} />}
    </Estado>
  );
}

/** Apariencia del negocio: logo, colores y plazo de cancelación (requiere `manage_schedule`). */
export function PaginaApariencia() {
  const puedeEditar = usePermiso("manage_schedule");
  return (
    <section>
      <h1>Apariencia</h1>
      {puedeEditar ? <ConPermiso /> : <p>No tenés permiso para cambiar la apariencia del negocio.</p>}
    </section>
  );
}
