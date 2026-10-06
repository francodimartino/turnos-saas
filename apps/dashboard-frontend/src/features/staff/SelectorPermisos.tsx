import { CATALOGO_PERMISOS } from "./catalogoPermisos";

interface Props {
  legend: string;
  seleccion: readonly string[];
  alCambiar: (nueva: string[]) => void;
}

/** Casillas del catálogo de permisos, cada una con su descripción. */
export function SelectorPermisos({ legend, seleccion, alCambiar }: Props) {
  function alternar(clave: string, marcado: boolean) {
    alCambiar(marcado ? [...seleccion, clave] : seleccion.filter((k) => k !== clave));
  }
  return (
    <fieldset>
      <legend>{legend}</legend>
      {CATALOGO_PERMISOS.map((p) => (
        <label key={p.clave} className="casilla">
          <input
            type="checkbox"
            checked={seleccion.includes(p.clave)}
            onChange={(e) => alternar(p.clave, e.target.checked)}
          />
          <span>
            <strong>{p.etiqueta}</strong>: {p.descripcion}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
