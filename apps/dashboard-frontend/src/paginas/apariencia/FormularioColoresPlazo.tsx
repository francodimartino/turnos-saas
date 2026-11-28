import { useRef, useState, type FormEvent } from "react";
import { guardarApariencia, type CambiosApariencia, type SettingsMarca } from "../../api/marca";
import { Campo } from "../../compartido/Campo";
import { mensajeDeGuardarApariencia } from "./mensajes";
import { errorDeColor, errorDePlazo, esColorValido, PLAZO_MAXIMO_HORAS, PLAZO_MINIMO_HORAS, PLAZO_POR_DEFECTO_HORAS } from "./validaciones";

interface Props {
  settings: SettingsMarca;
  principal: string;
  secundario: string;
  alCambiarColores: (principal: string, secundario: string) => void;
  alGuardar: (settings: SettingsMarca) => void;
}

const textoDe = (v: string | null | undefined) => v ?? "";
const plazoDe = (s: SettingsMarca) => (s.cancelacion_plazo_horas == null ? "" : String(s.cancelacion_plazo_horas));

/** Selector nativo de color; solo acepta `#RRGGBB`, así que si el texto no es válido muestra un negro neutro. */
function Selector({ etiqueta, valor, alCambiar }: { etiqueta: string; valor: string; alCambiar: (v: string) => void }) {
  return (
    <input
      type="color"
      aria-label={etiqueta}
      value={esColorValido(valor.trim()) ? valor.trim().toLowerCase() : "#000000"}
      onChange={(e) => alCambiar(e.target.value)}
    />
  );
}

/** Colores de la marca y plazo de cancelación: se guardan juntos y solo se envía lo que cambió. */
export function FormularioColoresPlazo({ settings, principal, secundario, alCambiarColores, alGuardar }: Props) {
  const [plazo, setPlazo] = useState(plazoDe(settings));
  const [errores, setErrores] = useState<Record<string, string>>({});
  const [errorGeneral, setErrorGeneral] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const bloqueado = useRef(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (bloqueado.current) return;
    setAviso(null);
    setErrorGeneral(null);
    const erroresNuevos: Record<string, string> = {};
    const errorPrincipal = errorDeColor(principal);
    const errorSecundario = errorDeColor(secundario);
    const errorPlazo = errorDePlazo(plazo);
    if (errorPrincipal) erroresNuevos["color_principal"] = errorPrincipal;
    if (errorSecundario) erroresNuevos["color_secundario"] = errorSecundario;
    if (errorPlazo) erroresNuevos["plazo"] = errorPlazo;
    setErrores(erroresNuevos);
    if (Object.keys(erroresNuevos).length > 0) return;

    const cambios: CambiosApariencia = {};
    if (principal.trim() !== textoDe(settings.color_principal)) cambios.color_principal = principal.trim() === "" ? null : principal.trim();
    if (secundario.trim() !== textoDe(settings.color_secundario)) cambios.color_secundario = secundario.trim() === "" ? null : secundario.trim();
    if (plazo.trim() !== plazoDe(settings)) cambios.cancelacion_plazo_horas = plazo.trim() === "" ? null : Number(plazo.trim());
    if (Object.keys(cambios).length === 0) {
      setErrorGeneral("No hay cambios para guardar.");
      return;
    }

    bloqueado.current = true;
    setEnviando(true);
    try {
      const { settings: settingsGuardados } = await guardarApariencia(cambios);
      alGuardar(settingsGuardados);
      setPlazo(plazoDe(settingsGuardados));
      alCambiarColores(textoDe(settingsGuardados.color_principal), textoDe(settingsGuardados.color_secundario));
      setAviso("Cambios guardados.");
    } catch (err) {
      setErrorGeneral(mensajeDeGuardarApariencia(err));
    } finally {
      bloqueado.current = false;
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" aria-label="Colores y plazo de cancelación" onSubmit={(e) => void enviar(e)} noValidate>
      <fieldset disabled={enviando}>
        <h2>Colores</h2>
        <Campo etiqueta="Color principal" ayuda="Formato #RRGGBB. Vacío: sin color propio." error={errores["color_principal"]}>
          {(a) => (
            <div className="fila">
              <input {...a} value={principal} maxLength={7} onChange={(e) => alCambiarColores(e.target.value, secundario)} />
              <Selector etiqueta="Elegir color principal" valor={principal} alCambiar={(v) => alCambiarColores(v, secundario)} />
            </div>
          )}
        </Campo>
        <Campo etiqueta="Color secundario" ayuda="Formato #RRGGBB. Vacío: sin color propio." error={errores["color_secundario"]}>
          {(a) => (
            <div className="fila">
              <input {...a} value={secundario} maxLength={7} onChange={(e) => alCambiarColores(principal, e.target.value)} />
              <Selector etiqueta="Elegir color secundario" valor={secundario} alCambiar={(v) => alCambiarColores(principal, v)} />
            </div>
          )}
        </Campo>
        <h2>Cancelación</h2>
        <Campo
          etiqueta="Plazo de cancelación (horas)"
          ayuda={`Horas de anticipación con las que el cliente puede cancelar. Entre ${PLAZO_MINIMO_HORAS} y ${PLAZO_MAXIMO_HORAS}. Vacío: ${PLAZO_POR_DEFECTO_HORAS} horas.`}
          error={errores["plazo"]}
        >
          {(a) => (
            <input
              {...a}
              inputMode="numeric"
              placeholder={String(PLAZO_POR_DEFECTO_HORAS)}
              value={plazo}
              onChange={(e) => setPlazo(e.target.value)}
            />
          )}
        </Campo>
      </fieldset>
      {errorGeneral && <p role="alert">{errorGeneral}</p>}
      {aviso && <p role="status">{aviso}</p>}
      <button type="submit" disabled={enviando}>
        Guardar cambios
      </button>
    </form>
  );
}
