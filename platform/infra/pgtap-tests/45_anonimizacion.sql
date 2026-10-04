-- anonymize_end_customer (§2.3, §3.5), versión de plataforma (sin ramas de dominio).
--
-- `set role` no cambia session_user: acá el actor viene de la función interna
-- anonymize_end_customer_core(id, actor_type, actor_id) — la que ejecuta la lógica real y a la que la
-- función pública le pasa el actor derivado de session_user (el rol de migraciones de pgTAP la puede
-- llamar; ningún rol de aplicación tiene EXECUTE). El camino completo con conexiones reales por rol
-- (bot_service/dashboard_service/platform_admin/purge_job) está en
-- tests/integracion/test_anonimizacion_purga.py.
begin;
select plan(39);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job', 'scheduler_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

-- Contexto de tenant de la transacción (null = sin contexto)
create function pg_temp.ctx(p_tenant text) returns void
language plpgsql as $$
begin
  perform set_config('app.current_tenant_id', coalesce(p_tenant, ''), true);
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
  ('00000000-0000-0000-0000-0000000000b1', 'A', 'anon-a'),
  ('00000000-0000-0000-0000-0000000000b2', 'B', 'anon-b');

insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name, extra_data) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000b1', 'telegram', 'tg-c1', '+5491111111111', 'Ana Pérez', '{"alergia": "x"}'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000b1', 'whatsapp', 'wa-c2', '+5491122222222', 'Beto', '{}'),
  ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000b2', 'telegram', 'tg-c3', '+5491133333333', 'Carla', '{"k": 1}'),
  ('00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000b2', 'telegram', 'tg-c4', '+5491144444444', 'Dani', '{}'),
  ('00000000-0000-0000-0000-0000000000c5', '00000000-0000-0000-0000-0000000000b1', null, null, '+5491155555555', 'Manual', '{}');

insert into public.bot_sessions (tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000b1', 'telegram', 'tg-c1'),
  ('00000000-0000-0000-0000-0000000000b1', 'telegram', 'tg-otro'),
  ('00000000-0000-0000-0000-0000000000b2', 'telegram', 'tg-c3');

insert into public.notifications_log (tenant_id, end_customer_id, channel, status, last_error) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000c1', 'telegram', 'failed', 'error 403 del proveedor'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000c3', 'telegram', 'failed', 'error 400 del proveedor');

create temp table hash_esperado as
  select public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000b1', 'telegram', 'tg-c1') as h;

-- ---------------------------------------------------------------------------
-- Negativos (§3.5): tenant ajeno, sin contexto, actor desconocido, rol no listado
-- ---------------------------------------------------------------------------
select pg_temp.ctx('00000000-0000-0000-0000-0000000000b1');
select throws_ok(  -- 1
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c3', 'bot', null)$$,
  '42501', 'tenant mismatch: end_customer inexistente o de otro tenant',
  'bot con tenant A sobre un end_customer de B: falla');
select throws_ok(  -- 2
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c3', 'staff', null)$$,
  '42501', 'tenant mismatch: end_customer inexistente o de otro tenant',
  'staff con tenant A sobre un end_customer de B: falla');
select is((select phone || '/' || name from public.end_customers where id = '00000000-0000-0000-0000-0000000000c3'),  -- 3
  '+5491133333333/Carla', 'el rechazo no toca al end_customer de B');
select is((select count(*) from public.audit_log where action = 'anonymize_end_customer'), 0::bigint,  -- 4
  'el rechazo no deja filas de auditoría');

select pg_temp.ctx(null);
select throws_ok(  -- 5
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'bot', null)$$,
  '42501', 'tenant mismatch: contexto de tenant requerido',
  'bot sin tenant en contexto: falla (NULL no coincide con ningún tenant)');
select throws_ok(  -- 6
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'staff', null)$$,
  '42501', 'tenant mismatch: contexto de tenant requerido',
  'staff sin tenant en contexto: falla');
select is((select phone from public.end_customers where id = '00000000-0000-0000-0000-0000000000c1'),  -- 7
  '+5491111111111', 'sin contexto no se anonimiza nada');

select throws_ok(  -- 8
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'scheduler_job', null)$$,
  '42501', 'actor_type no autorizado para anonimizar',
  'un actor_type fuera de la lista: falla');
select throws_ok(  -- 9
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', null, null)$$,
  '42501', 'actor_type no autorizado para anonimizar',
  'un actor_type nulo: falla');

select throws_ok(  -- 10
  $$select public.anonymize_end_customer('00000000-0000-0000-0000-0000000000c1')$$,
  '42501', 'rol no autorizado para anonimizar: postgres',
  'rol no listado (session_user = postgres): falla');
select ok(pg_temp.como('platform_admin_readonly', null,  -- 11
  $$select public.anonymize_end_customer('00000000-0000-0000-0000-0000000000c1')::text$$)
  like 'err 42501%permission denied%', 'platform_admin_readonly: permission denied');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000b1',  -- 12
  $$select public.anonymize_end_customer('00000000-0000-0000-0000-0000000000c1')::text$$)
  like 'err 42501 rol no autorizado para anonimizar: postgres',
  'bot_service pasa el GRANT y la función revisa el session_user real');

-- ---------------------------------------------------------------------------
-- Camino feliz: staff sobre su propio tenant
-- ---------------------------------------------------------------------------
select pg_temp.ctx('00000000-0000-0000-0000-0000000000b1');
select lives_ok(  -- 13
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'staff', '00000000-0000-0000-0000-00000000aaaa')$$,
  'staff anonimiza un end_customer de su tenant');
select is((select phone is null and name is null and extra_data = '{}'::jsonb and channel_user_id is null  -- 14
                  and deleted_at is not null
             from public.end_customers where id = '00000000-0000-0000-0000-0000000000c1'),
  true, 'phone/name/extra_data/channel_user_id anonimizados y deleted_at marcado');
select is((select channel_user_hash from public.end_customers where id = '00000000-0000-0000-0000-0000000000c1'),  -- 15
  (select h from hash_esperado), 'channel_user_hash = HMAC del identificador original');
select is((select count(*) from public.notifications_log  -- 16
            where end_customer_id = '00000000-0000-0000-0000-0000000000c1' and last_error is not null), 0::bigint,
  'notifications_log.last_error queda en null');
select is((select count(*) from public.bot_sessions  -- 17
            where tenant_id = '00000000-0000-0000-0000-0000000000b1' and channel_user_id = 'tg-c1'), 0::bigint,
  'se borra la bot_session del cliente');
select is((select count(*) from public.bot_sessions  -- 18
            where tenant_id = '00000000-0000-0000-0000-0000000000b1' and channel_user_id = 'tg-otro'), 1::bigint,
  'la sesión de otro usuario sigue');
select is((select count(*) from public.notifications_log  -- 19
            where end_customer_id = '00000000-0000-0000-0000-0000000000c3' and last_error is not null), 1::bigint,
  'las notificaciones de otro tenant no se tocan');
select is((select actor_type || '/' || entity || '/' || entity_id::text || '/' || actor_id::text  -- 20
             from public.audit_log where action = 'anonymize_end_customer'),
  'staff/end_customers/00000000-0000-0000-0000-0000000000c1/00000000-0000-0000-0000-00000000aaaa',
  'audit_log: actor_type staff (dashboard_service), entidad y actor_id del contexto');
select is((select tenant_id from public.audit_log where action = 'anonymize_end_customer'),  -- 21
  '00000000-0000-0000-0000-0000000000b1'::uuid, 'audit_log: tenant del end_customer');
select is((select metadata::text ~ '(Ana|5491111|tg-c1|alergia|error 403)' from public.audit_log  -- 22
            where action = 'anonymize_end_customer'),
  false, 'audit_log no retiene ningún valor original');
select ok((select metadata -> 'fields_anonymized' from public.audit_log where action = 'anonymize_end_customer')  -- 23
  @> '["phone", "name", "extra_data", "channel_user_id", "notifications_log.last_error"]'::jsonb,
  'audit_log deja los nombres de los campos anonimizados');

-- idempotente: la segunda llamada conserva el hash (channel_user_id ya es null)
select lives_ok(  -- 24
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'staff', null)$$,
  'segunda llamada: no falla');
select is((select channel_user_hash from public.end_customers where id = '00000000-0000-0000-0000-0000000000c1'),  -- 25
  (select h from hash_esperado), 'segunda llamada: conserva el hash');
select is((select channel_user_id is null and channel = 'telegram'  -- 26
             from public.end_customers where id = '00000000-0000-0000-0000-0000000000c1'),
  true, 'segunda llamada: sigue sin channel_user_id y conserva el canal');

-- alta manual sin canal: se anonimiza sin hash
select lives_ok(  -- 27
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c5', 'bot', null)$$,
  'un end_customer sin canal (alta manual) también se anonimiza');
select is((select phone is null and name is null and channel is null and channel_user_hash is null  -- 28
             from public.end_customers where id = '00000000-0000-0000-0000-0000000000c5'),
  true, 'sin canal no hay hash');
select is((select actor_type from public.audit_log  -- 29
            where action = 'anonymize_end_customer' and entity_id = '00000000-0000-0000-0000-0000000000c5'),
  'bot', 'audit_log: actor_type bot (bot_service)');

-- platform_admin y el job de purga (system) cruzan tenants, aun sin contexto
select pg_temp.ctx(null);
select lives_ok(  -- 30
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c3', 'platform_admin', null)$$,
  'platform_admin anonimiza un end_customer de cualquier tenant');
select lives_ok(  -- 31
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c4', 'system', null)$$,
  'system (purge_job) anonimiza sin contexto de tenant');
select is((select string_agg(actor_type, ',' order by actor_type) from public.audit_log  -- 32
            where action = 'anonymize_end_customer'
              and entity_id in ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000c4')),
  'platform_admin,system', 'audit_log: actor_type platform_admin y system');

select throws_ok(  -- 33
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-00000000dead', 'platform_admin', null)$$,
  'P0002', 'end_customer inexistente', 'un id inexistente falla');

-- sin la clave de Vault falla cerrada: el check de end_customers impide dejar la fila sin identificador
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone) values
  ('00000000-0000-0000-0000-0000000000c6', '00000000-0000-0000-0000-0000000000b2', 'telegram', 'tg-c6', '+5491166666666');
delete from vault.secrets where name = 'channel_user_hmac_key';
select ok(pg_temp.como('postgres', null,  -- 34
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c6', 'platform_admin', null)::text$$)
  like 'err 23514%', 'sin la clave HMAC en Vault: falla cerrada (check de end_customers)');
select is((select phone from public.end_customers where id = '00000000-0000-0000-0000-0000000000c6'),  -- 35
  '+5491166666666', 'y no deja la fila a medio anonimizar');

-- ---------------------------------------------------------------------------
-- EXECUTE: anonymize_end_customer solo para bot_service, dashboard_service y platform_admin;
-- la función interna no la ejecuta ningún rol de aplicación
-- ---------------------------------------------------------------------------
select is_empty(  -- 36
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin']) r
     where not has_function_privilege(r, 'public.anonymize_end_customer(uuid)', 'execute')$$,
  'bot_service, dashboard_service y platform_admin ejecutan anonymize_end_customer');
select is_empty(  -- 37
  $$select r from unnest(array['platform_admin_readonly', 'purge_job', 'scheduler_job', 'anon', 'authenticated',
                               'service_role']) r
     where has_function_privilege(r, 'public.anonymize_end_customer(uuid)', 'execute')$$,
  'ningún otro rol ejecuta anonymize_end_customer (purge_job la alcanza anidada, sin grant)');
select is_empty(  -- 38
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.anonymize_end_customer_core(uuid, text, uuid)', 'execute')$$,
  'anonymize_end_customer_core (interna) no tiene EXECUTE para ningún rol de aplicación');
select ok((select prosecdef and proconfig @> array['search_path=""']  -- 39
             from pg_proc where oid = 'public.anonymize_end_customer(uuid)'::regprocedure),
  'anonymize_end_customer es SECURITY DEFINER con search_path vacío');

select * from finish();
rollback;
