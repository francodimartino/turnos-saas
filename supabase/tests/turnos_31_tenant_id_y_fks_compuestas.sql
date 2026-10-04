-- Tablas de turnos: tenant_id forzado por trigger desde el padre y FKs compuestas hacia todos
-- los padres (nota de RLS de §7). Una referencia cruzada entre tenants es físicamente imposible.
begin;
select plan(27);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'tfk-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'tfk-b');
insert into public.tenant_users (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000000f1', '00000000-0000-0000-0000-0000000000a1'),
  ('00000000-0000-0000-0000-0000000000f2', '00000000-0000-0000-0000-0000000000a2');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof A'),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a2', 'Prof B');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Servicio A', 30, 100),
  ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000a2', 'Servicio B', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u2');

-- ---------------------------------------------------------------------------
-- El trigger corrige un tenant_id mentiroso (tenant B) en cada tabla hija de datos de A
-- ---------------------------------------------------------------------------
insert into public.availability_rules (id, tenant_id, professional_id, day_of_week, start_time, end_time)
values ('00000000-0000-0000-0000-000000000a01', '00000000-0000-0000-0000-0000000000a2',
        '00000000-0000-0000-0000-0000000000b1', 2, '09:00', '12:00');
select is(
  (select tenant_id from public.availability_rules where id = '00000000-0000-0000-0000-000000000a01'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'availability_rules: un INSERT con tenant_id ajeno queda corregido al del profesional');

insert into public.availability_blocks (id, tenant_id, professional_id, time_range)
values ('00000000-0000-0000-0000-000000000a02', '00000000-0000-0000-0000-0000000000a2',
        '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-02-01 00:00+00', '2030-02-02 00:00+00'));
select is(
  (select tenant_id from public.availability_blocks where id = '00000000-0000-0000-0000-000000000a02'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'availability_blocks: un INSERT con tenant_id ajeno queda corregido al del profesional');

insert into public.professional_services (tenant_id, professional_id, service_id)
values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b1',
        '00000000-0000-0000-0000-0000000000e1');
select is(
  (select tenant_id from public.professional_services
    where professional_id = '00000000-0000-0000-0000-0000000000b1'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'professional_services: un INSERT con tenant_id ajeno queda corregido al del profesional');

insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status)
values ('00000000-0000-0000-0000-000000000a03', '00000000-0000-0000-0000-0000000000a2',
        '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
        '00000000-0000-0000-0000-0000000000b1',
        tstzrange('2030-01-01 10:00+00', '2030-01-01 11:00+00'), 'confirmed');
select is(
  (select tenant_id from public.bookings where id = '00000000-0000-0000-0000-000000000a03'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'bookings: un INSERT con tenant_id ajeno queda corregido al del profesional');

insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, origin)
values ('00000000-0000-0000-0000-000000000a04', '00000000-0000-0000-0000-0000000000a2',
        '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 3, 'manual_grant');
select is(
  (select tenant_id from public.booking_packs where id = '00000000-0000-0000-0000-000000000a04'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'booking_packs: un INSERT con tenant_id ajeno queda corregido al del end_customer');

-- UPDATE de tenant_id hacia otro tenant: también queda corregido
update public.availability_rules set tenant_id = '00000000-0000-0000-0000-0000000000a2'
 where id = '00000000-0000-0000-0000-000000000a01';
update public.availability_blocks set tenant_id = '00000000-0000-0000-0000-0000000000a2'
 where id = '00000000-0000-0000-0000-000000000a02';
update public.professional_services set tenant_id = '00000000-0000-0000-0000-0000000000a2'
 where professional_id = '00000000-0000-0000-0000-0000000000b1';
update public.bookings set tenant_id = '00000000-0000-0000-0000-0000000000a2'
 where id = '00000000-0000-0000-0000-000000000a03';
update public.booking_packs set tenant_id = '00000000-0000-0000-0000-0000000000a2'
 where id = '00000000-0000-0000-0000-000000000a04';
select is(
  (select count(*) from (
     select tenant_id from public.availability_rules where id = '00000000-0000-0000-0000-000000000a01'
     union all select tenant_id from public.availability_blocks where id = '00000000-0000-0000-0000-000000000a02'
     union all select tenant_id from public.professional_services
       where professional_id = '00000000-0000-0000-0000-0000000000b1'
     union all select tenant_id from public.bookings where id = '00000000-0000-0000-0000-000000000a03'
     union all select tenant_id from public.booking_packs where id = '00000000-0000-0000-0000-000000000a04'
   ) t where tenant_id = '00000000-0000-0000-0000-0000000000a1'),
  5::bigint, 'un UPDATE de tenant_id queda corregido en las 5 tablas hijas');

-- bloqueo de todo el local (sin profesional): no hay padre del que copiar, se respeta el valor
insert into public.availability_blocks (id, tenant_id, professional_id, time_range)
values ('00000000-0000-0000-0000-000000000a05', '00000000-0000-0000-0000-0000000000a2', null,
        tstzrange('2030-05-01 00:00+00', '2030-05-02 00:00+00'));
select is(
  (select tenant_id from public.availability_blocks where id = '00000000-0000-0000-0000-000000000a05'),
  '00000000-0000-0000-0000-0000000000a2'::uuid,
  'availability_blocks sin profesional conserva el tenant_id informado (lo valida RLS)');

-- padre inexistente
select throws_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000f0', 1, '09:00', '12:00')$$,
  '23503', null, 'availability_rules con profesional inexistente es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000f0',
            '00000000-0000-0000-0000-0000000000e1', 3, 'manual_grant')$$,
  '23503', null, 'booking_packs con end_customer inexistente es rechazado');

-- ---------------------------------------------------------------------------
-- Referencias cruzadas entre tenants (triggers activos: el tenant_id queda en el del padre
-- que copia y la FK compuesta rechaza al otro padre)
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-04-01 10:00+00', '2030-04-01 11:00+00'), 'confirmed')$$,
  '23503', null, 'un booking de A hacia un servicio de B es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-04-01 10:00+00', '2030-04-01 11:00+00'), 'confirmed')$$,
  '23503', null, 'un booking de A hacia un end_customer de B es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3',
            tstzrange('2030-04-01 10:00+00', '2030-04-01 11:00+00'), 'confirmed')$$,
  '23503', null, 'un booking de A hacia un profesional de B es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2',
            '00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000b3',
            tstzrange('2030-04-01 10:00+00', '2030-04-01 11:00+00'), 'confirmed',
            '00000000-0000-0000-0000-000000000a04')$$,
  '23503', null, 'un booking de B hacia un pack de otro tenant es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e2', 3, 'manual_grant')$$,
  '23503', null, 'un pack de A hacia un servicio de B es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2',
            '00000000-0000-0000-0000-0000000000e1', 3, 'manual_grant')$$,
  '23503', null, 'un pack de A hacia un end_customer de B es rechazado');
select throws_ok(
  $$insert into public.professional_services (tenant_id, professional_id, service_id)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
            '00000000-0000-0000-0000-0000000000e2')$$,
  '23503', null, 'professional_services de un profesional de A hacia un servicio de B es rechazado');
select throws_ok(
  $$insert into public.professionals (tenant_id, name, tenant_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'Prof X', '00000000-0000-0000-0000-0000000000f2')$$,
  '23503', null, 'un profesional de A vinculado a un tenant_user de B es rechazado');
select lives_ok(
  $$insert into public.professionals (tenant_id, name, tenant_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'Prof Y', '00000000-0000-0000-0000-0000000000f1')$$,
  'un profesional vinculado a un tenant_user de su propio tenant es válido');
select throws_ok(
  $$insert into public.professionals (tenant_id, name, tenant_user_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'Prof Z', '00000000-0000-0000-0000-0000000000f1')$$,
  '23505', null, 'professionals.tenant_user_id es único');

-- tenant_integrations.professional_id: FK compuesta pendiente de la migración de plataforma
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, professional_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'google_calendar', '00000000-0000-0000-0000-0000000000b3')$$,
  '23503', null, 'una integración de A hacia un profesional de B es rechazada');
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, professional_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'google_calendar', '00000000-0000-0000-0000-0000000000f0')$$,
  '23503', null, 'una integración hacia un profesional inexistente es rechazada');
select lives_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, professional_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'google_calendar', '00000000-0000-0000-0000-0000000000b1')$$,
  'una integración hacia un profesional de su propio tenant es válida');
select lives_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type)
    values ('00000000-0000-0000-0000-0000000000a2', 'mercadopago')$$,
  'una integración sin profesional (null) no dispara la FK');

-- Con los triggers de usuario apagados la FK compuesta es la única defensa
alter table public.availability_rules disable trigger user;
alter table public.availability_blocks disable trigger user;
select throws_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b1', 1, '09:00', '12:00')$$,
  '23503', null, 'sin trigger: availability_rules con tenant_id ajeno lo rechaza la FK compuesta');
select throws_ok(
  $$insert into public.availability_blocks (tenant_id, professional_id, time_range)
    values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-02-01 00:00+00', '2030-02-02 00:00+00'))$$,
  '23503', null, 'sin trigger: availability_blocks con tenant_id ajeno lo rechaza la FK compuesta');
alter table public.availability_rules enable trigger user;
alter table public.availability_blocks enable trigger user;

-- ---------------------------------------------------------------------------
-- Estructura: unique (tenant_id, id) y FKs compuestas
-- ---------------------------------------------------------------------------
select is(
  (select count(*) from pg_constraint c
    where c.contype = 'u' and c.conrelid in (
      'public.professionals'::regclass, 'public.availability_rules'::regclass,
      'public.availability_blocks'::regclass, 'public.services'::regclass,
      'public.bookings'::regclass, 'public.booking_packs'::regclass)
      and (select array_agg(a.attname::text order by a.attname::text)
             from pg_attribute a where a.attrelid = c.conrelid and a.attnum = any (c.conkey))
          = array['id', 'tenant_id']),
  6::bigint, 'las 6 tablas con id propio tienen unique (tenant_id, id)');
select is(
  (select count(*) from pg_constraint c
    where c.contype = 'f'
      and c.conrelid in (
        'public.professionals'::regclass, 'public.availability_rules'::regclass,
        'public.availability_blocks'::regclass, 'public.professional_services'::regclass,
        'public.services'::regclass, 'public.bookings'::regclass,
        'public.booking_packs'::regclass, 'public.tenant_integrations'::regclass)
      and c.confrelid <> 'public.tenants'::regclass
      and array_length(c.conkey, 1) <> 2),
  0::bigint, 'ninguna FK hacia una tabla del tenant es simple: todas son compuestas');

select * from finish();
rollback;
