-- tenant_users.pending_invite_email (decisión de producto 2026-10-04): el email del owner invitado se guarda
-- mientras la invitación está pendiente (auth_user_id null) y se anula solo al vincular la cuenta y en la purga.
-- Grants por columna: solo platform_admin lee y escribe la columna; el resto de los roles, no.
-- Los emails se generan por corrida (ninguno literal), con dominio reservado .invalid.
begin;
select plan(30);

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

select set_config('test.email', 'pend-' || md5(random()::text) || '@prueba.invalid', false);

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

-- ---------------------------------------------------------------------------
-- La columna
-- ---------------------------------------------------------------------------
select has_column('public', 'tenant_users', 'pending_invite_email', 'tenant_users.pending_invite_email existe');  -- 1
select col_type_is('public', 'tenant_users', 'pending_invite_email', 'text', 'es text');  -- 2
select col_is_null('public', 'tenant_users', 'pending_invite_email', 'admite null');  -- 3

-- ---------------------------------------------------------------------------
-- Grants por columna
-- ---------------------------------------------------------------------------
select ok(has_column_privilege('platform_admin', 'public.tenant_users', 'pending_invite_email', 'SELECT'),  -- 4
  'platform_admin lee la columna');
select ok(has_column_privilege('platform_admin', 'public.tenant_users', 'pending_invite_email', 'INSERT, UPDATE'),  -- 5
  'platform_admin escribe la columna');
select is_empty(  -- 6
  $$select r from unnest(array['dashboard_service', 'platform_admin_readonly', 'bot_service', 'purge_job',
                               'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_column_privilege(r, 'public.tenant_users', 'pending_invite_email', 'SELECT')$$,
  'ningún otro rol puede leer la columna');
select is_empty(  -- 7
  $$select r from unnest(array['dashboard_service', 'platform_admin_readonly', 'bot_service', 'purge_job',
                               'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_column_privilege(r, 'public.tenant_users', 'pending_invite_email', 'INSERT')
        or has_column_privilege(r, 'public.tenant_users', 'pending_invite_email', 'UPDATE')$$,
  'ningún otro rol puede escribir la columna');

-- lo que el dashboard y el readonly ya leían sigue disponible (columna a columna)
select is_empty(  -- 8
  $$select r || '.' || c from unnest(array['dashboard_service', 'platform_admin_readonly']) r
     cross join unnest(array['id', 'tenant_id', 'auth_user_id', 'is_owner', 'is_active', 'created_at']) c
     where not has_column_privilege(r, 'public.tenant_users', c, 'SELECT')$$,
  'dashboard_service y platform_admin_readonly siguen leyendo el resto de las columnas');
select is_empty(  -- 9
  $$select c from unnest(array['id', 'tenant_id', 'auth_user_id', 'is_owner', 'is_active', 'created_at']) c
     where not has_column_privilege('dashboard_service', 'public.tenant_users', c, 'INSERT')
        or not has_column_privilege('dashboard_service', 'public.tenant_users', c, 'UPDATE')$$,
  'dashboard_service conserva INSERT y UPDATE sobre el resto de las columnas');
select ok(not has_column_privilege('platform_admin_readonly', 'public.tenant_users', 'is_owner', 'UPDATE'),  -- 10
  'platform_admin_readonly sigue sin escribir');

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000e1', 'Pendiente', 'email-pend-1'),
  ('00000000-0000-0000-0000-0000000000e2', 'Pendiente 2', 'email-pend-2');

-- como platform_admin: insertar, leer y actualizar la columna (RLS: acceso explícito de plataforma)
select is(pg_temp.como('platform_admin',  -- 11
  $$insert into public.tenant_users (id, tenant_id, is_owner, pending_invite_email)
    values ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', true,
            current_setting('test.email')) returning 'ok'$$), 'ok',
  'platform_admin inserta un owner con el email pendiente');
select is(pg_temp.como('platform_admin',  -- 12
  $$select (pending_invite_email = current_setting('test.email'))::text from public.tenant_users
     where id = '00000000-0000-0000-0000-0000000000c1'$$), 'true', 'platform_admin lo lee');
select is(pg_temp.como('platform_admin',  -- 13
  $$update public.tenant_users set pending_invite_email = current_setting('test.email')
     where id = '00000000-0000-0000-0000-0000000000c1' returning 'ok'$$), 'ok', 'platform_admin lo actualiza');

-- dashboard_service y readonly: rechazo por permisos, tanto select explícito como select *
select ok(pg_temp.como('dashboard_service',  -- 14
  $$select pending_invite_email from public.tenant_users limit 1$$) like 'err 42501%',
  'dashboard_service: select de la columna rechazado (permission denied)');
select ok(pg_temp.como('dashboard_service',  -- 15
  $$select count(*)::text from (select * from public.tenant_users) t$$) like 'err 42501%',
  'dashboard_service: select * rechazado (la columna no se filtra por accidente)');
select ok(pg_temp.como('platform_admin_readonly',  -- 16
  $$select pending_invite_email from public.tenant_users limit 1$$) like 'err 42501%',
  'platform_admin_readonly: select de la columna rechazado');
select ok(pg_temp.como('platform_admin_readonly',  -- 17
  $$select count(*)::text from (select * from public.tenant_users) t$$) like 'err 42501%',
  'platform_admin_readonly: select * rechazado');
select ok(pg_temp.como('dashboard_service',  -- 18
  $$update public.tenant_users set pending_invite_email = current_setting('test.email')$$) like 'err 42501%',
  'dashboard_service: no escribe la columna');
select ok(pg_temp.como('dashboard_service',  -- 19
  $$select count(*)::text from (select id, tenant_id, is_owner from public.tenant_users) t$$) !~ '^err',
  'dashboard_service sigue leyendo las demás columnas (sin error de permisos)');

-- ---------------------------------------------------------------------------
-- Se anula solo al vincular la cuenta
-- ---------------------------------------------------------------------------
insert into auth.users (id) values
  ('00000000-0000-0000-0000-0000000000a1'), ('00000000-0000-0000-0000-0000000000a2'),
  ('00000000-0000-0000-0000-0000000000a3');

select is((select pending_invite_email = current_setting('test.email') from public.tenant_users  -- 20
            where id = '00000000-0000-0000-0000-0000000000c1'), true,
  'mientras auth_user_id es null el email se conserva');
update public.tenant_users set is_active = true where id = '00000000-0000-0000-0000-0000000000c1';
select is((select pending_invite_email = current_setting('test.email') from public.tenant_users  -- 21
            where id = '00000000-0000-0000-0000-0000000000c1'), true,
  'actualizar otra columna con la invitación pendiente no anula el email');

update public.tenant_users set auth_user_id = '00000000-0000-0000-0000-0000000000a1'
 where id = '00000000-0000-0000-0000-0000000000c1';
select is((select pending_invite_email from public.tenant_users  -- 22
            where id = '00000000-0000-0000-0000-0000000000c1'), null,
  'al vincular la cuenta (auth_user_id deja de ser null) el email queda en null');

insert into public.tenant_users (id, tenant_id, auth_user_id, pending_invite_email) values
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e1',
   '00000000-0000-0000-0000-0000000000a2', current_setting('test.email'));
select is((select pending_invite_email from public.tenant_users  -- 23
            where id = '00000000-0000-0000-0000-0000000000c2'), null,
  'un alta ya vinculada no guarda el email');

update public.tenant_users set pending_invite_email = current_setting('test.email')
 where id = '00000000-0000-0000-0000-0000000000c1';
select is((select pending_invite_email from public.tenant_users  -- 24
            where id = '00000000-0000-0000-0000-0000000000c1'), null,
  'con la cuenta vinculada tampoco se puede guardar un email pendiente');

insert into public.tenant_users (id, tenant_id, pending_invite_email) values
  ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000e1', current_setting('test.email'));
update public.tenant_users
   set auth_user_id = '00000000-0000-0000-0000-0000000000a3', pending_invite_email = current_setting('test.email')
 where id = '00000000-0000-0000-0000-0000000000c3';
select is((select pending_invite_email from public.tenant_users  -- 25
            where id = '00000000-0000-0000-0000-0000000000c3'), null,
  'vincular y escribir el email en el mismo update deja null');

-- ---------------------------------------------------------------------------
-- La purga lo anula (tenant inactivo con el período vencido, como purge_job)
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000e3', 'Vencido', 'email-pend-vencido', 'inactive', now() - interval '100 days');
insert into public.tenant_users (id, tenant_id, is_owner, pending_invite_email) values
  ('00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000e3', true, current_setting('test.email'));
-- otro tenant con invitación pendiente: la purga no lo toca
insert into public.tenant_users (id, tenant_id, is_owner, pending_invite_email) values
  ('00000000-0000-0000-0000-0000000000c5', '00000000-0000-0000-0000-0000000000e2', true, current_setting('test.email'));

select is((select pending_invite_email = current_setting('test.email') from public.tenant_users  -- 26
            where id = '00000000-0000-0000-0000-0000000000c4'), true, 'antes de purgar el email está guardado');
select ok(pg_temp.como('purge_job',  -- 27
  $$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000000e3')$$) !~ '^err',
  'purge_tenant_data corre como purge_job');
select is((select pending_invite_email from public.tenant_users  -- 28
            where id = '00000000-0000-0000-0000-0000000000c4'), null,
  'purge_tenant_data anula pending_invite_email del tenant purgado');
select is((select pending_invite_email = current_setting('test.email') from public.tenant_users  -- 29
            where id = '00000000-0000-0000-0000-0000000000c5'), true,
  'la purga no toca el email de otro tenant');
select is((select count(*) from public.audit_log where tenant_id = '00000000-0000-0000-0000-0000000000e3'  -- 30
            and action = 'tenant_purge_step' and metadata::text like '%' || current_setting('test.email') || '%'),
  0::bigint, 'el audit de la purga no retiene el email');

select * from finish();
rollback;
