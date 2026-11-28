-- Outbox multicanal (§2.12, §6.4): destino (cliente o negocio), plantillas con CHECK, destinatarios por canal
-- (resolve_outbox_delivery), resultado por canal (registrar_entrega_outbox), aviso al negocio
-- (encolar_aviso_negocio), privilegios exactos de las funciones y del tenant, y borrado en anonimización y
-- purga. El reclamo con dos conexiones concurrentes está en tests/integracion/test_notification_outbox.py (pgTAP
-- corre en una sola sesión).
begin;
select plan(110);

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

-- Ejecuta una consulta (un valor) como un rol, con el contexto de tenant dado ('' = sin contexto).
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
    return 'err ' || sqlstate;
  end;
end
$$;

-- Ejecuta una sentencia como dueño y revierte sus efectos. Devuelve 'ok' o 'err <sqlstate>'.
create function pg_temp.intentar(p_sql text) returns text
language plpgsql as $$
begin
  execute p_sql;
  raise exception 'revertir' using errcode = 'ZZ001';
exception when others then
  if sqlstate = 'ZZ001' then
    return 'ok';
  end if;
  return 'err ' || sqlstate;
end
$$;

-- Destinatarios que devuelve resolve_outbox_delivery como un rol: 'canal:identificador:entregado:destino', con
-- telegram primero; '<ninguno>' si no devuelve filas; 'err <sqlstate>' si falla.
create function pg_temp.destinos(p_rol text, p_ctx text, p_id uuid) returns text
language plpgsql as $$
declare
  v text;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
  begin
    select coalesce(string_agg(coalesce(r.channel, '-') || ':' || coalesce(r.channel_user_id, '-') || ':'
                               || r.entregado::text || ':' || r.destino,
                               ',' order by r.channel desc nulls last, r.channel_user_id), '<ninguno>')
      into v
      from public.resolve_outbox_delivery(p_id) r;
    reset role;
    return v;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
end
$$;

-- Clave de entrega del canal de un cliente (la que devuelve resolve_outbox_delivery).
create function pg_temp.clave(p_identificador text) returns text
language sql as $$
  select 'cc:' || cc.id::text from public.canales_cliente cc where cc.identificador = p_identificador
$$;

-- ---------------------------------------------------------------------------
-- Datos: A (con clientes, personal y turno), B (aislamiento) y D (purga)
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000075a1', 'A', 'multicanal-a', 'active', null),
  ('00000000-0000-0000-0000-0000000075a2', 'B', 'multicanal-b', 'active', null),
  ('00000000-0000-0000-0000-0000000075a3', 'Vencido', 'multicanal-vencido', 'inactive', now() - interval '100 days');
-- A: dueño (b1), aprobador (b2), personal común (b3), aprobador inactivo (b4), aprobador sin email (b6). B: dueño (b5).
insert into public.tenant_users (id, tenant_id, is_owner, is_active, pending_invite_email) values
  ('00000000-0000-0000-0000-0000000075b1', '00000000-0000-0000-0000-0000000075a1', true, true, 'duenio@a.test'),
  ('00000000-0000-0000-0000-0000000075b2', '00000000-0000-0000-0000-0000000075a1', false, true, 'aprueba@a.test'),
  ('00000000-0000-0000-0000-0000000075b3', '00000000-0000-0000-0000-0000000075a1', false, true, 'comun@a.test'),
  ('00000000-0000-0000-0000-0000000075b4', '00000000-0000-0000-0000-0000000075a1', false, false, 'inactivo@a.test'),
  ('00000000-0000-0000-0000-0000000075b5', '00000000-0000-0000-0000-0000000075a2', true, true, 'duenio@b.test'),
  ('00000000-0000-0000-0000-0000000075b6', '00000000-0000-0000-0000-0000000075a1', false, true, null);
insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075b2', 'approve_bookings'),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075b4', 'approve_bookings'),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075b6', 'approve_bookings');
insert into public.end_customers (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000075c1', '00000000-0000-0000-0000-0000000075a1'),
  ('00000000-0000-0000-0000-0000000075c2', '00000000-0000-0000-0000-0000000075a1'),
  ('00000000-0000-0000-0000-0000000075c3', '00000000-0000-0000-0000-0000000075a2'),
  ('00000000-0000-0000-0000-0000000075c4', '00000000-0000-0000-0000-0000000075a3');
-- c1: telegram y email verificados (los dos canales de aviso), whatsapp verificado (todavía no envía) y un
-- telegram sin verificar. c2: un canal verificado pero la ficha está anonimizada. c3 es de B.
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'telegram', 'tg-75-1', now()),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'email', 'uno@mail.test', now()),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'whatsapp', 'wa-75-1', now()),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'telegram', 'tg-75-1b', null),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c2', 'telegram', 'tg-75-2', now()),
  ('00000000-0000-0000-0000-0000000075a2', '00000000-0000-0000-0000-0000000075c3', 'telegram', 'tg-75-3', now());
update public.end_customers set deleted_at = now() where id = '00000000-0000-0000-0000-0000000075c2';
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000075d1', '00000000-0000-0000-0000-0000000075a1', 'Ana'),
  ('00000000-0000-0000-0000-0000000075d2', '00000000-0000-0000-0000-0000000075a2', 'Beto');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000075e1', '00000000-0000-0000-0000-0000000075a1', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000075e2', '00000000-0000-0000-0000-0000000075a2', 'Tintura', 30, 100);
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status,
                             pending_expires_at) values
  ('00000000-0000-0000-0000-0000000075f1', '00000000-0000-0000-0000-0000000075a1',
   '00000000-0000-0000-0000-0000000075c1', '00000000-0000-0000-0000-0000000075e1',
   '00000000-0000-0000-0000-0000000075d1', tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'),
   'pending', now() - interval '1 minute'),
  ('00000000-0000-0000-0000-0000000075f2', '00000000-0000-0000-0000-0000000075a2',
   '00000000-0000-0000-0000-0000000075c3', '00000000-0000-0000-0000-0000000075e2',
   '00000000-0000-0000-0000-0000000075d2', tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'),
   'pending', now() - interval '1 minute');

-- Filas del outbox: O1 cliente con dos canales, O2 cliente anonimizado, O3 negocio (A), O4 sincronización de
-- Calendar (A), O5 negocio (B), O6 cliente (B).
insert into public.notification_outbox (id, tenant_id, end_customer_id, destino, channel, template, params) values
  ('00000000-0000-0000-0000-000000750001', '00000000-0000-0000-0000-0000000075a1',
   '00000000-0000-0000-0000-0000000075c1', 'cliente', 'telegram', 'turno_confirmado',
   '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750002', '00000000-0000-0000-0000-0000000075a1',
   '00000000-0000-0000-0000-0000000075c2', 'cliente', 'telegram', 'turno_cancelado',
   '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750006', '00000000-0000-0000-0000-0000000075a2',
   '00000000-0000-0000-0000-0000000075c3', 'cliente', 'telegram', 'turno_confirmado',
   '{"booking_id": "00000000-0000-0000-0000-0000000075f2"}');
insert into public.notification_outbox (id, tenant_id, destino, template, params) values
  ('00000000-0000-0000-0000-000000750003', '00000000-0000-0000-0000-0000000075a1', 'negocio',
   'aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750004', '00000000-0000-0000-0000-0000000075a1', 'negocio',
   'sincronizacion_calendar', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750005', '00000000-0000-0000-0000-0000000075a2', 'negocio',
   'aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000075f2"}');

-- Tenant E: dueño sin email (negocio sin destinatarios), luego con invitación pendiente y luego vinculado a una cuenta.
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000075a4', 'E', 'multicanal-e');
insert into public.tenant_users (id, tenant_id, is_owner, is_active) values
  ('00000000-0000-0000-0000-0000000075b8', '00000000-0000-0000-0000-0000000075a4', true, true);
insert into auth.users (id, email) values ('00000000-0000-0000-0000-0000000075a0', 'real@e.test');
insert into public.end_customers (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000075c5', '00000000-0000-0000-0000-0000000075a4'),
  ('00000000-0000-0000-0000-0000000075c6', '00000000-0000-0000-0000-0000000075a1');
-- c6 (de A) solo tiene un email verificado.
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c6', 'email', 'solo@mail.test', now());
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000075d4', '00000000-0000-0000-0000-0000000075a4', 'Eva');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000075e4', '00000000-0000-0000-0000-0000000075a4', 'Masaje', 30, 100);
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status,
                             pending_expires_at) values
  ('00000000-0000-0000-0000-0000000075f4', '00000000-0000-0000-0000-0000000075a4',
   '00000000-0000-0000-0000-0000000075c5', '00000000-0000-0000-0000-0000000075e4',
   '00000000-0000-0000-0000-0000000075d4', tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'),
   'pending', now() - interval '1 minute');
-- c10: ficha vigente sin ningún canal verificado (O8).
insert into public.end_customers (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000075ca', '00000000-0000-0000-0000-0000000075a1');
insert into public.notification_outbox (id, tenant_id, end_customer_id, destino, channel, template, params) values
  ('00000000-0000-0000-0000-000000750008', '00000000-0000-0000-0000-0000000075a1',
   '00000000-0000-0000-0000-0000000075ca', 'cliente', 'telegram', 'turno_confirmado',
   '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}');
insert into public.notification_outbox (id, tenant_id, destino, template, params) values
  ('00000000-0000-0000-0000-000000750007', '00000000-0000-0000-0000-0000000075a4', 'negocio',
   'aviso_negocio_cancelacion', '{"booking_id": "00000000-0000-0000-0000-0000000075f4"}');

-- ---------------------------------------------------------------------------
-- Estructura y CHECK
-- ---------------------------------------------------------------------------
select has_column('public', 'notification_outbox', 'destino', 'notification_outbox tiene destino');  -- 1
select has_column('public', 'notification_outbox', 'entregas', 'notification_outbox tiene entregas');  -- 2
select is((select destino || '|' || entregas::text from public.notification_outbox  -- 3
            where id = '00000000-0000-0000-0000-000000750001'),
  'cliente|{}', 'una fila nueva del cliente nace con destino cliente y sin entregas');
select is(pg_temp.intentar($q$insert into public.notification_outbox (tenant_id, destino, template)  -- 4
  values ('00000000-0000-0000-0000-0000000075a1', 'negocio', 'aviso_negocio_cancelacion')$q$),
  'ok', 'una fila del negocio sin ficha de cliente ni canal es válida');
select is(pg_temp.intentar($q$insert into public.notification_outbox (tenant_id, destino, template)  -- 5
  values ('00000000-0000-0000-0000-0000000075a1', 'negocio', 'turno_confirmado')$q$),
  'err 23514', 'una plantilla del cliente no vale para el negocio');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 6
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'cliente', 'telegram',
          'aviso_negocio_reserva')$q$),
  'err 23514', 'una plantilla del negocio no vale para el cliente');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 7
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'cliente', 'telegram',
          'recordatorio')$q$),
  'err 23514', 'una plantilla desconocida se rechaza');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 8
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'cliente', null,
          'turno_confirmado')$q$),
  'err 23514', 'una fila del cliente exige canal de origen');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 9
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'cliente', 'email',
          'cambio_horario_aprobado')$q$),
  'ok', 'el canal de origen email y las plantillas de cambio de horario valen para el cliente');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 10
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'cliente', 'sms',
          'turno_confirmado')$q$),
  'err 23514', 'un canal de origen fuera de telegram, whatsapp y email se rechaza');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 11
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a1', null, 'cliente', 'telegram', 'turno_confirmado')$q$),
  'err 23514', 'una fila del cliente exige la ficha');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 12
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'negocio', null,
          'aviso_negocio_reserva')$q$),
  'err 23514', 'una fila del negocio no puede colgar de una ficha');
select is(pg_temp.intentar($q$insert into public.notification_outbox (tenant_id, destino, template)  -- 13
  values ('00000000-0000-0000-0000-0000000075ff', 'negocio', 'aviso_negocio_reserva')$q$),
  'err 23503', 'una fila del negocio exige un tenant existente');
select is(pg_temp.intentar($q$insert into public.notification_outbox (tenant_id, destino, template, entregas)  -- 14
  values ('00000000-0000-0000-0000-0000000075a1', 'negocio', 'aviso_negocio_reserva', '[]')$q$),
  'err 23514', 'entregas debe ser un objeto');
select is(pg_temp.intentar($q$insert into public.notification_outbox  -- 15
  (tenant_id, end_customer_id, destino, channel, template)
  values ('00000000-0000-0000-0000-0000000075a2', '00000000-0000-0000-0000-0000000075c1', 'cliente', 'telegram',
          'turno_confirmado')$q$),
  'ok', 'en una fila del cliente el trigger sigue forzando tenant_id desde la ficha');
select is((select string_agg(distinct o.tenant_id::text, ',') from public.notification_outbox o  -- 16
            where o.end_customer_id = '00000000-0000-0000-0000-0000000075c1'),
  '00000000-0000-0000-0000-0000000075a1', 'ninguna fila de la ficha de A quedó en otro tenant');

-- ---------------------------------------------------------------------------
-- Privilegios exactos
-- ---------------------------------------------------------------------------
select is((select string_agg(r, ',' order by r)  -- 17
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            cross join unnest(array['destino', 'entregas', 'end_customer_id', 'channel', 'status']) c
            where has_column_privilege(r, 'public.notification_outbox', c, 'SELECT,INSERT,UPDATE,REFERENCES')),
  null, 'ningún rol de aplicación tiene privilegios sobre las columnas de la tabla (nuevas ni viejas)');
select is((select string_agg(r, ',' order by r)  -- 18
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_table_privilege(r, 'public.notification_outbox', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
               or has_any_column_privilege(r, 'public.notification_outbox', 'SELECT,INSERT,UPDATE,REFERENCES')),
  null, 'ningún rol de aplicación tiene privilegios sobre la tabla');
select is((select string_agg(r, ',' order by r)  -- 19
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_function_privilege(r, 'public.resolve_outbox_delivery(uuid)', 'execute')),
  'scheduler_job', 'resolve_outbox_delivery: solo scheduler_job');
select is((select string_agg(r, ',' order by r)  -- 20
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_function_privilege(r, 'public.registrar_entrega_outbox(uuid, text, text, text)', 'execute')),
  'scheduler_job', 'registrar_entrega_outbox: solo scheduler_job');
select is((select string_agg(r, ',' order by r)  -- 21
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_function_privilege(r, 'public.encolar_aviso_negocio(text, jsonb, integer)', 'execute')),
  'bot_service,dashboard_service,scheduler_job', 'encolar_aviso_negocio: bot, dashboard y scheduler_job');
select is((select string_agg(r, ',' order by r)  -- 22
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_function_privilege(r, 'public.outbox_destinatarios(uuid, uuid)', 'execute')),
  null, 'outbox_destinatarios (interna): ningún rol la ejecuta');
select is((select string_agg(r, ',' order by r)  -- +
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_function_privilege(r, 'public.reclamar_avisos_telegram(integer, integer, integer)', 'execute')),
  'scheduler_job', 'reclamar_avisos_telegram: solo scheduler_job');
select is((select string_agg(r, ',' order by r)  -- 23
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_function_privilege(r, 'public.claim_notification_outbox(integer, integer, integer)', 'execute')),
  'scheduler_job', 'claim_notification_outbox conserva su EXECUTE: solo scheduler_job');
select is((select string_agg(r, ',' order by r)  -- 24
             from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
            where has_function_privilege(r, 'public.complete_notification_outbox(uuid, text, text, integer)', 'execute')),
  'bot_service,dashboard_service,scheduler_job', 'complete_notification_outbox conserva su EXECUTE');
select is_empty($q$select p.oid::regprocedure::text  -- 25
  from pg_proc p
  cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
 where p.oid in ('public.resolve_outbox_delivery(uuid)'::regprocedure,
                 'public.registrar_entrega_outbox(uuid, text, text, text)'::regprocedure,
                 'public.encolar_aviso_negocio(text, jsonb, integer)'::regprocedure,
                 'public.outbox_destinatarios(uuid, uuid)'::regprocedure,
                 'public.reclamar_avisos_telegram(integer, integer, integer)'::regprocedure,
                 'public.claim_notification_outbox(integer, integer, integer)'::regprocedure,
                 'public.complete_notification_outbox(uuid, text, text, integer)'::regprocedure)
   and a.grantee = 0$q$,
  'ninguna de las funciones del outbox tiene EXECUTE para PUBLIC');
select is((select count(*)::int from pg_proc p  -- 26
            where p.oid in ('public.resolve_outbox_delivery(uuid)'::regprocedure,
                            'public.registrar_entrega_outbox(uuid, text, text, text)'::regprocedure,
                            'public.encolar_aviso_negocio(text, jsonb, integer)'::regprocedure,
                            'public.outbox_destinatarios(uuid, uuid)'::regprocedure,
                            'public.reclamar_avisos_telegram(integer, integer, integer)'::regprocedure,
                            'public.claim_notification_outbox(integer, integer, integer)'::regprocedure,
                            'public.complete_notification_outbox(uuid, text, text, integer)'::regprocedure)
              and p.prosecdef
              and (p.proconfig @> array['search_path=""'] or p.proconfig @> array['search_path='])),
  7, 'las siete funciones son SECURITY DEFINER con search_path vacío');

-- ---------------------------------------------------------------------------
-- resolve_outbox_delivery: cliente
-- ---------------------------------------------------------------------------
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 27
                           '00000000-0000-0000-0000-000000750001'),
  'telegram:tg-75-1:false:cliente,email:uno@mail.test:false:cliente',
  'cliente con dos canales: devuelve telegram y email verificados, telegram primero (ni whatsapp ni los sin verificar)');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1', $q$  -- 28
  select r.service_name || '|' || r.professional_name || '|' || r.booking_status || '|' || r.tenant_active::text
         || '|' || count(*) over ()
    from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000750001') r order by r.channel desc limit 1$q$),
  'Corte|Ana|pending|true|2', 'cada fila trae los datos del turno y el estado del tenant');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1', $q$  -- 29
  select count(distinct r.clave)::text || '|' || count(*) filter (where r.clave like 'cc:%')
    from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000750001') r$q$),
  '2|2', 'cada canal tiene su propia clave de entrega');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 30
                           '00000000-0000-0000-0000-000000750002'),
  '-:-:false:cliente', 'cliente anonimizado: una sola fila sin canal ni identificador (se descarta)');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 31
                           '00000000-0000-0000-0000-00000000dead'),
  '<ninguno>', 'una fila inexistente no devuelve nada');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- +
  $q$select string_agg(r.anonimizada::text, ',') from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000750001') r$q$),
  'false,false', 'una ficha vigente no figura como anonimizada');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- +
  $q$select r.anonimizada::text || '|' || coalesce(r.channel, '-') || '|' || count(*) over ()
       from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000750002') r$q$),
  'true|-|1', 'una ficha anonimizada devuelve una fila sin canal y marcada anonimizada');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- +
  $q$select r.anonimizada::text || '|' || coalesce(r.channel, '-') || '|' || count(*) over ()
       from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000750008') r$q$),
  'false|-|1', 'una ficha vigente sin canales verificados devuelve una fila sin canal y NO anonimizada');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- +
  $q$select string_agg(distinct r.anonimizada::text, ',')
       from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000750003') r$q$),
  'false', 'los avisos al negocio no figuran como anonimizados');
select is(pg_temp.destinos('scheduler_job', '', '00000000-0000-0000-0000-000000750001'),  -- 32
  'err 42501', 'sin contexto de tenant lanza insufficient_privilege');
select is(pg_temp.destinos('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 33
                           '00000000-0000-0000-0000-000000750001'),
  'err 42501', 'un rol sin EXECUTE no puede resolver');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a2',  -- 34
                           '00000000-0000-0000-0000-000000750001'),
  '<ninguno>', 'aislamiento: con el contexto de B no se resuelve la fila de A');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 35
                           '00000000-0000-0000-0000-000000750006'),
  '<ninguno>', 'aislamiento: con el contexto de A no se resuelve la fila de B');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a2',  -- 36
                           '00000000-0000-0000-0000-000000750006'),
  'telegram:tg-75-3:false:cliente', 'el cliente de B se resuelve con sus propios canales');

-- ---------------------------------------------------------------------------
-- resolve_outbox_delivery: negocio
-- ---------------------------------------------------------------------------
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 37
                           '00000000-0000-0000-0000-000000750003'),
  'email:aprueba@a.test:false:negocio,email:duenio@a.test:false:negocio',
  'negocio con la configuración por defecto: el dueño y quien aprueba (no el personal común, ni el inactivo, ni el que no tiene email)');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a2',  -- 38
                           '00000000-0000-0000-0000-000000750005'),
  'email:duenio@b.test:false:negocio', 'el negocio de B recibe en su propio dueño');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a2',  -- 39
                           '00000000-0000-0000-0000-000000750003'),
  '<ninguno>', 'aislamiento: con el contexto de B no se resuelve el aviso al negocio de A');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 40
                           '00000000-0000-0000-0000-000000750004'),
  'calendar:-:false:negocio', 'sincronización de Calendar: un único destino sin identificador');
-- configuración personalizada: entra el personal común, sale quien aprueba y el dueño deja de recibir por email
insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075b3', true, true);
insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075b2', false, true);
insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075b1', true, false);
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 41
                           '00000000-0000-0000-0000-000000750003'),
  'email:comun@a.test:false:negocio', 'negocio con configuración personalizada: solo quien la configuración dice');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a2',  -- 42
                           '00000000-0000-0000-0000-000000750005'),
  'email:duenio@b.test:false:negocio', 'la configuración de A no cambia los destinatarios de B');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 43
                           '00000000-0000-0000-0000-000000750004'),
  'calendar:-:false:negocio', 'la sincronización de Calendar no depende de la configuración de avisos');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1', $q$  -- 44
  select string_agg(r.clave, ',' order by r.clave)
    from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000750003') r$q$),
  'tu:00000000-0000-0000-0000-0000000075b3:email', 'la clave del negocio identifica al miembro, no su email');

-- negocio sin destinatarios: una sola fila con canal e identificador NULL (distinta de la fila inexistente)
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a4',  -- +
                           '00000000-0000-0000-0000-000000750007'),
  '-:-:false:negocio', 'negocio sin ningún destinatario con email: una fila con canal e identificador nulos');
update public.tenant_users set pending_invite_email = 'viejo@e.test'
 where id = '00000000-0000-0000-0000-0000000075b8';
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a4',  -- +
                           '00000000-0000-0000-0000-000000750007'),
  'email:viejo@e.test:false:negocio', 'sin cuenta vinculada se usa el email de la invitación pendiente');
update public.tenant_users set auth_user_id = '00000000-0000-0000-0000-0000000075a0'
 where id = '00000000-0000-0000-0000-0000000075b8';
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a4',  -- +
                           '00000000-0000-0000-0000-000000750007'),
  'email:real@e.test:false:negocio',
  'con cuenta vinculada (auth.users) se usa el email de la cuenta, no el de la invitación');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- +
                           '00000000-0000-0000-0000-000000750007'),
  '<ninguno>', 'aislamiento: con el contexto de A no se resuelve el aviso al negocio de E');

-- ---------------------------------------------------------------------------
-- encolar_aviso_negocio y enqueue_notification
-- ---------------------------------------------------------------------------
select is(pg_temp.como('bot_service', '',  -- 45
  $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'::jsonb)::text$q$),
  'err 42501', 'sin contexto de tenant lanza insufficient_privilege');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-0000000075a1',  -- 46
  $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'::jsonb)::text$q$),
  'err 42501', 'un rol sin EXECUTE no puede encolar al negocio');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 47
  $q$select public.encolar_aviso_negocio('turno_confirmado', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'::jsonb)::text$q$),
  'err 23514', 'una plantilla del cliente no se puede encolar al negocio');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 48
  $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '[]'::jsonb)::text$q$),
  'err 22023', 'params debe ser un objeto con booking_id');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- +
  $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '{}'::jsonb)::text$q$),
  'err 22023', 'un aviso al negocio sin booking_id se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- +
  $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '{"booking_id": "no-es-un-uuid"}'::jsonb)::text$q$),
  'err 22023', 'un booking_id que no es uuid se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- +
  $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', null)::text$q$),
  'err 22023', 'params nulo se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 49
  $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'::jsonb, -1)::text$q$),
  'err 22023', 'una demora negativa se rechaza');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 50
  $q$select public.encolar_aviso_negocio('aviso_negocio_pedido_inexistente', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'::jsonb)::text$q$) = 'err 23514',
  'una plantilla desconocida se rechaza');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 51
  $q$select public.encolar_aviso_negocio('aviso_negocio_aprobacion',
       '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'::jsonb)::text$q$) ~ '^[0-9a-f-]{36}$',
  'bot_service encola un aviso al negocio y recibe el id');
select ok(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000075a1',  -- 52
  $q$select public.encolar_aviso_negocio('aviso_negocio_movimiento', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'::jsonb, 60)::text$q$)
  ~ '^[0-9a-f-]{36}$', 'dashboard_service encola un aviso al negocio');
select is((select o.destino || '|' || o.tenant_id::text || '|' || coalesce(o.end_customer_id::text, '-') || '|'  -- 53
                  || coalesce(o.channel, '-') || '|' || o.status || '|' || o.attempts || '|' || o.entregas::text
             from public.notification_outbox o where o.template = 'aviso_negocio_aprobacion'),
  'negocio|00000000-0000-0000-0000-0000000075a1|-|-|pending|0|{}',
  'la fila del negocio nace en el tenant del contexto, sin ficha ni canal, pending y sin entregas');
select ok((select o.next_attempt_at <= now() + interval '5 seconds' from public.notification_outbox o  -- 54
            where o.template = 'aviso_negocio_aprobacion')
      and (select o.next_attempt_at > now() + interval '50 seconds' from public.notification_outbox o
            where o.template = 'aviso_negocio_movimiento'),
  'el primer intento es inmediato por defecto y respeta la demora pedida');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 55
  $q$select public.enqueue_notification('00000000-0000-0000-0000-0000000075c1', 'telegram',
       'aviso_negocio_reserva')::text$q$),
  'err 23514', 'enqueue_notification no acepta una plantilla del negocio');
select ok(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 56
  $q$select public.enqueue_notification('00000000-0000-0000-0000-0000000075c1', 'email',
       'cambio_horario_rechazado')::text$q$) ~ '^[0-9a-f-]{36}$',
  'enqueue_notification acepta el canal de origen email y las plantillas nuevas del cliente');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- 57
  $q$select public.enqueue_notification('00000000-0000-0000-0000-0000000075c3', 'telegram',
       'turno_confirmado')::text$q$),
  'err 42501', 'enqueue_notification sigue rechazando un cliente de otro tenant');

-- ---------------------------------------------------------------------------
-- registrar_entrega_outbox: resultado por canal
-- ---------------------------------------------------------------------------
select is(pg_temp.como('scheduler_job', '',  -- 58
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'err 42501', 'sin contexto de tenant lanza insufficient_privilege');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000075a1',  -- +
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'err 42501', 'bot_service no puede marcar un canal como entregado');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000075a1',  -- +
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'err 42501', 'dashboard_service no puede marcar un canal como entregado');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-0000000075a1',  -- 59
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'err 42501', 'un rol sin EXECUTE no puede registrar');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 60
  format($q$select public.registrar_entrega_outbox(%L, %L, 'enviado')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'err 22023', 'un estado inválido se rechaza');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 61
  $q$select public.registrar_entrega_outbox('00000000-0000-0000-0000-000000750001', '', 'sent')::text$q$),
  'err 22023', 'una clave vacía se rechaza');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 62
  $q$select public.registrar_entrega_outbox('00000000-0000-0000-0000-000000750001', 'cc:inventada', 'sent')::text$q$),
  'false', 'una clave que no es de un destinatario de la fila no se registra');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 63
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-3'))),
  'false', 'el canal de un cliente de otro tenant no se registra en la fila');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a2',  -- 64
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'false', 'aislamiento: con el contexto de B no se registra nada en la fila de A');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 65
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'true', 'el canal telegram sale: se registra sent');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 66
  format($q$select public.registrar_entrega_outbox(%L, %L, 'failed', repeat('x', 500))::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('uno@mail.test'))),
  'true', 'el canal email falla: se registra failed sin afectar al telegram');
select is((select ((o.entregas -> pg_temp.clave('tg-75-1')) ->> 'estado') || '|'  -- 67
                  || ((o.entregas -> pg_temp.clave('uno@mail.test')) ->> 'estado') || '|'
                  || length((o.entregas -> pg_temp.clave('uno@mail.test')) ->> 'error')
             from public.notification_outbox o where o.id = '00000000-0000-0000-0000-000000750001'),
  'sent|failed|200', 'cada canal tiene su resultado y el error se trunca a 200 caracteres');
select ok((select o.entregas::text !~ 'tg-75-1|uno@mail' from public.notification_outbox o  -- 68
            where o.id = '00000000-0000-0000-0000-000000750001'),
  'las entregas no guardan identificadores de canal');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 69
                           '00000000-0000-0000-0000-000000750001'),
  'telegram:tg-75-1:true:cliente,email:uno@mail.test:false:cliente',
  'reintento: resolve marca como entregado el canal que ya salió y deja pendiente el que falló');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 70
  format($q$select public.registrar_entrega_outbox(%L, %L, 'failed', 'otra vez')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('tg-75-1'))),
  'false', 'un canal ya entregado no se pisa');
select is((select (o.entregas -> pg_temp.clave('tg-75-1')) ->> 'estado' from public.notification_outbox o  -- 71
            where o.id = '00000000-0000-0000-0000-000000750001'),
  'sent', 'y sigue sent');
-- el enviador reprograma la fila y la reclama de nuevo: solo falta el email
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 72
  $q$select public.complete_notification_outbox('00000000-0000-0000-0000-000000750001', 'pending', 'email', 0)::text$q$),
  'true', 'la fila se reprograma para reintentar');
select is(pg_temp.como('scheduler_job', '',  -- 73
  $q$select count(*)::text from public.claim_notification_outbox(50) where id = '00000000-0000-0000-0000-000000750001'$q$),
  '1', 'el reclamo vuelve a entregar la fila reprogramada');
select is((select o.attempts from public.notification_outbox o where o.id = '00000000-0000-0000-0000-000000750001'),  -- 74
  1, 'el reclamo suma un intento y conserva el tope de intentos');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 75
                           '00000000-0000-0000-0000-000000750001'),
  'telegram:tg-75-1:true:cliente,email:uno@mail.test:false:cliente',
  'tras el reclamo el telegram sigue entregado y solo queda el email');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 76
  format($q$select public.registrar_entrega_outbox(%L, %L, 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('uno@mail.test'))),
  'true', 'el reintento del email sale: failed pasa a sent');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 77
  $q$select public.complete_notification_outbox('00000000-0000-0000-0000-000000750001', 'sent')::text$q$),
  'true', 'la fila se cierra sent con los dos canales entregados');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 78
  format($q$select public.registrar_entrega_outbox(%L, %L, 'failed')::text$q$,
         '00000000-0000-0000-0000-000000750001', pg_temp.clave('uno@mail.test'))),
  'false', 'una fila cerrada no admite más resultados');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 79
  format($q$select public.registrar_entrega_outbox(%L, 'tu:00000000-0000-0000-0000-0000000075b3:email', 'sent')::text$q$,
         '00000000-0000-0000-0000-000000750003')),
  'true', 'el aviso al negocio registra el resultado por miembro');
select is(pg_temp.como('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 80
  $q$select public.registrar_entrega_outbox('00000000-0000-0000-0000-000000750003',
       'tu:00000000-0000-0000-0000-0000000075b2:email', 'sent')::text$q$),
  'false', 'el miembro que la configuración dejó fuera no es destinatario: no se registra');
select is(pg_temp.destinos('scheduler_job', '00000000-0000-0000-0000-0000000075a1',  -- 81
                           '00000000-0000-0000-0000-000000750003'),
  'email:comun@a.test:true:negocio', 'el aviso al negocio marca entregado al miembro que ya recibió');

-- ---------------------------------------------------------------------------
-- Reclamo: filas del negocio (sin ficha) y tope de intentos
-- ---------------------------------------------------------------------------
select is(pg_temp.como('scheduler_job', '',  -- 82
  $q$select count(*)::text from public.claim_notification_outbox(50)
      where id = '00000000-0000-0000-0000-000000750004' and end_customer_id is null and channel is null$q$),
  '0', 'las filas ya reclamadas están arrendadas y no se repiten (el reclamo de arriba las tomó)');
update public.notification_outbox set next_attempt_at = now() - interval '1 minute'
 where id = '00000000-0000-0000-0000-000000750004';
select is(pg_temp.como('scheduler_job', '',  -- 83
  $q$select count(*)::text from public.claim_notification_outbox(50)
      where id = '00000000-0000-0000-0000-000000750004' and end_customer_id is null and channel is null$q$),
  '1', 'el reclamo entrega las filas del negocio con ficha y canal nulos');
update public.notification_outbox set attempts = 5, next_attempt_at = now() - interval '1 minute'
 where id = '00000000-0000-0000-0000-000000750004';
select is(pg_temp.como('scheduler_job', '',  -- 84
  $q$select count(*)::text from public.claim_notification_outbox(50, 300, 5)
      where id = '00000000-0000-0000-0000-000000750004'$q$),
  '0', 'una fila del negocio con los reclamos agotados no se devuelve');
select is((select o.status from public.notification_outbox o where o.id = '00000000-0000-0000-0000-000000750004'),  -- 85
  'failed', 'y queda failed (dead-letter)');

-- ---------------------------------------------------------------------------
-- reclamar_avisos_telegram: solo lo que el job de vencimiento puede atender
-- ---------------------------------------------------------------------------
-- Fichas: c7 con un único canal verificado, un Telegram (reclamable); c1 con Telegram, email y WhatsApp verificados;
-- c8 con dos Telegram verificados; c9 con Telegram y WhatsApp verificados; c6 con solo un email.
insert into public.end_customers (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000075c7', '00000000-0000-0000-0000-0000000075a1'),
  ('00000000-0000-0000-0000-0000000075c8', '00000000-0000-0000-0000-0000000075a1'),
  ('00000000-0000-0000-0000-0000000075c9', '00000000-0000-0000-0000-0000000075a1');
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c7', 'telegram', 'tg-75-7', now()),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c8', 'telegram', 'tg-75-8', now()),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c8', 'telegram', 'tg-75-8b', now()),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c9', 'telegram', 'tg-75-9', now()),
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c9', 'whatsapp', 'wa-75-9', now());
-- R11 única reclamable (c7, plantilla de turno). R12 cambio de horario (c7). R13 negocio. R14 solo email (c6).
-- R15 Telegram y email (c1). R16 dos Telegram (c8). R17 Telegram y WhatsApp (c9).
insert into public.notification_outbox (id, tenant_id, end_customer_id, destino, channel, template, params) values
  ('00000000-0000-0000-0000-000000750011', '00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c7', 'cliente', 'telegram',
   'turno_vencido', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750012', '00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c7', 'cliente', 'telegram',
   'cambio_horario_aprobado', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750014', '00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c6', 'cliente', 'email',
   'turno_confirmado', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750015', '00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c1', 'cliente', 'telegram',
   'turno_cancelado', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750016', '00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c8', 'cliente', 'telegram',
   'turno_movido', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}'),
  ('00000000-0000-0000-0000-000000750017', '00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c9', 'cliente', 'telegram',
   'reserva_confirmada', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}');
insert into public.notification_outbox (id, tenant_id, destino, template, params) values
  ('00000000-0000-0000-0000-000000750013', '00000000-0000-0000-0000-0000000075a1', 'negocio', 'aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000075f1"}');
select is(pg_temp.como('bot_service', '',  -- +
  $q$select count(*)::text from public.reclamar_avisos_telegram()$q$),
  'err 42501', 'bot_service no puede reclamar solo por Telegram');
select is(pg_temp.como('scheduler_job', '', $q$select count(*)::text from public.reclamar_avisos_telegram(0)$q$),  -- +
  'err 22023', 'un límite inválido se rechaza');
select is(pg_temp.como('scheduler_job', '',  -- +
  $q$select string_agg(right(id::text, 2), ',' order by id) from public.reclamar_avisos_telegram(500)
      where id::text like '00000000-0000-0000-0000-0000007500%'$q$),
  '11', 'solo se reclama la fila de la ficha con un único canal verificado, un Telegram, y plantilla de turno');
select is((select string_agg(right(id::text, 2) || ':' || attempts, ',' order by id) from public.notification_outbox  -- +
            where id::text like '00000000-0000-0000-0000-00000075001%'),
  '11:1,12:0,13:0,14:0,15:0,16:0,17:0', 'las filas que el job no atiende no consumen intentos');
select is(pg_temp.como('scheduler_job', '',  -- +
  $q$select count(*)::text from public.reclamar_avisos_telegram(500)
      where id::text like '00000000-0000-0000-0000-00000075001%'$q$),
  '0', 'la fila reclamada queda arrendada y las demás siguen sin reclamarse en la corrida siguiente');
update public.notification_outbox set attempts = 5, next_attempt_at = now() - interval '1 minute'
 where id::text like '00000000-0000-0000-0000-00000075001%';
select is(pg_temp.como('scheduler_job', '',  -- +
  $q$select count(*)::text from public.reclamar_avisos_telegram(500, 300, 5)
      where id::text like '00000000-0000-0000-0000-00000075001%'$q$),
  '0', 'con los reclamos agotados no se devuelve nada');
select is((select string_agg(right(id::text, 2) || ':' || status, ',' order by id) from public.notification_outbox  -- +
            where id::text like '00000000-0000-0000-0000-00000075001%'),
  '11:failed,12:pending,13:pending,14:pending,15:pending,16:pending,17:pending',
  'el dead-letter cierra solo la fila de un único Telegram: las demás, aun con intentos agotados, quedan pending');
-- Si la ficha suma un canal verificado después del encolado, la fila deja de reclamarse
update public.notification_outbox set status = 'pending', processed_at = null, attempts = 0,
       next_attempt_at = now() - interval '1 minute' where id = '00000000-0000-0000-0000-000000750011';
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000075a1', '00000000-0000-0000-0000-0000000075c7', 'email', 'siete@mail.test', now());
select is(pg_temp.como('scheduler_job', '',  -- +
  $q$select count(*)::text from public.reclamar_avisos_telegram(500)
      where id = '00000000-0000-0000-0000-000000750011'$q$),
  '0', 'una ficha que verifica un segundo canal deja de ser reclamable por el job');

-- ---------------------------------------------------------------------------
-- Anonimización y purga
-- ---------------------------------------------------------------------------
select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000075a1', true);
select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000075c1', 'staff',
                                          '00000000-0000-0000-0000-00000000aaaa');
select is((select count(*)::int from public.notification_outbox o  -- 86
            where o.end_customer_id = '00000000-0000-0000-0000-0000000075c1'),
  0, 'anonimizar borra los avisos del cliente');
select is((select count(*)::int from public.notification_outbox o  -- 87
            where o.tenant_id = '00000000-0000-0000-0000-0000000075a1' and o.destino = 'negocio'),
  5, 'y no toca los avisos al negocio (no son de ninguna ficha)');
insert into public.notification_outbox (tenant_id, destino, template, params) values
  ('00000000-0000-0000-0000-0000000075a3', 'negocio', 'aviso_negocio_reserva', '{}');
select pg_temp.como('purge_job', '',
  $q$select count(*)::text from public.purge_tenant_data('00000000-0000-0000-0000-0000000075a3')$q$);
select is((select count(*)::int from public.notification_outbox o  -- 88
            where o.tenant_id = '00000000-0000-0000-0000-0000000075a3'),
  0, 'purge_tenant_data borra también los avisos al negocio del tenant');

select * from finish();
rollback;
