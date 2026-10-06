import { solicitar } from "./cliente";

/** El backend serializa los decimales como texto ("1500.00"); se acepta también número. */
export type Dinero = string | number;
export type ModoConfirmacion = "auto" | "approval" | "deposit";

export interface Servicio {
  id: string;
  name: string;
  duration_minutes: number;
  price: Dinero;
  deposit_amount: Dinero | null;
  is_active: boolean;
}

export interface DatosServicio {
  name: string;
  duration_minutes: number;
  price: string;
  is_active: boolean;
}

export interface Profesional {
  id: string;
  name: string;
  is_active: boolean;
  tenant_user_id: string | null;
}

export interface DatosProfesional {
  name: string;
  tenant_user_id: string | null;
  is_active: boolean;
}

export interface ServicioDeProfesional {
  professional_id: string;
  service_id: string;
  confirmation_mode: ModoConfirmacion;
}

export interface ReglaDisponibilidad {
  id: string;
  professional_id: string;
  /** 0 = domingo ... 6 = sábado. */
  day_of_week: number;
  /** Hora local del local, "HH:MM:SS". */
  start_time: string;
  end_time: string;
}

export interface DatosRegla {
  professional_id: string;
  day_of_week: number;
  start_time: string;
  end_time: string;
}

export interface Bloqueo {
  id: string;
  /** null: bloqueo de todo el local. */
  professional_id: string | null;
  start_at: string;
  end_at: string;
  reason: string | null;
}

export interface DatosBloqueo {
  professional_id: string | null;
  start_at: string;
  end_at: string;
  reason: string | null;
}

export type SettingsLocal = Record<string, unknown>;

const LIMITE = "limite=200";

export const listarServicios = () => solicitar<Servicio[]>(`/servicios?incluir_inactivos=true&${LIMITE}`);
export const crearServicio = (d: DatosServicio) => solicitar<Servicio>("/servicios", { metodo: "POST", cuerpo: d });
export const modificarServicio = (id: string, d: Partial<DatosServicio>) =>
  solicitar<Servicio>(`/servicios/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarServicio = (id: string) => solicitar<void>(`/servicios/${id}`, { metodo: "DELETE" });

export const listarProfesionales = () => solicitar<Profesional[]>(`/profesionales?incluir_inactivos=true&${LIMITE}`);
export const crearProfesional = (d: DatosProfesional) =>
  solicitar<Profesional>("/profesionales", { metodo: "POST", cuerpo: d });
export const modificarProfesional = (id: string, d: Partial<DatosProfesional>) =>
  solicitar<Profesional>(`/profesionales/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarProfesional = (id: string) => solicitar<void>(`/profesionales/${id}`, { metodo: "DELETE" });

export const serviciosDeProfesional = (id: string) =>
  solicitar<ServicioDeProfesional[]>(`/profesionales/${id}/servicios`);
export const asignarServicio = (profesionalId: string, servicioId: string, modo: Exclude<ModoConfirmacion, "deposit">) =>
  solicitar<ServicioDeProfesional>(`/profesionales/${profesionalId}/servicios/${servicioId}`, {
    metodo: "PUT",
    cuerpo: { confirmation_mode: modo },
  });
export const quitarServicio = (profesionalId: string, servicioId: string) =>
  solicitar<void>(`/profesionales/${profesionalId}/servicios/${servicioId}`, { metodo: "DELETE" });

export const listarReglas = (profesionalId: string) =>
  solicitar<ReglaDisponibilidad[]>(`/reglas-disponibilidad?professional_id=${encodeURIComponent(profesionalId)}&${LIMITE}`);
export const crearRegla = (d: DatosRegla) =>
  solicitar<ReglaDisponibilidad>("/reglas-disponibilidad", { metodo: "POST", cuerpo: d });
export const modificarRegla = (id: string, d: Partial<DatosRegla>) =>
  solicitar<ReglaDisponibilidad>(`/reglas-disponibilidad/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarRegla = (id: string) => solicitar<void>(`/reglas-disponibilidad/${id}`, { metodo: "DELETE" });

/** Bloqueos que todavía no terminaron (los pasados no aportan a la agenda). */
export const listarBloqueos = (desde: Date) =>
  solicitar<Bloqueo[]>(`/bloqueos-disponibilidad?desde=${encodeURIComponent(desde.toISOString())}&${LIMITE}`);
export const crearBloqueo = (d: DatosBloqueo) =>
  solicitar<Bloqueo>("/bloqueos-disponibilidad", { metodo: "POST", cuerpo: d });
export const modificarBloqueo = (id: string, d: Partial<DatosBloqueo>) =>
  solicitar<Bloqueo>(`/bloqueos-disponibilidad/${id}`, { metodo: "PATCH", cuerpo: d });
export const borrarBloqueo = (id: string) => solicitar<void>(`/bloqueos-disponibilidad/${id}`, { metodo: "DELETE" });

export const leerSettings = () => solicitar<{ settings: SettingsLocal }>("/local/settings");
/** Mezcla de primer nivel: `null` borra la clave. */
export const guardarSettings = (settings: SettingsLocal) =>
  solicitar<{ settings: SettingsLocal }>("/local/settings", { metodo: "PATCH", cuerpo: { settings } });
