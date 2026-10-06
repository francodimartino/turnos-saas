-- Migración de plataforma 7: identidad (email y nombre) de los tenant_users, leída desde auth.users.
-- Decisión de producto (2026-10-06): el staff y los owners se ven siempre con nombre y email.
--
--   tenant_users no guarda nombre ni email: viven en auth.users (email y raw_user_meta_data ->> 'full_name').
--   dashboard_service y platform_admin no tienen acceso a auth.users, por eso la lectura pasa por esta función
--   SECURITY DEFINER. Si el usuario aún no vinculó su cuenta (invitación pendiente), el email sale de
--   tenant_users.pending_invite_email y el nombre es null.
--
-- Autorización (session_user, no se puede esquivar con SET ROLE):
--   dashboard_service: solo si app.current_tenant_id = p_tenant_id.
--   platform_admin: cualquier tenant.
-- EXECUTE solo para esos dos roles.

create function public.get_tenant_staff_identity(p_tenant_id uuid)
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
           (au.raw_user_meta_data ->> 'full_name')::text
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
