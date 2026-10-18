import { solicitar } from "./cliente";

/** Estados del tenant (ARCHITECTURE.md §2.2). `purging` es la purga en curso (sin acciones); `purged` es terminal: solo lo alcanza el job de purga. */
export type EstadoTenant = "active" | "suspended" | "inactive" | "purging" | "purged";

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  plan: string;
  status: EstadoTenant;
  deactivated_at: string | null;
  /** Fecha (sin hora) `AAAA-MM-DD`. */
  paid_until: string | null;
  created_at: string;
  invitacion_pendiente: boolean;
  /** Identidad del owner; `null` si todavía no se conoce (por ejemplo, tenants anteriores a este dato). */
  owner_nombre: string | null;
  owner_email: string | null;
}

export type EstadoInvitacion = "ok" | "invite_pendiente";

export interface Facturacion {
  tenant_id: string;
  paid_until: string | null;
  billing_notes: string | null;
}

export interface EntradaAudit {
  id: string;
  tenant_id: string | null;
  actor_type: string;
  actor_id: string | null;
  action: string;
  entity: string | null;
  entity_id: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface NotificacionConProblema {
  id: string;
  tenant_id: string;
  end_customer_id: string;
  channel: string;
  template: string | null;
  status: string;
  retry_count: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
}

export interface ProblemasNotificaciones {
  notificaciones: NotificacionConProblema[];
  limpiezas_calendar: EntradaAudit[];
}

export interface ResultadoIntegracion {
  integration_id: string;
  tenant_id: string;
  integration_type: string;
  webhook_key: string;
}

export const CLAVE_PERIODO_GRACIA = "tenant_deletion_grace_period_days";

export function listarTenants(status?: string): Promise<Tenant[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return solicitar(`/tenants${q}`);
}

export function obtenerTenant(id: string): Promise<Tenant> {
  return solicitar(`/tenants/${id}`);
}

export function crearTenant(datos: {
  name: string;
  slug: string;
  owner_email: string;
  owner_nombre: string;
}): Promise<{ tenant_id: string; estado_invitacion: EstadoInvitacion }> {
  return solicitar("/tenants", { metodo: "POST", cuerpo: datos });
}

export type Transicion = "suspender" | "levantar-suspension" | "dar-de-baja" | "reactivar";

export function transicionarTenant(id: string, accion: Transicion): Promise<void> {
  return solicitar(`/tenants/${id}/${accion}`, { metodo: "POST" });
}

export function reenviarInvitacion(
  id: string,
  correccion: { ownerEmail?: string; ownerNombre?: string } = {},
): Promise<{ tenant_id: string; estado_invitacion: EstadoInvitacion }> {
  const cuerpo: { owner_email?: string; owner_nombre?: string } = {};
  if (correccion.ownerEmail) cuerpo.owner_email = correccion.ownerEmail;
  if (correccion.ownerNombre) cuerpo.owner_nombre = correccion.ownerNombre;
  return solicitar(`/tenants/${id}/reenviar-invitacion`, {
    metodo: "POST",
    cuerpo: Object.keys(cuerpo).length > 0 ? cuerpo : undefined,
  });
}

export function obtenerFacturacion(id: string): Promise<Facturacion> {
  return solicitar(`/tenants/${id}/facturacion`);
}

/** Se mandan los dos campos: `null` borra el valor. */
export function guardarFacturacion(
  id: string,
  datos: { paid_until: string | null; billing_notes: string | null },
): Promise<Facturacion> {
  return solicitar(`/tenants/${id}/facturacion`, { metodo: "PUT", cuerpo: datos });
}

export function listarSettings(): Promise<Record<string, unknown>> {
  return solicitar("/settings");
}

/** Mínimo del período de gracia que acepta el backend (días). */
export const MINIMO_PERIODO_GRACIA = 30;

/**
 * `confirmarReduccion` solo se envía cuando está definido: bajar el período de gracia sin `true` lo rechaza el
 * backend con 409 `confirmacion_reduccion_requerida` y no escribe nada.
 */
export function guardarSetting(
  clave: string,
  valor: unknown,
  confirmarReduccion?: boolean,
): Promise<{ key: string; desde: unknown; hasta: unknown }> {
  const cuerpo = confirmarReduccion === undefined ? { valor } : { valor, confirmar_reduccion: confirmarReduccion };
  return solicitar(`/settings/${encodeURIComponent(clave)}`, { metodo: "PUT", cuerpo });
}

export interface FiltrosAudit {
  action?: string;
  actor_type?: string;
  /** ISO 8601 con offset. */
  desde?: string;
  hasta?: string;
}

export function listarAudit(id: string, filtros: FiltrosAudit = {}): Promise<EntradaAudit[]> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(filtros)) if (v) q.set(k, v);
  const s = q.toString();
  return solicitar(`/tenants/${id}/audit-log${s ? `?${s}` : ""}`);
}

export function problemasNotificaciones(): Promise<ProblemasNotificaciones> {
  return solicitar("/notificaciones/problemas");
}

/** El `secret` se escribe y nunca vuelve: la respuesta no lo incluye. */
export function cargarTelegram(id: string, secret: string): Promise<ResultadoIntegracion> {
  return solicitar(`/tenants/${id}/integraciones/telegram_bot`, { metodo: "PUT", cuerpo: { secret } });
}

// --- Impersonación: solo GET (ARCHITECTURE.md §2.11.4). No hay ninguna función de escritura acá a propósito. ---

export interface Turno {
  id: string;
  start_at: string;
  end_at: string;
  status: string;
  customer_name: string;
  service_name: string;
  professional_name: string;
}

export interface Cliente {
  id: string;
  name: string | null;
  phone: string | null;
  channel: string;
  deleted_at: string | null;
}

export interface Servicio {
  id: string;
  name: string;
  duration_minutes: number;
  price: string | number | null;
  is_active: boolean;
}

export interface Profesional {
  id: string;
  name: string;
  is_active: boolean;
}

export interface Pack {
  id: string;
  end_customer_id: string;
  total_sessions: number;
  used_sessions: number;
  payment_status: string;
  expires_at: string | null;
}

export function impersonarBookings(id: string, desde: string, hasta: string): Promise<Turno[]> {
  const q = new URLSearchParams({ desde, hasta });
  return solicitar(`/tenants/${id}/impersonar/bookings?${q.toString()}`);
}

export function impersonarClientes(id: string): Promise<Cliente[]> {
  return solicitar(`/tenants/${id}/impersonar/clientes`);
}

export function impersonarServicios(id: string): Promise<Servicio[]> {
  return solicitar(`/tenants/${id}/impersonar/servicios`);
}

export function impersonarProfesionales(id: string): Promise<Profesional[]> {
  return solicitar(`/tenants/${id}/impersonar/profesionales`);
}

export function impersonarPacks(id: string): Promise<Pack[]> {
  return solicitar(`/tenants/${id}/impersonar/packs`);
}

export interface VerificacionCadena {
  tenant_id: string | null;
  integra: boolean;
  fila_invalida_id: string | null;
  fila_invalida_chain_seq: number | null;
}

/** Recorre la cadena de hashes de `audit_log` de un tenant. */
export function verificarCadenaTenant(id: string): Promise<VerificacionCadena> {
  return solicitar(`/tenants/${id}/audit-log/verificar-cadena`);
}

/** Lo mismo para las filas de la plataforma (sin tenant). */
export function verificarCadenaPlataforma(): Promise<VerificacionCadena> {
  return solicitar("/plataforma/audit-log/verificar-cadena");
}

/** Marca como descartada una limpieza de Calendar que no se puede resolver. El motivo es obligatorio. */
export function descartarLimpiezaCalendar(
  tenantId: string,
  eventId: string,
  motivo: string,
): Promise<{ tenant_id: string; event_id: string; estado: "descartada" }> {
  return solicitar(`/tenants/${tenantId}/limpiezas-calendar/descartar`, {
    metodo: "POST",
    cuerpo: { event_id: eventId, motivo },
  });
}
