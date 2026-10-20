import { solicitar } from "./cliente";
import type { Esquemas } from "./tipos";

export type EstadoCalendar = Esquemas["EstadoCalendarOut"];
export type ResultadoCallback = Esquemas["ResultadoCallbackOut"];

export const estadoCalendar = (profesionalId: string) =>
  solicitar<EstadoCalendar>(`/profesionales/${encodeURIComponent(profesionalId)}/calendar`);
export const iniciarConexionCalendar = (profesionalId: string) =>
  solicitar<Esquemas["UrlConexionOut"]>(`/profesionales/${encodeURIComponent(profesionalId)}/calendar/conexion`, {
    metodo: "POST",
  });
export const desconectarCalendar = (profesionalId: string) =>
  solicitar<Esquemas["DesconexionOut"]>(`/profesionales/${encodeURIComponent(profesionalId)}/calendar`, {
    metodo: "DELETE",
  });
export const completarCallbackCalendar = (code: string, state: string, error: string) =>
  solicitar<ResultadoCallback>("/calendar/callback", {
    metodo: "POST",
    cuerpo: { code, state, error } satisfies Esquemas["CuerpoCallback"],
  });
