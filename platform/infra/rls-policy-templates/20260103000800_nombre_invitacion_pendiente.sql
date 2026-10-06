-- Migración de plataforma 8: nombre del owner/staff invitado, guardado solo mientras la invitación está pendiente.
-- Decisión de producto (2026-10-06): el staff y los owners se ven siempre con nombre y email, también antes de
-- que vinculen su cuenta.
--
--   1. tenant_users.pending_invite_name (text, null): mismo tratamiento que pending_invite_email (PII de vida
--      limitada). Se anula al vincular la cuenta (trigger, ampliado acá) y en la purga (migración del producto
--      que sigue). Sin grants nuevos: solo platform_admin (grants de tabla) la lee y escribe; dashboard_service
--      y platform_admin_readonly conservan sus grants por columna de siempre, que no la incluyen.
--   2. get_tenant_staff_identity devuelve pending_invite_name como nombre cuando la cuenta no tiene full_name
--      (invitación pendiente). Misma firma y mismos grants.

alter table public.tenant_users
  add column pending_invite_name text null;

-- ---------------------------------------------------------------------------
-- Anulación automática al vincular la cuenta (ahora también el nombre)
-- ---------------------------------------------------------------------------
create or replace function public.tenant_users_limpiar_email_pendiente()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.auth_user_id is not null then
    new.pending_invite_email := null;
    new.pending_invite_name := null;
  end if;
  return new;
end
$$;

revoke all on function public.tenant_users_limpiar_email_pendiente()
  from public, anon, authenticated, service_role;

drop trigger tenant_users_limpiar_email_pendiente on public.tenant_users;
create trigger tenant_users_limpiar_email_pendiente
  before insert or update of auth_user_id, pending_invite_email, pending_invite_name on public.tenant_users
  for each row execute function public.tenant_users_limpiar_email_pendiente();

-- ---------------------------------------------------------------------------
-- Identidad del staff: el nombre pendiente mientras no hay cuenta
-- ---------------------------------------------------------------------------
create or replace function public.get_tenant_staff_identity(p_tenant_id uuid)
returns table (tenant_user_id uuid, email text, nombre text)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_caller_tenant uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
begin
  if session_user = 'platform_admin' then
    null;
  elsif session_user = 'dashboard_service' then
    if v_caller_tenant is null or v_caller_tenant is distinct from p_tenant_id then
      raise exception 'el tenant pedido no coincide con el contexto'
        using errcode = 'insufficient_privilege';
    end if;
  else
    raise exception 'rol no autorizado para get_tenant_staff_identity: %', session_user
      using errcode = 'insufficient_privilege';
  end if;

  return query
    select tu.id,
           coalesce(au.email::text, tu.pending_invite_email),
           coalesce((au.raw_user_meta_data ->> 'full_name')::text, tu.pending_invite_name)
      from public.tenant_users tu
      left join auth.users au on au.id = tu.auth_user_id
     where tu.tenant_id = p_tenant_id
     order by tu.created_at, tu.id;
end
$$;

revoke all on function public.get_tenant_staff_identity(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.get_tenant_staff_identity(uuid)
  to dashboard_service, platform_admin;
