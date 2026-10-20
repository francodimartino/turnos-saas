import type { Permiso } from "./generado/permisos";
import { solicitar } from "./cliente";

import type { Esquemas } from "./tipos";

export type MiembroStaff = Esquemas["StaffItemOut"];
export type EstadoStaff = Esquemas["EstadoStaffOut"];

export const listarStaff = () => solicitar<Esquemas["ListaStaffOut"]>("/staff");
export const invitarStaff = (email: string, nombre: string, permissionKeys: Permiso[]) =>
  solicitar<Esquemas["InvitacionOut"]>("/staff/invitaciones", {
    metodo: "POST",
    cuerpo: { email, nombre, permission_keys: permissionKeys },
  });
export const cambiarPermisos = (id: string, permissionKeys: Permiso[]) =>
  solicitar<Esquemas["PermisosStaffOut"]>(`/staff/${encodeURIComponent(id)}/permisos`, {
    metodo: "PUT",
    cuerpo: { permission_keys: permissionKeys },
  });
export const cambiarOwner = (id: string, esOwner: boolean) =>
  solicitar<EstadoStaff>(`/staff/${encodeURIComponent(id)}/owner`, { metodo: "PUT", cuerpo: { is_owner: esOwner } });
export const desactivarStaff = (id: string) =>
  solicitar<EstadoStaff>(`/staff/${encodeURIComponent(id)}/desactivar`, { metodo: "POST" });
export const reactivarStaff = (id: string) =>
  solicitar<EstadoStaff>(`/staff/${encodeURIComponent(id)}/reactivar`, { metodo: "POST" });
