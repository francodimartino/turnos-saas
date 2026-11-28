import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { quitarLogo, subirLogo, type SettingsMarca } from "../../api/marca";
import { mensajeDeQuitarLogo, mensajeDeSubirLogo } from "./mensajes";
import { ACEPTA_LOGO, errorDeArchivoLogo } from "./validaciones";

interface Props {
  hayLogo: boolean;
  alCambiarSettings: (settings: SettingsMarca) => void;
  /** Imagen para la vista previa: la elegida o la última subida. */
  alCambiarImagen: (url: string | null) => void;
}

/** Subir y quitar el logo. El archivo se valida (tipo y peso) antes de enviarlo. */
export function SeccionLogo({ hayLogo, alCambiarSettings, alCambiarImagen }: Props) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [errorArchivo, setErrorArchivo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [enCurso, setEnCurso] = useState<"subir" | "quitar" | null>(null);
  const [vuelta, setVuelta] = useState(0);
  const urlActual = useRef<string | null>(null);
  const bloqueado = useRef(false);

  useEffect(
    () => () => {
      if (urlActual.current !== null) URL.revokeObjectURL(urlActual.current);
    },
    [],
  );

  function mostrar(url: string | null) {
    if (urlActual.current !== null) URL.revokeObjectURL(urlActual.current);
    urlActual.current = url;
    alCambiarImagen(url);
  }

  function elegir(e: ChangeEvent<HTMLInputElement>) {
    const elegido = e.target.files?.[0];
    setError(null);
    setAviso(null);
    if (elegido === undefined) return;
    const motivo = errorDeArchivoLogo(elegido);
    if (motivo !== null) {
      setErrorArchivo(motivo);
      setArchivo(null);
      return;
    }
    setErrorArchivo(null);
    setArchivo(elegido);
    mostrar(URL.createObjectURL(elegido));
  }

  async function ejecutar(accion: "subir" | "quitar") {
    if (bloqueado.current) return;
    bloqueado.current = true;
    setEnCurso(accion);
    setError(null);
    setAviso(null);
    try {
      if (accion === "subir") {
        if (archivo === null) return;
        const { settings } = await subirLogo(archivo);
        alCambiarSettings(settings);
        setArchivo(null);
        setVuelta((v) => v + 1);
        setAviso("Logo guardado.");
      } else {
        const { settings } = await quitarLogo();
        alCambiarSettings(settings);
        setArchivo(null);
        setVuelta((v) => v + 1);
        mostrar(null);
        setAviso("Logo quitado.");
      }
    } catch (e) {
      setError(accion === "subir" ? mensajeDeSubirLogo(e) : mensajeDeQuitarLogo(e));
    } finally {
      bloqueado.current = false;
      setEnCurso(null);
    }
  }

  return (
    <div className="panel" role="group" aria-label="Logo">
      <h2>Logo</h2>
      <p>PNG, JPG o WebP de hasta 1 MB. No se aceptan SVG.</p>
      <div className="campo">
        <label htmlFor="archivo-logo">Archivo del logo</label>
        <input
          key={vuelta}
          id="archivo-logo"
          type="file"
          accept={ACEPTA_LOGO}
          disabled={enCurso !== null}
          onChange={elegir}
          aria-describedby={errorArchivo ? "error-archivo-logo" : undefined}
        />
        {errorArchivo && (
          <p id="error-archivo-logo" role="alert">
            {errorArchivo}
          </p>
        )}
      </div>
      {archivo !== null && <p>El logo elegido todavía no se subió.</p>}
      <div className="acciones">
        <button type="button" disabled={archivo === null || enCurso !== null} onClick={() => void ejecutar("subir")}>
          Subir logo
        </button>
        {hayLogo && (
          <button type="button" disabled={enCurso !== null} onClick={() => void ejecutar("quitar")}>
            Quitar logo
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {aviso && <p role="status">{aviso}</p>}
    </div>
  );
}
