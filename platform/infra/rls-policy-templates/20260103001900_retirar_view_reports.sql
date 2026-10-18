-- Migración de plataforma: retira el permiso view_reports del catálogo public.permissions (§2.4).
--
-- Decisión de producto: view_reports no habilitaba nada (no hay reportes); los packs y la
-- creación de turnos siguen bajo approve_bookings. Se borran primero las asignaciones
-- (tenant_user_permissions.permission_key referencia permissions.key) y después la clave.
--
-- DESTRUCTIVA (borra filas de asignación): pasa por el gate migracion_destructiva.
-- Idempotente: si la clave ya no existe, los delete no tocan nada. No modifica el resto del
-- catálogo ni escribe en audit_log (estas tablas no tienen trigger de auditoría).
delete from public.tenant_user_permissions where permission_key = 'view_reports';
delete from public.permissions where key = 'view_reports';
