import { solicitar, solicitarSinContenido } from "./cliente";
import type { Esquemas } from "./tipos";

/** Montos de entrada: el backend acepta número o texto ("1500.00"); en las respuestas salen como número. */
export type Dinero = string | number;
export type ModoConfirmacion = Esquemas["ServicioDeProfesionalIn"]["confirmation_mode"];

export type Servicio = Esquemas["ServicioOut"];
export type DatosServicio = Esquemas["ServicioIn"];
export type Profesional = Esquemas["ProfesionalOut"];
export type DatosProfesional = Esquemas["ProfesionalIn"];
export type ServicioDeProfesional = Esquemas["AsignacionOut"];
/** `day_of_week`: 0 = domingo ... 6 = sábado. `start_time`/`end_time`: hora local del local, "HH:MM:SS". */
export type ReglaDisponibilidad = Esquemas["ReglaOut"];
export type DatosRegla = Esquemas["ReglaIn"];
/** `professional_id` null: bloqueo de todo el local. */
export type Bloqueo = Esquemas["BloqueoOut"];
export type DatosBloqueo = Esquemas["BloqueoIn"];
export type SettingsLocal = Esquemas["SettingsLocalOut"];

const LIMITE = "limite=200";

export const listarServicios = () => solicitar<Servicio[]>(`/servicios?incluir_inactivos=true&${LIMITE}`);
export const crearServicio = (d: DatosServicio) => solicitar<Servicio>("/servicios", { metodo: "POST", cuerpo: d });
export const modificarServicio = (id: string, d: Esquemas["ServicioParche"]) =>
  solicitar<Servicio>(`/servicios/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarServicio = (id: string) => solicitarSinContenido(`/servicios/${id}`, { metodo: "DELETE" });

export const listarProfesionales = () => solicitar<Profesional[]>(`/profesionales?incluir_inactivos=true&${LIMITE}`);
export const crearProfesional = (d: DatosProfesional) =>
  solicitar<Profesional>("/profesionales", { metodo: "POST", cuerpo: d });
export const modificarProfesional = (id: string, d: Esquemas["ProfesionalParche"]) =>
  solicitar<Profesional>(`/profesionales/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarProfesional = (id: string) => solicitarSinContenido(`/profesionales/${id}`, { metodo: "DELETE" });

export const serviciosDeProfesional = (id: string) =>
  solicitar<ServicioDeProfesional[]>(`/profesionales/${id}/servicios`);
export const asignarServicio = (profesionalId: string, servicioId: string, modo: Exclude<ModoConfirmacion, "deposit">) =>
  solicitar<ServicioDeProfesional>(`/profesionales/${profesionalId}/servicios/${servicioId}`, {
    metodo: "PUT",
    cuerpo: { confirmation_mode: modo },
  });
export const quitarServicio = (profesionalId: string, servicioId: string) =>
  solicitarSinContenido(`/profesionales/${profesionalId}/servicios/${servicioId}`, { metodo: "DELETE" });

export const listarReglas = (profesionalId: string) =>
  solicitar<ReglaDisponibilidad[]>(`/reglas-disponibilidad?professional_id=${encodeURIComponent(profesionalId)}&${LIMITE}`);
export const crearRegla = (d: DatosRegla) =>
  solicitar<ReglaDisponibilidad>("/reglas-disponibilidad", { metodo: "POST", cuerpo: d });
export const modificarRegla = (id: string, d: Esquemas["ReglaParche"]) =>
  solicitar<ReglaDisponibilidad>(`/reglas-disponibilidad/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarRegla = (id: string) => solicitarSinContenido(`/reglas-disponibilidad/${id}`, { metodo: "DELETE" });

/** Bloqueos que todavía no terminaron (los pasados no aportan a la agenda). */
export const listarBloqueos = (desde: Date) =>
  solicitar<Bloqueo[]>(`/bloqueos-disponibilidad?desde=${encodeURIComponent(desde.toISOString())}&${LIMITE}`);
export const crearBloqueo = (d: DatosBloqueo) =>
  solicitar<Bloqueo>("/bloqueos-disponibilidad", { metodo: "POST", cuerpo: d });
export const modificarBloqueo = (id: string, d: Esquemas["BloqueoParche"]) =>
  solicitar<Bloqueo>(`/bloqueos-disponibilidad/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarBloqueo = (id: string) => solicitarSinContenido(`/bloqueos-disponibilidad/${id}`, { metodo: "DELETE" });

export const leerSettings = () => solicitar<Esquemas["SettingsOut"]>("/local/settings");
/** Mezcla de primer nivel: `null` borra la clave. */
export const guardarSettings = (settings: Esquemas["SettingsParche"]["settings"]) =>
  solicitar<Esquemas["SettingsOut"]>("/local/settings", { metodo: "PATCH", cuerpo: { settings } });
