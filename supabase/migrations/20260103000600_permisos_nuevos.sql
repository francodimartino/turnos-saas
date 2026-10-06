-- Migración de plataforma 8: tres permisos nuevos en el catálogo public.permissions (§2.4).
--
--   manage_customers : alta y edición de clientes (end_customers) desde el dashboard.
--   delete_customers : derecho al olvido desde el dashboard (anonymize_end_customer, §3.5).
--   manage_schedule  : horarios, bloqueos de disponibilidad y datos del local.
--
-- Idempotente: las claves ya existentes se respetan. El owner las tiene implícitamente (is_owner, §2.4):
-- no hace falta asignárselas en tenant_user_permissions.
insert into public.permissions (key)
values ('manage_customers'), ('delete_customers'), ('manage_schedule')
on conflict (key) do nothing;
