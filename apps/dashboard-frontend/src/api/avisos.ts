import { solicitar } from "./cliente";
import type { Esquemas } from "./tipos";

export type AvisosNegocio = Esquemas["AvisosOut"];
export type DestinatarioAvisos = Esquemas["DestinatarioOut"];
/** Un cambio del pedido: los campos con valor por defecto del contrato (`por_defecto`, `canal_whatsapp`) son opcionales. */
export type CambioAvisos = Omit<Esquemas["CambioAvisosCuerpo"], "por_defecto" | "canal_whatsapp"> &
  Partial<Pick<Esquemas["CambioAvisosCuerpo"], "por_defecto" | "canal_whatsapp">>;

export const leerAvisos = () => solicitar<AvisosNegocio>("/local/avisos");

export const guardarAvisos = (cambios: CambioAvisos[]) =>
  solicitar<AvisosNegocio>("/local/avisos", { metodo: "PUT", cuerpo: { cambios } });
