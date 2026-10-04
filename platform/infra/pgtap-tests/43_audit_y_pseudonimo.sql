-- write_audit_log (§2.10/§3.2: el actor se deriva del caller real), pseudonymize_channel_user y
-- find_dormant_end_customer (§2.3, §3.5).
--
-- `set role` no cambia session_user: acá se prueba el mapeo rol -> actor_type con la función
-- pura audit_actor_type y el rechazo de roles no listados (el caller de pgTAP es el rol de migraciones).
-- El camino completo con conexiones reales por rol está en tests/integracion/test_funciones_plataforma.py.
begin;
select plan(37);

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

create function pg_temp.como(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v text;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
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

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'aud-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'aud-b');

-- ---------------------------------------------------------------------------
-- write_audit_log: actor_type según session_user
-- ---------------------------------------------------------------------------
select is(public.audit_actor_type('bot_service'), 'bot', 'bot_service -> bot');
select is(public.audit_actor_type('dashboard_service'), 'staff', 'dashboard_service -> staff');
select is(public.audit_actor_type('platform_admin'), 'platform_admin', 'platform_admin -> platform_admin');
select is(public.audit_actor_type('purge_job'), 'system', 'purge_job -> system');
select is(public.audit_actor_type('scheduler_job'), null, 'scheduler_job no está listado');
select is(public.audit_actor_type('platform_admin_readonly'), null, 'platform_admin_readonly no está listado');
select is(public.audit_actor_type('postgres'), null, 'el rol de migraciones no está listado');
select is(public.audit_actor_type('anon'), null, 'anon no está listado');
select is(public.audit_actor_type(null), null, 'null no está listado');

select throws_ok(
  $$select public.write_audit_log('accion_x', 'entidad', null, null, '00000000-0000-0000-0000-0000000000a1')$$,
  '42501', 'rol no autorizado para escribir en audit_log: postgres',
  'write_audit_log rechaza un caller no listado (session_user = postgres)');
select is((select count(*) from public.audit_log where action = 'accion_x'), 0::bigint,
  'el rechazo no deja ninguna fila');

-- EXECUTE solo para los 4 roles de §3.2
select is_empty(
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'purge_job']) r
     where not has_function_privilege(r, 'public.write_audit_log(text, text, uuid, jsonb, uuid)', 'execute')$$,
  'bot_service, dashboard_service, platform_admin y purge_job ejecutan write_audit_log');
select is_empty(
  $$select r from unnest(array['platform_admin_readonly', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.write_audit_log(text, text, uuid, jsonb, uuid)', 'execute')$$,
  'ningún otro rol ejecuta write_audit_log');
select ok(pg_temp.como('scheduler_job', null, $$select public.write_audit_log('accion_y')::text$$)
  like 'err 42501%permission denied%', 'scheduler_job: permission denied');

-- la función interna que inserta no la ejecuta ningún rol de aplicación
select is_empty(
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r,
       'public.audit_log_append(uuid, text, uuid, text, text, uuid, jsonb)', 'execute')$$,
  'audit_log_append (interna) no tiene EXECUTE para ningún rol de aplicación');
select ok(public.audit_log_append('00000000-0000-0000-0000-0000000000a1', 'staff', null, 'prueba_interna', 'x', null, '{"k": 1}') is not null,
  'audit_log_append inserta y devuelve el id');
select is((select actor_type || '/' || action from public.audit_log where action = 'prueba_interna'),
  'staff/prueba_interna', 'la fila interna queda con el actor indicado');

-- ---------------------------------------------------------------------------
-- pseudonymize_channel_user: clave en Vault
-- ---------------------------------------------------------------------------
delete from vault.secrets where name = 'channel_user_hmac_key';

select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'), null,
  'sin la clave en Vault devuelve NULL (la anonimización falla cerrada)');

do $$
begin
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

select ok(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1') ~ '^[0-9a-f]{64}$',
  'con la clave devuelve un HMAC-SHA256 en hexadecimal (64 caracteres)');
select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  'es determinístico para el mismo input');
select isnt(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a2', 'telegram', 'u1'),
  'cambia al variar tenant_id (no vincula a la persona entre tenants)');
select isnt(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'whatsapp', 'u1'),
  'cambia al variar el canal');
select isnt(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u2'),
  'cambia al variar channel_user_id');
select is(public.pseudonymize_channel_user(null, 'telegram', 'u1'), null, 'tenant nulo: NULL');
select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', null, 'u1'), null, 'canal nulo: NULL');
select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', null), null,
  'channel_user_id nulo: NULL');

select is_empty(
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.pseudonymize_channel_user(uuid, text, text)', 'execute')$$,
  'pseudonymize_channel_user no tiene EXECUTE para ningún rol de aplicación');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1')$$)
  like 'err 42501%permission denied%', 'bot_service no puede calcular el hash (la clave nunca llega a la aplicación)');

-- ---------------------------------------------------------------------------
-- find_dormant_end_customer
-- ---------------------------------------------------------------------------
create temp table hashes as
  select public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'dorm1') as ha,
         public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a2', 'telegram', 'dorm1') as hb;

insert into public.end_customers (id, tenant_id, channel, channel_user_id, channel_user_hash, deleted_at) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'telegram', null, (select ha from hashes), now() - interval '10 days'),
  ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000a1', 'telegram', null, (select ha from hashes), now() - interval '2 days'),
  ('00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000a2', 'telegram', null, (select hb from hashes), now() - interval '1 day'),
  -- fila de B con el MISMO hash que la de A: aun así, A nunca la ve
  ('00000000-0000-0000-0000-0000000000e4', '00000000-0000-0000-0000-0000000000a2', 'telegram', null, (select ha from hashes), now() - interval '1 hour');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000e5', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'vivo1');

select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select id::text from public.find_dormant_end_customer('telegram', 'dorm1')$$),
  '00000000-0000-0000-0000-0000000000e2', 'devuelve la fila dormida más reciente del tenant del contexto');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.find_dormant_end_customer('telegram', 'dorm1')$$),
  '1', 'una sola fila');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a2',
  $$select id::text from public.find_dormant_end_customer('telegram', 'dorm1')$$),
  '00000000-0000-0000-0000-0000000000e3', 'con el contexto de B devuelve solo la fila de B');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.find_dormant_end_customer('whatsapp', 'dorm1')$$),
  '0', 'otro canal: nada');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.find_dormant_end_customer('telegram', 'vivo1')$$),
  '0', 'un cliente con fila activa no figura como dormido');
select ok(pg_temp.como('bot_service', null,
  $$select count(*)::text from public.find_dormant_end_customer('telegram', 'dorm1')$$)
  like 'err 42501%contexto de tenant%', 'sin contexto de tenant: rechaza');
select ok(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.find_dormant_end_customer('telegram', 'dorm1')$$)
  like 'err 42501%permission denied%', 'solo bot_service: dashboard_service no la ejecuta');
select is_empty(
  $$select r from unnest(array['dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.find_dormant_end_customer(text, text)', 'execute')$$,
  'ningún otro rol ejecuta find_dormant_end_customer');

delete from vault.secrets where name = 'channel_user_hmac_key';
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.find_dormant_end_customer('telegram', 'dorm1')$$),
  '0', 'sin la clave en Vault no hay recontacto (devuelve nada)');

select * from finish();
rollback;
