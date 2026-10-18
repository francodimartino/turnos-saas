import type { EstadoTenant } from "../api/panel";

export const ETIQUETAS_ESTADO: Record<EstadoTenant, string> = {
  active: "Activo",
  suspended: "Suspendido",
  inactive: "De baja",
  purging: "Purgando",
  purged: "Purgado",
};

/** Badge del estado del tenant (§2.2). El texto siempre dice el estado: el color solo refuerza. */
export function EstadoTenantBadge({ estado }: { estado: EstadoTenant }) {
  return <span className={`badge badge-${estado}`}>{ETIQUETAS_ESTADO[estado] ?? estado}</span>;
}
