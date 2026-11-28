-- Anonimización y purga de la web de reservas: el actor 'web', lo que borra la anonimización de una ficha (email,
-- política aceptada, canales, sesiones y códigos) y lo que borra la purga de un negocio (sesiones, códigos, canales,
-- configuración de avisos y slugs anteriores), con la ruta del logo en su resultado.
--
-- Lo que depende de session_user (que web_reservas llegue a anonymize_end_customer y quede como actor 'web') no se
-- puede probar con set role en pgTAP: está en tests/integracion/test_anonimizacion_purga.py, con una conexión real.
-- Acá la función interna recibe el actor ya decidido, igual que en los demás tests de anonimización.
begin;
select plan(61);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['web_reservas', 'purge_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

-- Ejecuta p_sql (un select que devuelve un valor) como p_rol y devuelve el valor como texto ('<null>' si es nulo) o
-- 'err <sqlstate>'.
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

create function pg_temp.contexto(p_tenant text) returns void
language sql as $$ select set_config('app.current_tenant_id', coalesce(p_tenant, ''), true) $$;

-- ---------------------------------------------------------------------------
-- Datos
-- A y B activos; C, E y F inactivos con el período vencido (C con cuentas de Auth y logo, E con logo y sin cuentas,
-- F sin cuentas ni logo)
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug, status, deactivated_at, settings) values
  ('00000000-0000-0000-0000-0000000080a1', 'A', 'anonweb-a', 'active', null, '{}'),
  ('00000000-0000-0000-0000-0000000080b1', 'B', 'anonweb-b', 'active', null,
   '{"logo_ruta": "00000000-0000-0000-0000-0000000080b1/logo-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png"}'),
  ('00000000-0000-0000-0000-0000000080c1', 'C', 'anonweb-c', 'inactive', now() - interval '100 days',
   '{"logo_ruta": "00000000-0000-0000-0000-0000000080c1/logo-cccccccccccccccccccccccccccccccc.webp"}'),
  ('00000000-0000-0000-0000-0000000080e1', 'E', 'anonweb-e', 'inactive', now() - interval '100 days',
   '{"logo_ruta": "00000000-0000-0000-0000-0000000080e1/logo-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.jpg"}'),
  ('00000000-0000-0000-0000-0000000080f1', 'F', 'anonweb-f', 'inactive', now() - interval '100 days', '{}');

-- Fichas: c1 (A) con email, política, dos canales, sesión y código; c2 (A) igual pero con un canal; c3 (A) sin canales,
-- sesiones ni códigos; b1 (B) con el mismo email que c1 (otro negocio)
insert into public.end_customers (id, tenant_id, name, phone, email, politica_version, politica_aceptada_at) values
  ('00000000-0000-0000-0000-000000008101', '00000000-0000-0000-0000-0000000080a1', 'Ana', '+5491100000001',
   'ana@a.test', 'v1', now()),
  ('00000000-0000-0000-0000-000000008102', '00000000-0000-0000-0000-0000000080a1', 'Bea', '+5491100000002',
   'bea@a.test', 'v1', now()),
  ('00000000-0000-0000-0000-000000008103', '00000000-0000-0000-0000-0000000080a1', 'Cata', '+5491100000003',
   'cata@a.test', 'v1', now()),
  ('00000000-0000-0000-0000-000000008201', '00000000-0000-0000-0000-0000000080b1', 'Ana en B', '+5491100000004',
   'ana@a.test', 'v1', now());
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008101', 'telegram', 'tg-ana', now()),
  ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008101', 'email', 'ana@a.test', now()),
  ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008102', 'email', 'bea@a.test', now()),
  ('00000000-0000-0000-0000-0000000080b1', '00000000-0000-0000-0000-000000008201', 'email', 'ana@a.test', now());
insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008101', repeat('1', 64), now() + interval '1 day'),
  ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008102', repeat('2', 64), now() + interval '1 day'),
  ('00000000-0000-0000-0000-0000000080b1', '00000000-0000-0000-0000-000000008201', repeat('3', 64), now() + interval '1 day');
insert into public.codigos_web (tenant_id, end_customer_id, email_hash, codigo_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008101', repeat('4', 64), repeat('5', 64), now() + interval '5 minutes'),
  ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008102', repeat('6', 64), repeat('7', 64), now() + interval '5 minutes'),
  ('00000000-0000-0000-0000-0000000080a1', null, repeat('8', 64), repeat('9', 64), now() + interval '5 minutes'),
  ('00000000-0000-0000-0000-0000000080b1', '00000000-0000-0000-0000-000000008201', repeat('a', 64), repeat('b', 64), now() + interval '5 minutes');
-- B: la configuración de avisos y un slug anterior, que la purga de C no debe tocar
insert into public.tenant_users (id, tenant_id, is_owner, is_active) values
  ('00000000-0000-0000-0000-000000008b01', '00000000-0000-0000-0000-0000000080b1', true, true);
insert into public.avisos_negocio_config (tenant_id, tenant_user_id) values
  ('00000000-0000-0000-0000-0000000080b1', '00000000-0000-0000-0000-000000008b01');
insert into public.slugs_anteriores (slug, tenant_id, reemplazado_at, vigente_hasta) values
  ('anonweb-b-viejo', '00000000-0000-0000-0000-0000000080b1', now(), now() + interval '30 days');

-- ---------------------------------------------------------------------------
-- audit_actor_type, check de audit_log y privilegios
-- ---------------------------------------------------------------------------
select is(public.audit_actor_type('web_reservas'), 'web', 'web_reservas -> web');  -- 1
select is(public.audit_actor_type('enviador'), null, 'enviador no está listado');  -- 2
select is(public.audit_actor_type('bot_service'), 'bot', 'bot_service sigue siendo bot');  -- 3
select lives_ok(
  $$insert into public.audit_log (tenant_id, actor_type, action)
    values ('00000000-0000-0000-0000-0000000080a1', 'web', 'accion_de_prueba')$$,
  'audit_log acepta el actor web');  -- 4
select throws_ok(
  $$insert into public.audit_log (tenant_id, actor_type, action)
    values ('00000000-0000-0000-0000-0000000080a1', 'robot', 'accion_de_prueba')$$,
  '23514', null, 'audit_log sigue rechazando un actor desconocido');  -- 5
select ok(has_function_privilege('web_reservas', 'public.anonymize_end_customer(uuid)', 'EXECUTE'),
  'web_reservas ejecuta anonymize_end_customer');  -- 6
select ok(not has_function_privilege('web_reservas', 'public.anonymize_end_customer_core(uuid, text, uuid)', 'EXECUTE'),
  'web_reservas no ejecuta la función interna que recibe el actor como parámetro');  -- 7
select ok(not has_function_privilege('web_reservas', 'public.write_audit_log(text, text, uuid, jsonb, uuid)', 'EXECUTE'),
  'web_reservas no escribe audit_log por su cuenta');  -- 8
select ok(has_column_privilege('web_reservas', 'public.codigos_web', 'end_customer_id', 'INSERT')
          and not has_column_privilege('web_reservas', 'public.codigos_web', 'end_customer_id', 'UPDATE')
          and not has_column_privilege('bot_service', 'public.codigos_web', 'end_customer_id', 'SELECT'),
  'web_reservas inserta codigos_web.end_customer_id y no lo cambia después; el bot no lo ve');  -- 9
select throws_ok(
  $$insert into public.codigos_web (tenant_id, end_customer_id, email_hash, codigo_hash, expira_at)
    values ('00000000-0000-0000-0000-0000000080a1', '00000000-0000-0000-0000-000000008201',
            repeat('c', 64), repeat('d', 64), now() + interval '5 minutes')$$,
  '23503', null, 'un código no se liga a una ficha de otro negocio');  -- 10

-- ---------------------------------------------------------------------------
-- Rechazos de la función interna
-- ---------------------------------------------------------------------------
select pg_temp.contexto('00000000-0000-0000-0000-0000000080a1');
select throws_ok($$select public.anonymize_end_customer_core('00000000-0000-0000-0000-000000008101', 'robot', null)$$,
  '42501', 'actor_type no autorizado para anonimizar', 'un actor desconocido se rechaza');  -- 11
select throws_ok($$select public.anonymize_end_customer_core('00000000-0000-0000-0000-000000008101', null, null)$$,
  '42501', 'actor_type no autorizado para anonimizar', 'un actor nulo se rechaza');  -- 12
select throws_ok($$select public.anonymize_end_customer_core('00000000-0000-0000-0000-000000008201', 'web', null)$$,
  '42501', 'tenant mismatch: end_customer inexistente o de otro tenant',
  'el actor web con el contexto de A no anonimiza una ficha de B');  -- 13
select throws_ok($$select public.anonymize_end_customer_core(gen_random_uuid(), 'web', null)$$,
  '42501', 'tenant mismatch: end_customer inexistente o de otro tenant', 'una ficha inexistente se rechaza');  -- 14
select pg_temp.contexto(null);
select throws_ok($$select public.anonymize_end_customer_core('00000000-0000-0000-0000-000000008101', 'web', null)$$,
  '42501', 'tenant mismatch: contexto de tenant requerido', 'el actor web sin contexto de tenant se rechaza');  -- 15
select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008201'), 'ana@a.test',
  'tras los rechazos, la ficha de B conserva su email');  -- 16
select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008101'), 'ana@a.test',
  'tras los rechazos, la ficha de A conserva su email');  -- 17

-- ---------------------------------------------------------------------------
-- Anonimizar c1 como actor web (con el trigger de sesiones apagado, para probar que lo borra la función)
-- ---------------------------------------------------------------------------
alter table public.end_customers disable trigger borrar_sesiones_web_al_anonimizar;
select pg_temp.contexto('00000000-0000-0000-0000-0000000080a1');
select public.anonymize_end_customer_core('00000000-0000-0000-0000-000000008101', 'web',
                                          '00000000-0000-0000-0000-000000008aaa');
alter table public.end_customers enable trigger borrar_sesiones_web_al_anonimizar;
create temp table despues as
  select channel_user_hash from public.end_customers where id = '00000000-0000-0000-0000-000000008101';

select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008101'), null,
  'la anonimización borra el email');  -- 18
select is((select politica_version from public.end_customers where id = '00000000-0000-0000-0000-000000008101'), null,
  'la anonimización anula politica_version');  -- 19
select is((select politica_aceptada_at from public.end_customers where id = '00000000-0000-0000-0000-000000008101'), null,
  'la anonimización anula politica_aceptada_at');  -- 20
select ok((select phone is null and name is null and extra_data = '{}'::jsonb and deleted_at is not null
             and channel_user_id is null and channel_user_hash is not null
             from public.end_customers where id = '00000000-0000-0000-0000-000000008101'),
  'sigue anonimizando teléfono, nombre y datos extra, y conserva el pseudónimo del canal');  -- 21
select is((select count(*) from public.canales_cliente where end_customer_id = '00000000-0000-0000-0000-000000008101'),
  0::bigint, 'borra los dos canales de la ficha');  -- 22
select is((select count(*) from public.sesiones_web where end_customer_id = '00000000-0000-0000-0000-000000008101'),
  0::bigint, 'borra la sesión de la ficha (sin ayuda del trigger)');  -- 23
select is((select count(*) from public.codigos_web where end_customer_id = '00000000-0000-0000-0000-000000008101'),
  0::bigint, 'borra el código de la ficha');  -- 24
select is((select count(*) from public.canales_cliente where identificador in ('tg-ana', 'ana@a.test')
             and tenant_id = '00000000-0000-0000-0000-0000000080a1'),
  0::bigint, 'el identificador y el email en claro ya no están en el negocio');  -- 25
select is((select actor_type from public.audit_log where action = 'anonymize_end_customer'
             and entity_id = '00000000-0000-0000-0000-000000008101'), 'web',
  'audit_log registra la anonimización con el actor web');  -- 26
select is((select actor_id from public.audit_log where action = 'anonymize_end_customer'
             and entity_id = '00000000-0000-0000-0000-000000008101'), '00000000-0000-0000-0000-000000008aaa'::uuid,
  'audit_log registra el actor_id');  -- 27
select ok((select metadata -> 'fields_anonymized' from public.audit_log where action = 'anonymize_end_customer'
             and entity_id = '00000000-0000-0000-0000-000000008101')
          @> '["email", "politica_version", "politica_aceptada_at", "canales_cliente", "sesiones_web", "codigos_web"]'::jsonb,
  'la constancia nombra los campos nuevos');  -- 28
select ok((select metadata::text !~ '(ana@a.test|tg-ana|Ana)' from public.audit_log
            where action = 'anonymize_end_customer' and entity_id = '00000000-0000-0000-0000-000000008101'),
  'la constancia no retiene ningún valor original');  -- 29

-- lo que no debía tocar
select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008102'), 'bea@a.test',
  'otra ficha del mismo negocio conserva su email');  -- 30
select is((select count(*) from public.canales_cliente where end_customer_id = '00000000-0000-0000-0000-000000008102')
          + (select count(*) from public.sesiones_web where end_customer_id = '00000000-0000-0000-0000-000000008102')
          + (select count(*) from public.codigos_web where end_customer_id = '00000000-0000-0000-0000-000000008102'),
  3::bigint, 'otra ficha del mismo negocio conserva su canal, su sesión y su código');  -- 31
select is((select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000080a1'
             and end_customer_id is null), 1::bigint,
  'un código sin ficha ligada no se toca');  -- 32
select is((select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000080b1')
          + (select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000080b1')
          + (select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000080b1'),
  3::bigint, 'el negocio B conserva sus canales, sesiones y códigos');  -- 33
select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008201'), 'ana@a.test',
  'la ficha de B con el mismo email no se toca');  -- 34

-- idempotencia y fichas sin nada que borrar
select lives_ok($$select public.anonymize_end_customer_core('00000000-0000-0000-0000-000000008101', 'web', null)$$,
  'anonimizar una ficha ya anonimizada no falla');  -- 35
select is((select channel_user_hash from public.end_customers where id = '00000000-0000-0000-0000-000000008101'),
  (select channel_user_hash from despues), 'repetirla conserva el pseudónimo');  -- 36
select lives_ok($$select public.anonymize_end_customer_core('00000000-0000-0000-0000-000000008103', 'web', null)$$,
  'anonimizar una ficha sin canales, sesiones ni códigos no falla');  -- 37
select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008103'), null,
  'la ficha sin canales también pierde el email');  -- 38
select is((select count(*) from public.audit_log where action = 'anonymize_end_customer'
             and entity_id = '00000000-0000-0000-0000-000000008103'), 1::bigint,
  'y deja su constancia');  -- 39

-- el email liberado vuelve a poder usarse en el mismo negocio
select lives_ok(
  $$insert into public.end_customers (tenant_id, name, phone, email)
    values ('00000000-0000-0000-0000-0000000080a1', 'Ana nueva', '+5491100000009', 'ana@a.test')$$,
  'el email de una ficha anonimizada queda libre');  -- 40

-- ---------------------------------------------------------------------------
-- Purga: C (cuentas de Auth y logo), con datos de la web, avisos y slugs anteriores
-- ---------------------------------------------------------------------------
insert into auth.users (id) values
  ('00000000-0000-0000-0000-00000000c0a1'), ('00000000-0000-0000-0000-00000000c0a2');
insert into public.tenant_users (id, tenant_id, auth_user_id, is_owner, is_active) values
  ('00000000-0000-0000-0000-000000008c01', '00000000-0000-0000-0000-0000000080c1',
   '00000000-0000-0000-0000-00000000c0a1', true, true),
  ('00000000-0000-0000-0000-000000008c02', '00000000-0000-0000-0000-0000000080c1',
   '00000000-0000-0000-0000-00000000c0a2', false, true);
insert into public.avisos_negocio_config (tenant_id, tenant_user_id) values
  ('00000000-0000-0000-0000-0000000080c1', '00000000-0000-0000-0000-000000008c01'),
  ('00000000-0000-0000-0000-0000000080c1', '00000000-0000-0000-0000-000000008c02');
insert into public.slugs_anteriores (slug, tenant_id, reemplazado_at, vigente_hasta) values
  ('anonweb-c-viejo', '00000000-0000-0000-0000-0000000080c1', now(), now() + interval '30 days');
-- una ficha viva (cv) y una ya anonimizada antes (ca) que dejó una sesión colgada
insert into public.end_customers (id, tenant_id, name, email, politica_version, politica_aceptada_at, deleted_at) values
  ('00000000-0000-0000-0000-000000008301', '00000000-0000-0000-0000-0000000080c1', 'Viva', 'viva@c.test', 'v1', now(), null),
  ('00000000-0000-0000-0000-000000008302', '00000000-0000-0000-0000-0000000080c1', null, null, null, null, now());
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000080c1', '00000000-0000-0000-0000-000000008301', 'email', 'viva@c.test', now()),
  ('00000000-0000-0000-0000-0000000080c1', '00000000-0000-0000-0000-000000008302', 'email', 'resto@c.test', now());
insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000080c1', '00000000-0000-0000-0000-000000008301', repeat('e', 64), now() + interval '1 day'),
  ('00000000-0000-0000-0000-0000000080c1', '00000000-0000-0000-0000-000000008302', repeat('f', 64), now() + interval '1 day');
insert into public.codigos_web (tenant_id, end_customer_id, email_hash, codigo_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000080c1', '00000000-0000-0000-0000-000000008301', repeat('0', 64), repeat('1', 64), now() + interval '5 minutes'),
  ('00000000-0000-0000-0000-0000000080c1', null, repeat('2', 64), repeat('3', 64), now() + interval '5 minutes');
-- E: logo, sin cuentas; también una sesión de una ficha anonimizada antes
insert into public.end_customers (id, tenant_id, deleted_at) values
  ('00000000-0000-0000-0000-000000008401', '00000000-0000-0000-0000-0000000080e1', now());
insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000080e1', '00000000-0000-0000-0000-000000008401', repeat('4', 64), now() + interval '1 day');

select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.slugs_anteriores where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  9::bigint, 'antes de la purga C tiene 2 sesiones, 2 códigos, 2 canales, 2 configuraciones de avisos y 1 slug');  -- 41

select is(pg_temp.como('purge_job',
  $q$select string_agg(coalesce(p.auth_user_id::text, '-') || '|' || coalesce(p.logo_ruta, '-'), ',' order by p.auth_user_id)
       from public.purge_tenant_data('00000000-0000-0000-0000-0000000080c1') p$q$),
  '00000000-0000-0000-0000-00000000c0a1|00000000-0000-0000-0000-0000000080c1/logo-cccccccccccccccccccccccccccccccc.webp,'
  || '00000000-0000-0000-0000-00000000c0a2|00000000-0000-0000-0000-0000000080c1/logo-cccccccccccccccccccccccccccccccc.webp',
  'la purga (como purge_job) devuelve cada cuenta de Auth con la ruta del logo');  -- 42

select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  0::bigint, 'la purga no deja sesiones del negocio');  -- 43
select is((select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  0::bigint, 'la purga no deja códigos del negocio (ni los que no se ligan a una ficha)');  -- 44
select is((select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  0::bigint, 'la purga no deja canales del negocio');  -- 45
select is((select count(*) from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  0::bigint, 'la purga no deja configuración de avisos del negocio');  -- 46
select is((select count(*) from public.slugs_anteriores where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  0::bigint, 'la purga no deja slugs anteriores del negocio');  -- 47
select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008301'), null,
  'la purga anonimiza las fichas vivas con la lógica nueva (sin email)');  -- 48
select is((select settings ->> 'logo_ruta' from public.tenants where id = '00000000-0000-0000-0000-0000000080c1'),
  '00000000-0000-0000-0000-0000000080c1/logo-cccccccccccccccccccccccccccccccc.webp',
  'la purga no quita logo_ruta de settings: un reintento la vuelve a devolver');  -- 49
select is(
  (select (metadata ->> 'sesiones_web_deleted') || '/' || (metadata ->> 'codigos_web_deleted') || '/'
          || (metadata ->> 'avisos_negocio_config_deleted') || '/' || (metadata ->> 'slugs_anteriores_deleted') || '/'
          || (metadata ->> 'logo_pendiente_de_borrar')
     from public.audit_log
    where tenant_id = '00000000-0000-0000-0000-0000000080c1' and action = 'tenant_purge_step'
      and metadata ->> 'step' = 'anonymize_end_customers'),
  '1/1/2/1/true', 'el paso de la purga deja constancia de cuántas filas borró, sin valores');  -- 50

-- aislamiento: la purga de C no toca B
select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000080b1')
          + (select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000080b1')
          + (select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000080b1')
          + (select count(*) from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000080b1')
          + (select count(*) from public.slugs_anteriores where tenant_id = '00000000-0000-0000-0000-0000000080b1'),
  5::bigint, 'la purga de C no toca las sesiones, códigos, canales, avisos ni slugs de B');  -- 51
select is((select settings ->> 'logo_ruta' from public.tenants where id = '00000000-0000-0000-0000-0000000080b1'),
  '00000000-0000-0000-0000-0000000080b1/logo-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.png', 'ni el logo de B');  -- 52
select is((select email from public.end_customers where id = '00000000-0000-0000-0000-000000008201'), 'ana@a.test',
  'ni las fichas de B');  -- 53

-- reanudable: repetirla no falla, devuelve lo mismo y no duplica nada
select is(pg_temp.como('purge_job',
  $q$select string_agg(coalesce(p.auth_user_id::text, '-') || '|' || coalesce(p.logo_ruta, '-'), ',' order by p.auth_user_id)
       from public.purge_tenant_data('00000000-0000-0000-0000-0000000080c1') p$q$),
  '00000000-0000-0000-0000-00000000c0a1|00000000-0000-0000-0000-0000000080c1/logo-cccccccccccccccccccccccccccccccc.webp,'
  || '00000000-0000-0000-0000-00000000c0a2|00000000-0000-0000-0000-0000000080c1/logo-cccccccccccccccccccccccccccccccc.webp',
  'repetir la purga no falla y devuelve las mismas cuentas y el mismo logo');  -- 54
select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000080c1')
          + (select count(*) from public.slugs_anteriores where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  0::bigint, 'tras repetirla sigue sin quedar ninguna fila de C');  -- 55
select is((select count(*) from public.end_customers where tenant_id = '00000000-0000-0000-0000-0000000080c1'
             and deleted_at is not null and email is null and politica_version is null),
  2::bigint, 'ni duplica ni deshace las fichas anonimizadas');  -- 56

-- interrumpida a mitad: si quedan filas colgadas de una corrida anterior, la siguiente las borra
insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000080c1', repeat('5', 64), repeat('6', 64), now() + interval '5 minutes');
select pg_temp.como('purge_job', $q$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000080c1')$q$);
select is((select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000080c1'),
  0::bigint, 'una corrida posterior borra lo que haya quedado colgado');  -- 57

-- sin cuentas: con logo, una sola fila con la cuenta en nulo; sin logo, ninguna fila
select is(pg_temp.como('purge_job',
  $q$select string_agg(coalesce(p.auth_user_id::text, '-') || '|' || coalesce(p.logo_ruta, '-'), ',')
       from public.purge_tenant_data('00000000-0000-0000-0000-0000000080e1') p$q$),
  '-|00000000-0000-0000-0000-0000000080e1/logo-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.jpg',
  'sin cuentas pero con logo: una fila con la cuenta nula y la ruta');  -- 58
select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000080e1'),
  0::bigint, 'la sesión colgada de una ficha anonimizada antes también se borra');  -- 59
select is(pg_temp.como('purge_job',
  $q$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000080f1')$q$),
  '0', 'sin cuentas ni logo no devuelve filas');  -- 60
select is(pg_temp.como('web_reservas',
  $q$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000080c1')$q$),
  'err 42501', 'web_reservas no ejecuta la purga');  -- 61

select * from finish();
rollback;
