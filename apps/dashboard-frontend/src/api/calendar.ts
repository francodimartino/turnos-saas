import { solicitar } from "./cliente";

export interface EstadoCalendar {
  profesional_id: string;
  conectado: boolean;
  conectado_desde: string | null;
  google_configurado: boolean;
}

export interface ResultadoCallback {
  resultado: string;
  profesional_id: string;
}

export const estadoCalendar = (profesionalId: string) =>
  solicitar<EstadoCalendar>(`/profesionales/${encodeURIComponent(profesionalId)}/calendar`);
export const iniciarConexionCalendar = (profesionalId: string) =>
  solicitar<{ url: string }>(`/profesionales/${encodeURIComponent(profesionalId)}/calendar/conexion`, {
    metodo: "POST",
  });
export const desconectarCalendar = (profesionalId: string) =>
  solicitar<{ conectado: boolean; habia_conexion: boolean }>(
    `/profesionales/${encodeURIComponent(profesionalId)}/calendar`,
    { metodo: "DELETE" },
  );
export const completarCallbackCalendar = (code: string, state: string, error: string) =>
  solicitar<ResultadoCallback>("/calendar/callback", { metodo: "POST", cuerpo: { code, state, error } });
