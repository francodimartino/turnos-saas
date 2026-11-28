import { esColorValido } from "./validaciones";

const PRINCIPAL_NEUTRO = "#e4e7eb";
const SECUNDARIO_NEUTRO = "#9aa5b1";

/** Texto oscuro o claro según la luminosidad del fondo, para que se lea sobre el color elegido. */
export function colorDeTextoSobre(fondo: string): string {
  const r = parseInt(fondo.slice(1, 3), 16);
  const g = parseInt(fondo.slice(3, 5), 16);
  const b = parseInt(fondo.slice(5, 7), 16);
  const luminosidad = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminosidad > 0.6 ? "#1f2933" : "#ffffff";
}

interface Props {
  principal: string;
  secundario: string;
  /** Imagen para mostrar (la elegida o la última subida); `null` si no hay una para previsualizar. */
  logoUrl: string | null;
  /** Hay un logo guardado en el negocio aunque no se pueda mostrar acá (por ejemplo, tras recargar la página). */
  hayLogoGuardado: boolean;
}

/** Cómo se vería la marca del negocio con los colores y el logo actuales (todavía sin guardar). */
export function VistaPrevia({ principal, secundario, logoUrl, hayLogoGuardado }: Props) {
  const fondo = esColorValido(principal.trim()) ? principal.trim() : PRINCIPAL_NEUTRO;
  const acento = esColorValido(secundario.trim()) ? secundario.trim() : SECUNDARIO_NEUTRO;
  return (
    <div className="vista-previa" role="group" aria-label="Vista previa de la marca">
      <div className="vista-previa-cabecera" data-testid="previa-cabecera" style={{ backgroundColor: fondo, color: colorDeTextoSobre(fondo) }}>
        {logoUrl !== null && <img src={logoUrl} alt="Logo del negocio" height={32} />}
        <strong>Tu negocio</strong>
      </div>
      {logoUrl === null && (
        <p>{hayLogoGuardado ? "Hay un logo guardado; se ve en las páginas de reserva." : "Todavía no hay un logo."}</p>
      )}
      <span className="vista-previa-boton" data-testid="previa-boton" style={{ backgroundColor: acento, color: colorDeTextoSobre(acento) }}>
        Reservar turno
      </span>
    </div>
  );
}
