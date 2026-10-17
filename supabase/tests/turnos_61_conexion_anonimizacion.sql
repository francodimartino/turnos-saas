-- Conexión turnos <-> plataforma (§3.5, §2.2, §4.2): ramas de dominio de la anonimización del
-- end_customer (payer de payments, bookings pending futuros), anonimización de professionals.name en
-- la purga de tenant y calendar_scrub_targets (rechazos y grants).
--
-- Lo que depende de session_user (que el actor salga del rol real) se prueba con conexiones reales en
-- tests/integracion/test_conexion_roles.py. Acá las funciones de lógica (anonymize_end_customer_core,
-- calendar_scrub_targets_core) reciben el actor ya decidido, igual que en la plataforma.
begin;
select plan(46);

do $$
begin
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

-- A y B activos; C inactive con el período vencido; D inactive sin vencer
insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'conanon-a', 'active', null),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'conanon-b', 'active', null),
  ('00000000-0000-0000-0000-0000000000a3', 'C', 'conanon-c', 'inactive', now() - interval '100 days'),
  ('00000000-0000-0000-0000-0000000000a4', 'D', 'conanon-d', 'inactive', now() - interval '10 days');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof A1'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1', 'Prof A2 sin calendar'),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a3', 'Prof C1'),
  ('00000000-0000-0000-0000-0000000000b4', '00000000-0000-0000-0000-0000000000a3', 'Prof C2');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte A', 30, 100),
  ('00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000a3', 'Corte C', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'ca-x', '+5491100000001', 'Cliente X'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'ca-y', '+5491100000002', 'Cliente Y'),
  ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000a3', 'telegram', 'ca-z', '+5491100000003', 'Cliente Z'),
  ('00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'ca-w', '+5491100000004', 'Cliente W');
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 1, 'manual_grant');

-- Turnos del cliente X: pending futuro con sesión de pack (301), pending futuro (302), confirmed futuro con evento (303),
-- pending ya empezado (304: lo resuelve el job de vencimiento, no esta función), confirmed pasado con evento (307)
-- y confirmed futuro con evento en un profesional sin integración de calendar (309). Cliente Y: pending futuro (305)
-- y confirmed con evento (308). Cliente Z (tenant C): confirmed con evento (311) y pending futuro (312).
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at, booking_pack_id, google_calendar_event_id) values
  ('00000000-0000-0000-0000-000000000301', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'), 'pending', '2030-09-30 10:00+00', '00000000-0000-0000-0000-0000000000d1', null),
  ('00000000-0000-0000-0000-000000000302', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-02 10:00+00', '2030-10-02 10:30+00'), 'pending', '2030-10-01 10:00+00', null, null),
  ('00000000-0000-0000-0000-000000000303', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-03 10:00+00', '2030-10-03 10:30+00'), 'confirmed', null, null, 'evt-x-2'),
  ('00000000-0000-0000-0000-000000000304', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2020-01-01 10:00+00', '2020-01-01 10:30+00'), 'pending', '2020-01-01 09:00+00', null, null),
  ('00000000-0000-0000-0000-000000000305', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-04 10:00+00', '2030-10-04 10:30+00'), 'pending', '2030-10-03 10:00+00', null, null),
  ('00000000-0000-0000-0000-000000000307', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2020-02-01 10:00+00', '2020-02-01 10:30+00'), 'confirmed', null, null, 'evt-x-1'),
  ('00000000-0000-0000-0000-000000000308', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-05 10:00+00', '2030-10-05 10:30+00'), 'confirmed', null, null, 'evt-y-1'),
  ('00000000-0000-0000-0000-000000000309', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b2', tstzrange('2030-10-06 10:00+00', '2030-10-06 10:30+00'), 'confirmed', null, null, 'evt-x-3'),
  ('00000000-0000-0000-0000-000000000311', '00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-11-01 10:00+00', '2030-11-01 10:30+00'), 'confirmed', null, null, 'evt-z-1'),
  ('00000000-0000-0000-0000-000000000312', '00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-11-02 10:00+00', '2030-11-02 10:30+00'), 'pending', '2030-11-01 10:00+00', null, null);

insert into public.tenant_integrations (id, tenant_id, integration_type, professional_id) values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a1', 'google_calendar', '00000000-0000-0000-0000-0000000000b1'),
  ('00000000-0000-0000-0000-0000000000f3', '00000000-0000-0000-0000-0000000000a3', 'google_calendar', '00000000-0000-0000-0000-0000000000b3');

-- Pagos con datos del pagador (payload de Mercado Pago): X (depósito del 301 y pack d1), Y (depósito del 305), Z (311)
insert into public.payments (id, tenant_id, payable_type, payable_id, provider_payment_id, amount, status, webhook_payload) values
  ('00000000-0000-0000-0000-000000000501', '00000000-0000-0000-0000-0000000000a1', 'booking_deposit', '00000000-0000-0000-0000-000000000301', 'mp-x-1', 10, 'approved',
   '{"id": 1, "status": "approved", "payer": {"email": "x@example.com"}, "additional_info": {"items": [1], "payer": {"first_name": "Nombre"}}, "card": {"cardholder": {"name": "Titular"}, "last_four_digits": "1234"}}'),
  ('00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-0000000000a1', 'booking_pack', '00000000-0000-0000-0000-0000000000d1', 'mp-x-2', 50, 'approved',
   '{"id": 2, "status": "approved", "payer": {"email": "x@example.com"}, "additional_info": {"items": [2], "payer": {"first_name": "Nombre"}}, "card": {"cardholder": {"name": "Titular"}, "last_four_digits": "1234"}}'),
  ('00000000-0000-0000-0000-000000000503', '00000000-0000-0000-0000-0000000000a1', 'booking_deposit', '00000000-0000-0000-0000-000000000305', 'mp-y-1', 10, 'approved',
   '{"id": 3, "payer": {"email": "y@example.com"}}'),
  ('00000000-0000-0000-0000-000000000504', '00000000-0000-0000-0000-0000000000a3', 'booking_deposit', '00000000-0000-0000-0000-000000000311', 'mp-z-1', 10, 'approved',
   '{"id": 4, "payer": {"email": "z@example.com"}}');

-- ---------------------------------------------------------------------------
-- Anonimización de X (como staff del tenant A): ramas de dominio
-- ---------------------------------------------------------------------------
select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000000a1', true);
select lives_ok(  -- 1
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'staff', null)$$,
  'anonymize_end_customer_core con las tablas de dominio presentes');

select is((select webhook_payload from public.payments where id = '00000000-0000-0000-0000-000000000501'),  -- 2
  '{"id": 1, "status": "approved", "additional_info": {"items": [1]}, "card": {"last_four_digits": "1234"}}'::jsonb,
  'depósito de X: payer, additional_info.payer y card.cardholder desaparecen del webhook_payload; el resto se conserva');
select is((select webhook_payload from public.payments where id = '00000000-0000-0000-0000-000000000502'),  -- 3
  '{"id": 2, "status": "approved", "additional_info": {"items": [2]}, "card": {"last_four_digits": "1234"}}'::jsonb,
  'pago del pack de X: mismos datos del pagador quitados');
select is((select string_agg(provider_payment_id, ',' order by provider_payment_id) from public.payments  -- 4
            where id in ('00000000-0000-0000-0000-000000000501', '00000000-0000-0000-0000-000000000502')),
  'mp-x-1,mp-x-2', 'provider_payment_id se conserva (permite consultar el pago en Mercado Pago)');
select ok((select webhook_payload ? 'payer' from public.payments where id = '00000000-0000-0000-0000-000000000503'),  -- 5
  'el payload del pago de otro cliente (Y) queda intacto');
select ok((select webhook_payload ? 'payer' from public.payments where id = '00000000-0000-0000-0000-000000000504'),  -- 6
  'el payload del pago de otro tenant queda intacto');

select is((select count(*) from public.bookings  -- 7
            where id in ('00000000-0000-0000-0000-000000000301', '00000000-0000-0000-0000-000000000302')
              and status = 'cancelled' and pending_expires_at is null), 2::bigint,
  'los bookings pending futuros de X pasan a cancelled (liberan el horario)');
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-0000000000d1'),  -- 8
  0, 'la sesión del pack la devolvió el trigger de bookings, exactamente una vez');
select is((select count(*) from public.bookings  -- 9
            where id in ('00000000-0000-0000-0000-000000000303', '00000000-0000-0000-0000-000000000307',
                         '00000000-0000-0000-0000-000000000309') and status = 'confirmed'), 3::bigint,
  'los bookings confirmed de X quedan intactos');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000304'),  -- 10
  'pending', 'un pending ya empezado no es "futuro": lo resuelve el job de vencimiento');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000305'),  -- 11
  'pending', 'el booking pending de otro cliente (Y) queda intacto');
select ok((select metadata -> 'fields_anonymized' from public.audit_log  -- 12
            where action = 'anonymize_end_customer' and entity_id = '00000000-0000-0000-0000-0000000000c1')
  @> '["phone", "name", "extra_data", "channel_user_id", "payments.payer", "notifications_log.last_error"]'::jsonb,
  'audit_log deja constancia de payments.payer entre los campos anonimizados');
select is((select metadata::text ~ '(example\.com|Nombre|Titular|Cliente X|5491100)' from public.audit_log  -- 13
            where action = 'anonymize_end_customer' and entity_id = '00000000-0000-0000-0000-0000000000c1'),
  false, 'audit_log no retiene ningún dato del pagador ni del cliente');

select lives_ok(  -- 14
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'staff', null)$$,
  'repetir la anonimización no falla');
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-0000000000d1'),  -- 15
  0, 'repetirla no devuelve otra sesión (no hay nada más que cancelar)');
select throws_ok(  -- 16
  $$select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c4', 'staff', null)$$,
  '42501', null, 'seguir rechazando un end_customer de otro tenant (el contexto es A)');
select is((select count(*) from public.bookings where tenant_id = '00000000-0000-0000-0000-0000000000a2'), 0::bigint,  -- 17
  'sin efectos colaterales sobre el tenant B');

-- ---------------------------------------------------------------------------
-- calendar_scrub_targets_core: los dos rechazos y los resultados
-- ---------------------------------------------------------------------------
select is((select string_agg(t.google_calendar_event_id, ',' order by t.google_calendar_event_id)  -- 18
             from public.calendar_scrub_targets_core(
               '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'staff') t),
  'evt-x-1,evt-x-2,evt-x-3', 'con end_customer_id devuelve todos los eventos de ese cliente (pasados y futuros)');
select is((select string_agg(t.integration_id::text, ',' order by t.google_calendar_event_id)  -- 19
             from public.calendar_scrub_targets_core(
               '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'staff') t),
  '00000000-0000-0000-0000-0000000000f1,00000000-0000-0000-0000-0000000000f1',
  'cada evento viene con la integración de google_calendar de su profesional');
select is((select count(*) from public.calendar_scrub_targets_core(  -- 20
              '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'staff') t
            where t.integration_id is null and t.google_calendar_event_id = 'evt-x-3'), 1::bigint,
  'sin integración del profesional el par se devuelve con integration_id null (el caller registra calendar_scrub_failed)');
select is((select string_agg(t.google_calendar_event_id, ',' order by t.google_calendar_event_id)  -- 21
             from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a1', null, 'bot') t),
  'evt-x-1,evt-x-2,evt-x-3,evt-y-1', 'sin end_customer_id devuelve todos los del tenant');
select is((select count(*) from public.calendar_scrub_targets_core(  -- 22
              '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c4', 'staff')), 0::bigint,
  'un end_customer de otro tenant no devuelve nada');

-- rechazo 1: tenant ajeno al contexto (bot/staff)
select throws_ok(  -- 23
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a2', null, 'bot')$$,
  '42501', null, 'bot con contexto A pidiendo el tenant B: rechazado (tenant ajeno)');
select throws_ok(  -- 24
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a2', null, 'staff')$$,
  '42501', null, 'staff con contexto A pidiendo el tenant B: rechazado (tenant ajeno)');
select set_config('app.current_tenant_id', '', true);
select throws_ok(  -- 25
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a1', null, 'staff')$$,
  '42501', null, 'sin contexto de tenant (vacío) también se rechaza');
select set_config('app.current_tenant_id', '00000000-0000-0000-0000-0000000000a2', true);
select is((select count(*) from public.calendar_scrub_targets_core(  -- 26
              '00000000-0000-0000-0000-0000000000a1', null, 'platform_admin')), 4::bigint,
  'platform_admin cruza tenants (como anonymize_end_customer)');

-- rechazo 2: purge_job ('system') solo sobre tenants inactive con el período vencido
select throws_ok(  -- 27
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a1', null, 'system')$$,
  '23514', null, 'purge_job sobre un tenant active: rechazado');
select throws_ok(  -- 28
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a4', null, 'system')$$,
  '23514', null, 'purge_job sobre un tenant inactive con el período sin vencer: rechazado');
select throws_ok(  -- 29
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-00000000dead', null, 'system')$$,
  '23514', null, 'purge_job sobre un tenant inexistente: rechazado');
select is((select string_agg(t.google_calendar_event_id, ',') from public.calendar_scrub_targets_core(  -- 30
              '00000000-0000-0000-0000-0000000000a3', null, 'system') t),
  'evt-z-1', 'purge_job sobre un tenant vencido devuelve los eventos de todo el tenant');
select throws_ok(  -- 31
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a1', null, 'scheduler')$$,
  '42501', null, 'un actor_type no autorizado se rechaza');
select throws_ok(  -- 32
  $$select * from public.calendar_scrub_targets_core('00000000-0000-0000-0000-0000000000a1', null, null)$$,
  '42501', null, 'un actor_type nulo se rechaza');

-- función pública: el actor sale de session_user; el rol de migraciones no está autorizado
select throws_ok(  -- 33
  $$select * from public.calendar_scrub_targets('00000000-0000-0000-0000-0000000000a1', null)$$,
  '42501', null, 'la función pública rechaza un session_user que no es de aplicación');

-- ---------------------------------------------------------------------------
-- purge_tenant_data: ramas de dominio y professionals.name
-- ---------------------------------------------------------------------------
select lives_ok(  -- 34
  $$select * from public.purge_tenant_data('00000000-0000-0000-0000-0000000000a3')$$,
  'purge_tenant_data con las tablas de dominio presentes');
select is((select count(*) from public.professionals  -- 35
            where tenant_id = '00000000-0000-0000-0000-0000000000a3' and name = 'Profesional anonimizado'), 2::bigint,
  'anonimiza el nombre de los professionals del tenant (dato personal del personal)');
select is((select string_agg(name, ',' order by name) from public.professionals  -- 36
            where tenant_id = '00000000-0000-0000-0000-0000000000a1'),
  'Prof A1,Prof A2 sin calendar', 'no toca los professionals de otros tenants');
select is((select webhook_payload from public.payments where id = '00000000-0000-0000-0000-000000000504'),  -- 37
  '{"id": 4}'::jsonb, 'la purga también limpia el payer de los pagos de sus clientes (rama de dominio de la core)');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000312'),  -- 38
  'cancelled', 'la purga cancela los bookings pending futuros (rama de dominio de la core)');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000000311'),  -- 39
  'confirmed', 'la purga deja los confirmed');
select is((select string_agg(distinct metadata ->> 'step', ',' order by metadata ->> 'step')  -- 40
             from public.audit_log where action = 'tenant_purge_step'),
  'anonymize_end_customers,begin_tenant_purge,collect_auth_users,delete_bot_sessions,delete_vault_secrets',
  'los pasos auditados son los de la cascada mas begin_tenant_purge (inactive -> purging)');
select is((select (metadata ->> 'professionals_anonymized')::integer from public.audit_log  -- 41
            where action = 'tenant_purge_step' and metadata ->> 'step' = 'anonymize_end_customers'),
  2, 'el paso de anonimización deja constancia de cuántos professionals anonimizó');
select lives_ok(  -- 42
  $$select * from public.purge_tenant_data('00000000-0000-0000-0000-0000000000a3')$$,
  'purge_tenant_data es idempotente con las ramas de dominio');
select is((select count(*) from public.audit_log where action = 'tenant_purge_step'), 9::bigint,  -- 43
  'el reintento deja sus 4 pasos y ningún paso extra (begin_tenant_purge + 4, luego 4)');

-- ---------------------------------------------------------------------------
-- EXECUTE: solo bot_service, dashboard_service, platform_admin y purge_job
-- ---------------------------------------------------------------------------
select is((select string_agg(r, ',' order by r)  -- 44
             from unnest(array['anon', 'authenticated', 'service_role', 'bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job', 'scheduler_job']) r
            where has_function_privilege(r, 'public.calendar_scrub_targets(uuid, uuid)', 'execute')),
  'bot_service,dashboard_service,platform_admin,purge_job', 'calendar_scrub_targets: EXECUTE solo para los 4 roles de §3.2');
select is((select count(*) from pg_proc p  -- 45
             cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
            where p.pronamespace = 'public'::regnamespace
              and p.proname in ('calendar_scrub_targets', 'calendar_scrub_targets_core', 'anonymize_end_customer_core')
              and a.grantee = 0), 0::bigint, 'ninguna de estas funciones es ejecutable por PUBLIC');
select is((select string_agg(r, ',' order by r)  -- 46
             from unnest(array['anon', 'authenticated', 'service_role', 'bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job', 'scheduler_job']) r
            where has_function_privilege(r, 'public.calendar_scrub_targets_core(uuid, uuid, text)', 'execute')
               or has_function_privilege(r, 'public.anonymize_end_customer_core(uuid, text, uuid)', 'execute')),
  null, 'las funciones core no tienen EXECUTE para ningún rol de aplicación');

select * from finish();
rollback;
