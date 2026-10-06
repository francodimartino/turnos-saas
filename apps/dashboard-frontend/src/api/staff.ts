import { solicitar } from "./cliente";

export interface MiembroStaff {
  id: string;
  is_owner: boolean;
  is_active: boolean;
  /** Es la persona de la sesión actual. */
  es_yo: boolean;
  created_at: string;
  permission_keys: string[];
}

export interface EstadoStaff {
  id: string;
  is_owner: boolean;
  is_active: boolean;
}

export const listarStaff = () => solicitar<{ staff: MiembroStaff[] }>("/staff");
export const invitarStaff = (email: string, permissionKeys: string[]) =>
  solicitar<{ tenant_user_id: string; creado: boolean }>("/staff/invitaciones", {
    metodo: "POST",
    cuerpo: { email, permission_keys: permissionKeys },
  });
export const cambiarPermisos = (id: string, permissionKeys: string[]) =>
  solicitar<{ id: string; permission_keys: string[] }>(`/staff/${encodeURIComponent(id)}/permisos`, {
    metodo: "PUT",
    cuerpo: { permission_keys: permissionKeys },
  });
export const cambiarOwner = (id: string, esOwner: boolean) =>
  solicitar<EstadoStaff>(`/staff/${encodeURIComponent(id)}/owner`, { metodo: "PUT", cuerpo: { is_owner: esOwner } });
export const desactivarStaff = (id: string) =>
  solicitar<EstadoStaff>(`/staff/${encodeURIComponent(id)}/desactivar`, { metodo: "POST" });
export const reactivarStaff = (id: string) =>
  solicitar<EstadoStaff>(`/staff/${encodeURIComponent(id)}/reactivar`, { metodo: "POST" });
