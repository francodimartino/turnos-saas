-- Purga: la cuenta de Auth de un platform admin nunca se borra, aunque sea staff u owner del tenant que se purga.
-- `unlink_platform_admin_accounts` desvincula de ese tenant (auth_user_id a null, como lo haría el borrado de la
-- cuenta por la FK on delete set null) las filas de tenant_users cuya cuenta figura en platform_admins y devuelve
-- esas cuentas: el job las excluye de la eliminación en Auth y mark_tenant_purged ya no encuentra cuentas
-- vinculadas. Solo opera sobre un tenant en purging; idempotente (una segunda llamada no devuelve nada).
create function public.unlink_platform_admin_accounts(p_tenant_id uuid)
returns setof uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_status text;
begin
  select t.status into v_status from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status <> 'purging' then
    raise exception 'operacion rechazada: el tenant no esta en purga' using errcode = 'check_violation';
  end if;

  return query
    with admins as (
      select tu.id, tu.auth_user_id as cuenta
        from public.tenant_users tu
       where tu.tenant_id = p_tenant_id
         and tu.auth_user_id is not null
         and exists (select 1 from public.platform_admins pa where pa.auth_user_id = tu.auth_user_id)
    ),
    desvinculadas as (
      update public.tenant_users tu set auth_user_id = null
        from admins a
       where tu.id = a.id
      returning a.id, a.cuenta
    )
    select d.cuenta from desvinculadas d order by d.id;
end
$$;

revoke all on function public.unlink_platform_admin_accounts(uuid) from public, anon, authenticated, service_role;
grant execute on function public.unlink_platform_admin_accounts(uuid) to purge_job;
