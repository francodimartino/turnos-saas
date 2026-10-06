-- tenant_users.pending_invite_name: el nombre del owner/staff invitado se guarda mientras la invitación está
-- pendiente (auth_user_id null), con el mismo tratamiento que pending_invite_email: se anula al vincular la
-- cuenta y en la purga, solo platform_admin lo lee y escribe, y get_tenant_staff_identity lo devuelve como
-- nombre mientras no hay cuenta. Los nombres y emails se generan por corrida (ninguno literal).
begin;
select plan(22);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job', 'scheduler_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

select set_config('test.nombre', 'nom-' || md5(random()::text), false);
select set_config('test.email', 'nom-' || md5(random()::text) || '@prueba.invalid', false);

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
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- La columna
select has_column('public', 'tenant_users', 'pending_invite_name', 'tenant_users.pending_invite_name existe');  -- 1
select col_type_is('public', 'tenant_users', 'pending_invite_name', 'text', 'es text');  -- 2
select col_is_null('public', 'tenant_users', 'pending_invite_name', 'admite null');  -- 3

-- Grants por columna
select ok(has_column_privilege('platform_admin', 'public.tenant_users', 'pending_invite_name', 'SELECT'),  -- 4
  'platform_admin lee la columna');
select ok(has_column_privilege('platform_admin', 'public.tenant_users', 'pending_invite_name', 'INSERT, UPDATE'),  -- 5
  'platform_admin escribe la columna');
select is_empty(  -- 6
  $$select r from unnest(array['dashboard_service', 'platform_admin_readonly', 'bot_service', 'purge_job',
                               'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_column_privilege(r, 'public.tenant_users', 'pending_invite_name', 'SELECT')
        or has_column_privilege(r, 'public.tenant_users', 'pending_invite_name', 'INSERT')
        or has_column_privilege(r, 'public.tenant_users', 'pending_invite_name', 'UPDATE')$$,
  'ningún otro rol lee ni escribe la columna directamente');

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000f1', 'Nombre pend', 'nombre-pend-1'),
  ('00000000-0000-0000-0000-0000000000f2', 'Nombre pend 2', 'nombre-pend-2');

select is(pg_temp.como('platform_admin',  -- 7
  $$insert into public.tenant_users (id, tenant_id, is_owner, pending_invite_email, pending_invite_name)
    values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000f1', true,
            current_setting('test.email'), current_setting('test.nombre')) returning 'ok'$$), 'ok',
  'platform_admin inserta un owner con nombre pendiente');
select is(pg_temp.como('platform_admin',  -- 8
  $$select (pending_invite_name = current_setting('test.nombre'))::text from public.tenant_users
     where id = '00000000-0000-0000-0000-0000000000d1'$$), 'true', 'platform_admin lo lee');
select is(pg_temp.como('platform_admin',  -- 9
  $$update public.tenant_users set pending_invite_name = current_setting('test.nombre')
     where id = '00000000-0000-0000-0000-0000000000d1' returning 'ok'$$), 'ok', 'platform_admin lo actualiza');
select ok(pg_temp.como('dashboard_service',  -- 10
  $$select pending_invite_name from public.tenant_users limit 1$$) like 'err 42501%',
  'dashboard_service: select de la columna rechazado');
select ok(pg_temp.como('dashboard_service',  -- 11
  $$select count(*)::text from (select * from public.tenant_users) t$$) like 'err 42501%',
  'dashboard_service: select * rechazado');
select ok(pg_temp.como('platform_admin_readonly',  -- 12
  $$select pending_invite_name from public.tenant_users limit 1$$) like 'err 42501%',
  'platform_admin_readonly: select de la columna rechazado');
select ok(pg_temp.como('dashboard_service',  -- 13
  $$update public.tenant_users set pending_invite_name = current_setting('test.nombre')$$) like 'err 42501%',
  'dashboard_service: no escribe la columna');

-- (get_tenant_staff_identity depende de session_user: se prueba con conexiones reales en
-- tests/integracion/test_identidad_staff.py)

-- Se anula solo al vincular la cuenta
insert into auth.users (id, raw_user_meta_data) values
  ('00000000-0000-0000-0000-0000000000b1', '{}'::jsonb), ('00000000-0000-0000-0000-0000000000b2', '{}'::jsonb);

update public.tenant_users set is_active = true where id = '00000000-0000-0000-0000-0000000000d1';
select is((select pending_invite_name = current_setting('test.nombre') from public.tenant_users  -- 14
            where id = '00000000-0000-0000-0000-0000000000d1'), true,
  'actualizar otra columna con la invitación pendiente no anula el nombre');
update public.tenant_users set auth_user_id = '00000000-0000-0000-0000-0000000000b1'
 where id = '00000000-0000-0000-0000-0000000000d1';
select is((select pending_invite_name from public.tenant_users  -- 15
            where id = '00000000-0000-0000-0000-0000000000d1'), null,
  'al vincular la cuenta el nombre pendiente queda en null');
select is((select pending_invite_email from public.tenant_users  -- 16
            where id = '00000000-0000-0000-0000-0000000000d1'), null,
  'y el email pendiente también (el trigger sigue anulando ambos)');
update public.tenant_users set pending_invite_name = current_setting('test.nombre')
 where id = '00000000-0000-0000-0000-0000000000d1';
select is((select pending_invite_name from public.tenant_users  -- 17
            where id = '00000000-0000-0000-0000-0000000000d1'), null,
  'con la cuenta vinculada no se puede guardar un nombre pendiente');

insert into public.tenant_users (id, tenant_id, auth_user_id, pending_invite_name) values
  ('00000000-0000-0000-0000-0000000000d2', '00000000-0000-0000-0000-0000000000f1',
   '00000000-0000-0000-0000-0000000000b2', current_setting('test.nombre'));
select is((select pending_invite_name from public.tenant_users  -- 18
            where id = '00000000-0000-0000-0000-0000000000d2'), null,
  'un alta ya vinculada no guarda el nombre');

-- La purga lo anula
insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000f3', 'Vencido', 'nombre-pend-vencido', 'inactive', now() - interval '100 days');
insert into public.tenant_users (id, tenant_id, is_owner, pending_invite_email, pending_invite_name) values
  ('00000000-0000-0000-0000-0000000000d3', '00000000-0000-0000-0000-0000000000f3', true,
   current_setting('test.email'), current_setting('test.nombre')),
  ('00000000-0000-0000-0000-0000000000d4', '00000000-0000-0000-0000-0000000000f2', true,
   current_setting('test.email'), current_setting('test.nombre'));
select ok(pg_temp.como('purge_job',  -- 19
  $$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000000f3')$$) !~ '^err',
  'purge_tenant_data corre como purge_job');
select is((select pending_invite_name from public.tenant_users  -- 20
            where id = '00000000-0000-0000-0000-0000000000d3'), null,
  'purge_tenant_data anula pending_invite_name del tenant purgado');
select is((select pending_invite_name = current_setting('test.nombre') from public.tenant_users  -- 21
            where id = '00000000-0000-0000-0000-0000000000d4'), true,
  'la purga no toca el nombre de otro tenant');
select is((select count(*) from public.audit_log where tenant_id = '00000000-0000-0000-0000-0000000000f3'  -- 22
            and action = 'tenant_purge_step' and metadata::text like '%' || current_setting('test.nombre') || '%'),
  0::bigint, 'el audit de la purga no retiene el nombre');

select * from finish();
rollback;
