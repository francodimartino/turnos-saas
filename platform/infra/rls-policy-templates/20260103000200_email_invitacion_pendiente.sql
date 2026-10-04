-- Migración de plataforma 6: email del owner invitado, guardado solo mientras la invitación está pendiente.
-- Decisión de producto (2026-10-04): reenviar la invitación ya no obliga a volver a escribir el email.
--
--   1. tenant_users.pending_invite_email (text, null): el email es PII, por eso se limita su vida y su lectura.
--   2. Se anula solo cuando la cuenta queda vinculada (auth_user_id deja de ser null): desde la base no hay
--      forma de ver la confirmación de la cuenta de Auth, así que el vínculo es el momento de anulación.
--      La purga de tenant también lo anula (purge_tenant_data, en la migración del producto que sigue).
--   3. Grants por columna (§3.2): solo platform_admin lee y escribe la columna. dashboard_service y
--      platform_admin_readonly pasan de grants de tabla a grants por columna con todas las columnas de
--      siempre menos esta; una columna futura requiere un grant explícito (no se filtra por accidente).

alter table public.tenant_users
  add column pending_invite_email text null;

-- ---------------------------------------------------------------------------
-- Anulación automática al vincular la cuenta
-- ---------------------------------------------------------------------------
create function public.tenant_users_limpiar_email_pendiente()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.auth_user_id is not null then
    new.pending_invite_email := null;
  end if;
  return new;
end
$$;

revoke all on function public.tenant_users_limpiar_email_pendiente()
  from public, anon, authenticated, service_role;

create trigger tenant_users_limpiar_email_pendiente
  before insert or update of auth_user_id, pending_invite_email on public.tenant_users
  for each row execute function public.tenant_users_limpiar_email_pendiente();

-- ---------------------------------------------------------------------------
-- Grants por columna
-- ---------------------------------------------------------------------------
revoke select, insert, update on public.tenant_users from dashboard_service;
grant select (id, tenant_id, auth_user_id, is_owner, is_active, created_at)
  on public.tenant_users to dashboard_service;
grant insert (id, tenant_id, auth_user_id, is_owner, is_active, created_at)
  on public.tenant_users to dashboard_service;
grant update (id, tenant_id, auth_user_id, is_owner, is_active, created_at)
  on public.tenant_users to dashboard_service;

revoke select on public.tenant_users from platform_admin_readonly;
grant select (id, tenant_id, auth_user_id, is_owner, is_active, created_at)
  on public.tenant_users to platform_admin_readonly;

-- platform_admin conserva select, insert, update de tabla (incluye la columna nueva)
