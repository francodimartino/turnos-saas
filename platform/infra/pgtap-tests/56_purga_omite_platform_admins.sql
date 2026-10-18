-- unlink_platform_admin_accounts: la purga no borra la cuenta de Auth de un platform admin.
-- Desvincula de tenant_users (auth_user_id a null) solo las cuentas que figuran en platform_admins y solo en un
-- tenant en purging; devuelve esas cuentas para que el job las excluya de la eliminación en Auth.
begin;
select plan(11);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['purge_job', 'bot_service'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

create function pg_temp.como(p_rol text, p_sql text) returns text
language plpgsql as $$
declare
  v text;
begin
  execute format('set role %I', p_rol);
  begin
    execute p_sql into v;
    reset role;
    return coalesce(v, '<null>');
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
end
$$;

insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000d1', 'En purga', 'pa-purging', 'inactive', now() - interval '100 days'),
  ('00000000-0000-0000-0000-0000000000d2', 'Inactivo', 'pa-inactivo', 'inactive', now() - interval '100 days');
select public.begin_tenant_purge('00000000-0000-0000-0000-0000000000d1');

insert into auth.users (id) values
  ('00000000-0000-0000-0000-0000000000a1'), ('00000000-0000-0000-0000-0000000000a2'),
  ('00000000-0000-0000-0000-0000000000a3');
insert into public.platform_admins (auth_user_id) values ('00000000-0000-0000-0000-0000000000a1');
insert into public.tenant_users (id, tenant_id, auth_user_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1', true),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a2', false),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000d2', '00000000-0000-0000-0000-0000000000a3', true);

select ok(has_function_privilege('purge_job', 'public.unlink_platform_admin_accounts(uuid)', 'execute'),  -- 1
  'purge_job la ejecuta');
select is_empty(  -- 2
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.unlink_platform_admin_accounts(uuid)', 'execute')$$,
  'ningún otro rol la ejecuta');
select is(pg_temp.como('bot_service',  -- 3
  $$select count(*)::text from public.unlink_platform_admin_accounts('00000000-0000-0000-0000-0000000000d1')$$),
  'err 42501', 'bot_service no puede');
select is(pg_temp.como('purge_job',  -- 4
  $$select count(*)::text from public.unlink_platform_admin_accounts('00000000-0000-0000-0000-0000000000d2')$$),
  'err 23514', 'rechaza un tenant que no está en purging');
select is(pg_temp.como('purge_job',  -- 5
  $$select count(*)::text from public.unlink_platform_admin_accounts('00000000-0000-0000-0000-0000000000ff')$$),
  'err P0002', 'rechaza un tenant inexistente');
select is((select auth_user_id from public.tenant_users where id = '00000000-0000-0000-0000-0000000000b3'),  -- 6
  '00000000-0000-0000-0000-0000000000a3'::uuid, 'el rechazo no tocó nada');

select is(pg_temp.como('purge_job',  -- 7
  $$select string_agg(u::text, ',') from public.unlink_platform_admin_accounts('00000000-0000-0000-0000-0000000000d1') u$$),
  '00000000-0000-0000-0000-0000000000a1', 'devuelve solo la cuenta del platform admin');
select is((select auth_user_id from public.tenant_users where id = '00000000-0000-0000-0000-0000000000b1'), null,  -- 8
  'la fila del admin queda desvinculada');
select is((select auth_user_id from public.tenant_users where id = '00000000-0000-0000-0000-0000000000b2'),  -- 9
  '00000000-0000-0000-0000-0000000000a2'::uuid, 'la cuenta de staff común sigue vinculada');
select is((select count(*) from auth.users where id = '00000000-0000-0000-0000-0000000000a1'), 1::bigint,  -- 10
  'la cuenta de Auth del admin no se toca');
select is(pg_temp.como('purge_job',  -- 11
  $$select count(*)::text from public.unlink_platform_admin_accounts('00000000-0000-0000-0000-0000000000d1')$$),
  '0', 'idempotente: una segunda llamada no devuelve nada');

select * from finish();
rollback;
