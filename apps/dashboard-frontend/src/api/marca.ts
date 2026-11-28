import { solicitar } from "./cliente";
import type { Esquemas } from "./tipos";

export type SettingsMarca = Esquemas["SettingsLocalOut"];

/** Sube el logo: el cuerpo es la imagen tal cual (no multipart). El backend decide el tipo por los bytes. */
export const subirLogo = (imagen: Blob) =>
  solicitar<Esquemas["SettingsOut"]>("/local/logo", { metodo: "POST", binario: imagen });

export const quitarLogo = () => solicitar<Esquemas["SettingsOut"]>("/local/logo", { metodo: "DELETE" });

/** Cambios de apariencia: `null` borra la clave (vuelve al valor por defecto). */
export interface CambiosApariencia {
  color_principal?: string | null;
  color_secundario?: string | null;
  cancelacion_plazo_horas?: number | null;
}

export const guardarApariencia = (cambios: CambiosApariencia) =>
  solicitar<Esquemas["SettingsOut"]>("/local/apariencia", { metodo: "PATCH", cuerpo: cambios });
