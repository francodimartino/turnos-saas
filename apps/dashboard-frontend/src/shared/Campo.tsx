import { useId, type ReactElement } from "react";

interface Props {
  etiqueta: string;
  error?: string | undefined;
  ayuda?: string;
  /** Recibe los atributos que vinculan el control con su etiqueta, su ayuda y su error. */
  children: (atributos: { id: string; "aria-describedby": string | undefined; "aria-invalid": boolean }) => ReactElement;
}

/** Etiqueta + control + mensaje de error pegado al campo. */
export function Campo({ etiqueta, error, ayuda, children }: Props) {
  const id = useId();
  const idError = `${id}-error`;
  const idAyuda = `${id}-ayuda`;
  const descripcion = [ayuda ? idAyuda : null, error ? idError : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="campo">
      <label htmlFor={id}>{etiqueta}</label>
      {children({ id, "aria-describedby": descripcion, "aria-invalid": error !== undefined })}
      {ayuda && (
        <small id={idAyuda} className="ayuda">
          {ayuda}
        </small>
      )}
      {error && (
        <p id={idError} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
