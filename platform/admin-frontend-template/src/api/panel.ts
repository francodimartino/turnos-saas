import { solicitar, solicitarSinContenido } from "./cliente";
import type { Esquemas } from "./tipos";

/** Estados del tenant (ARCHITECTURE.md §2.2). `purging` es la purga en curso (sin acciones); `purged` es terminal: solo lo alcanza el job de purga. */
export type EstadoTenant = "active" | "suspended" | "inactive" | "purging" | "purged";

const ESTADOS_TENANT: readonly string[] = ["active", "suspended", "inactive", "purging", "purged"];

/** El contrato publica `status` como string plano: esta guarda lo acota a los estados que conoce el panel. */
export function esEstadoTenant(valor: string): valor is EstadoTenant {
  return ESTADOS_TENANT.includes(valor);
}

export type Tenant = Esquemas["TenantAdmin"];
export type Facturacion = Esquemas["Facturacion"];
export type EntradaAudit = Esquemas["EntradaAudit"];
export type NotificacionConProblema = Esquemas["NotificacionConProblema"];
export type ProblemasNotificaciones = Esquemas["ProblemasNotificaciones"];
export type ResultadoIntegracion = Esquemas["ResultadoIntegracion"];

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
}): Promise<Esquemas["InvitacionOut"]> {
  return solicitar("/tenants", { metodo: "POST", cuerpo: datos });
}

export type Transicion = "suspender" | "levantar-suspension" | "dar-de-baja" | "reactivar";

export function transicionarTenant(id: string, accion: Transicion): Promise<void> {
  return solicitarSinContenido(`/tenants/${id}/${accion}`, { metodo: "POST" });
}

export function reenviarInvitacion(
  id: string,
  correccion: { ownerEmail?: string; ownerNombre?: string } = {},
): Promise<Esquemas["InvitacionOut"]> {
  const cuerpo: Esquemas["ReenviarInvitacionIn"] = {};
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

export function listarSettings(): Promise<Esquemas["SettingsPlataformaOut"]> {
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
): Promise<Esquemas["SettingEditadoOut"]> {
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

export type Turno = Esquemas["BookingResumenOut"];
export type Cliente = Esquemas["ClienteOut"];
export type Servicio = Esquemas["ServicioOut"];
export type Profesional = Esquemas["ProfesionalOut"];
export type Pack = Esquemas["PackOut"];

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

export type VerificacionCadena = Esquemas["CadenaAuditOut"];

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
): Promise<Esquemas["DescarteLimpiezaOut"]> {
  return solicitar(`/tenants/${tenantId}/limpiezas-calendar/descartar`, {
    metodo: "POST",
    cuerpo: { event_id: eventId, motivo },
  });
}
