/** Reglas del cliente para la apariencia del negocio; el backend vuelve a validar todo. */

/** Tope del logo: 1 MiB, el mismo que aplica el backend. */
export const TAMANO_MAXIMO_LOGO = 1024 * 1024;
export const PLAZO_MINIMO_HORAS = 0;
export const PLAZO_MAXIMO_HORAS = 720;
export const PLAZO_POR_DEFECTO_HORAS = 24;

const TIPOS_LOGO = ["image/png", "image/jpeg", "image/webp"];
const EXTENSIONES_LOGO = [".png", ".jpg", ".jpeg", ".webp"];
export const ACEPTA_LOGO = [...TIPOS_LOGO, ...EXTENSIONES_LOGO].join(",");

/** `null` si el archivo se puede subir; si no, el motivo para mostrarle a la persona. Exige tipo MIME y extensión. */
export function errorDeArchivoLogo(archivo: { name: string; type: string; size: number }): string | null {
  const nombre = archivo.name.toLowerCase();
  const tipoValido = TIPOS_LOGO.includes(archivo.type.toLowerCase());
  const extensionValida = EXTENSIONES_LOGO.some((e) => nombre.endsWith(e));
  if (!tipoValido || !extensionValida) return "El logo tiene que ser una imagen PNG, JPG o WebP (no se aceptan otros formatos, como SVG).";
  if (archivo.size === 0) return "El archivo está vacío.";
  if (archivo.size > TAMANO_MAXIMO_LOGO) return "El logo pesa más de 1 MB: elegí una imagen más liviana.";
  return null;
}

const PATRON_COLOR = /^#[0-9A-Fa-f]{6}$/;

export const esColorValido = (valor: string): boolean => PATRON_COLOR.test(valor);

/** `null` si el color es válido o está vacío (vacío = sin color propio). */
export function errorDeColor(valor: string): string | null {
  const limpio = valor.trim();
  return limpio === "" || esColorValido(limpio) ? null : "Ingresá un color con el formato #RRGGBB, por ejemplo #1F6FEB.";
}

/** Plazo de cancelación en horas: entero de 0 a 720; vacío = valor por defecto. `null` si es válido. */
export function errorDePlazo(valor: string): string | null {
  const limpio = valor.trim();
  if (limpio === "") return null;
  if (!/^\d+$/.test(limpio)) return "Ingresá un número entero de horas, sin decimales ni signos.";
  const horas = Number(limpio);
  if (horas < PLAZO_MINIMO_HORAS || horas > PLAZO_MAXIMO_HORAS)
    return `Ingresá un plazo entre ${PLAZO_MINIMO_HORAS} y ${PLAZO_MAXIMO_HORAS} horas.`;
  return null;
}
