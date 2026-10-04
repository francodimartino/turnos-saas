-- Tablas de turnos: triggers de negocio (§7.1) — devolución de la sesión del pack al cancelar,
-- canje de packs, seña obligatoria en modo 'deposit' y baja del secret de Google Calendar.
begin;
select plan(30);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'trg-a');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Con calendar'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1', 'Otro con calendar'),
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a1', 'Agenda'),
  ('00000000-0000-0000-0000-0000000000b4', '00000000-0000-0000-0000-0000000000a1', 'Sin integración'),
  ('00000000-0000-0000-0000-0000000000b5', '00000000-0000-0000-0000-0000000000a1', 'Unicidad 1'),
  ('00000000-0000-0000-0000-0000000000b6', '00000000-0000-0000-0000-0000000000a1', 'Unicidad 2');
insert into public.services (id, tenant_id, name, duration_minutes, price, deposit_amount) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100, null),
  ('00000000-0000-0000-0000-0000000000e3', '00000000-0000-0000-0000-0000000000a1', 'Color', 60, 500, 150);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1');

-- ---------------------------------------------------------------------------
-- Devolución de la sesión del pack: UN solo trigger AFTER UPDATE en bookings, fila por fila
-- ---------------------------------------------------------------------------
select is(
  (select count(*) from pg_trigger
    where tgrelid = 'public.bookings'::regclass and not tgisinternal
      and (tgtype & 2) = 0      -- AFTER (no BEFORE)
      and (tgtype & 64) = 0     -- no INSTEAD OF
      and (tgtype & 16) <> 0),  -- UPDATE
  1::bigint, 'bookings tiene exactamente UN trigger AFTER UPDATE');
select is(
  (select count(*) from pg_trigger
    where tgrelid = 'public.bookings'::regclass and not tgisinternal
      and (tgtype & 2) = 0 and (tgtype & 64) = 0 and (tgtype & 16) <> 0
      and (tgtype & 1) = 1),    -- FOR EACH ROW
  1::bigint, 'ese trigger corre fila por fila');

insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-000000000a01', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 2, 'manual_grant'),
  ('00000000-0000-0000-0000-000000000a02', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 3, 'manual_grant'),
  ('00000000-0000-0000-0000-000000000a03', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 2, 'manual_grant'),
  ('00000000-0000-0000-0000-000000000a04', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 2, 'manual_grant'),
  ('00000000-0000-0000-0000-000000000a05', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 1, 'manual_grant'),
  ('00000000-0000-0000-0000-000000000a06', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 1, 'manual_grant');

insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id) values
  ('00000000-0000-0000-0000-000000000b01', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-01 10:00+00', '2030-06-01 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a01'),
  ('00000000-0000-0000-0000-000000000b02', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-02 10:00+00', '2030-06-02 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a02'),
  ('00000000-0000-0000-0000-000000000b03', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-03 10:00+00', '2030-06-03 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a02'),
  ('00000000-0000-0000-0000-000000000b04', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-04 10:00+00', '2030-06-04 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a02'),
  ('00000000-0000-0000-0000-000000000b05', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-05 10:00+00', '2030-06-05 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a03'),
  ('00000000-0000-0000-0000-000000000b06', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-06 10:00+00', '2030-06-06 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a03'),
  ('00000000-0000-0000-0000-000000000b07', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-07 10:00+00', '2030-06-07 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a04'),
  ('00000000-0000-0000-0000-000000000b08', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-08 10:00+00', '2030-06-08 10:30+00'), 'confirmed', null),
  ('00000000-0000-0000-0000-000000000b09', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-09 10:00+00', '2030-06-09 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a05'),
  ('00000000-0000-0000-0000-000000000b10', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3', tstzrange('2030-06-10 10:00+00', '2030-06-10 10:30+00'), 'cancelled', '00000000-0000-0000-0000-000000000a06');

-- un booking que pasa a cancelled devuelve exactamente una sesión
update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-000000000b01';
select is(
  (select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a01'),
  1, 'cancelar un booking devuelve una sesión (2 -> 1)');

-- varios bookings del mismo pack en un solo UPDATE: una sesión por cada uno
update public.bookings set status = 'cancelled'
 where id in ('00000000-0000-0000-0000-000000000b02', '00000000-0000-0000-0000-000000000b03',
              '00000000-0000-0000-0000-000000000b04');
select is(
  (select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a02'),
  0, 'tres bookings del mismo pack cancelados en un UPDATE devuelven tres sesiones (3 -> 0)');

-- packs distintos y un booking sin pack en el mismo UPDATE
update public.bookings set status = 'cancelled'
 where id in ('00000000-0000-0000-0000-000000000b05', '00000000-0000-0000-0000-000000000b06',
              '00000000-0000-0000-0000-000000000b07', '00000000-0000-0000-0000-000000000b08');
select is(
  (select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a03'),
  0, 'pack con dos bookings cancelados en el mismo UPDATE (2 -> 0)');
select is(
  (select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a04'),
  1, 'otro pack en el mismo UPDATE devuelve solo lo suyo (2 -> 1)');

-- transiciones que no son "pasar a cancelled" no devuelven nada
update public.bookings set status = 'completed' where id = '00000000-0000-0000-0000-000000000b09';
select is(
  (select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a05'),
  1, 'completar un booking no devuelve la sesión');
update public.bookings set google_calendar_event_id = 'evt-1' where id = '00000000-0000-0000-0000-000000000b10';
select is(
  (select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a06'),
  1, 'actualizar un booking ya cancelled no la devuelve por segunda vez');

-- ---------------------------------------------------------------------------
-- Canje de packs: un purchase en pending no puede redimirse
-- ---------------------------------------------------------------------------
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin, payment_status) values
  ('00000000-0000-0000-0000-000000000a07', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 0, 'purchase', 'pending'),
  ('00000000-0000-0000-0000-000000000a08', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 1, 'purchase', 'paid');

select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3',
            tstzrange('2030-07-01 10:00+00', '2030-07-01 10:30+00'), 'confirmed',
            '00000000-0000-0000-0000-000000000a07')$$,
  '23514', null, 'un booking no puede redimir un pack purchase en pending');
select lives_ok(
  $$insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id)
    values ('00000000-0000-0000-0000-000000000b11', '00000000-0000-0000-0000-0000000000a1',
            '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
            '00000000-0000-0000-0000-0000000000b3',
            tstzrange('2030-07-02 10:00+00', '2030-07-02 10:30+00'), 'confirmed',
            '00000000-0000-0000-0000-000000000a08')$$,
  'un pack purchase paid sí puede redimirse');
select lives_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b3',
            tstzrange('2030-07-03 10:00+00', '2030-07-03 10:30+00'), 'confirmed',
            '00000000-0000-0000-0000-000000000a01')$$,
  'un pack manual_grant sí puede redimirse');
select throws_ok(
  $$update public.bookings set booking_pack_id = '00000000-0000-0000-0000-000000000a07'
     where id = '00000000-0000-0000-0000-000000000b08'$$,
  '23514', null, 'tampoco se puede reasignar un booking a un pack purchase en pending');

-- el check solo aplica al asignar el pack: cancelar después de que el pack quedó pending devuelve la sesión
update public.booking_packs set payment_status = 'pending' where id = '00000000-0000-0000-0000-000000000a08';
update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-000000000b11';
select is(
  (select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a08'),
  0, 'cancelar un booking cuyo pack volvió a pending igual devuelve la sesión');

-- ---------------------------------------------------------------------------
-- professional_services en modo 'deposit' exige services.deposit_amount
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
            '00000000-0000-0000-0000-0000000000e1', 'deposit')$$,
  '23514', null, 'modo deposit sobre un servicio sin deposit_amount es rechazado');
select lives_ok(
  $$insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
            '00000000-0000-0000-0000-0000000000e3', 'deposit')$$,
  'modo deposit sobre un servicio con deposit_amount es válido');
insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode)
values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b2',
        '00000000-0000-0000-0000-0000000000e1', 'auto');
select throws_ok(
  $$update public.professional_services set confirmation_mode = 'deposit'
     where professional_id = '00000000-0000-0000-0000-0000000000b2'
       and service_id = '00000000-0000-0000-0000-0000000000e1'$$,
  '23514', null, 'pasar a deposit un servicio sin deposit_amount es rechazado');
select throws_ok(
  $$update public.services set deposit_amount = null
     where id = '00000000-0000-0000-0000-0000000000e3'$$,
  '23514', null, 'no se puede quitar deposit_amount mientras algún profesional usa deposit');
select lives_ok(
  $$update public.services set deposit_amount = 200
     where id = '00000000-0000-0000-0000-0000000000e3'$$,
  'cambiar el monto de la seña sí se puede');
select lives_ok(
  $$update public.services set deposit_amount = 50 where id = '00000000-0000-0000-0000-0000000000e1'$$,
  'agregar una seña a un servicio sin uso en deposit es válido');
select lives_ok(
  $$update public.services set deposit_amount = null where id = '00000000-0000-0000-0000-0000000000e1'$$,
  'quitar la seña de un servicio que nadie usa en deposit es válido');

-- ---------------------------------------------------------------------------
-- Baja del profesional: se elimina de Vault el secret de su google_calendar
-- ---------------------------------------------------------------------------
insert into public.tenant_integrations (id, tenant_id, integration_type, professional_id) values
  ('00000000-0000-0000-0000-000000000c01', '00000000-0000-0000-0000-0000000000a1', 'google_calendar', '00000000-0000-0000-0000-0000000000b1'),
  ('00000000-0000-0000-0000-000000000c02', '00000000-0000-0000-0000-0000000000a1', 'google_calendar', '00000000-0000-0000-0000-0000000000b2'),
  ('00000000-0000-0000-0000-000000000c03', '00000000-0000-0000-0000-0000000000a1', 'google_calendar', null);
do $$
begin
  perform vault.create_secret('token-prof-1', 'turnos-test-prof-1');
  perform vault.create_secret('token-prof-2', 'turnos-test-prof-2');
  perform vault.create_secret('token-local', 'turnos-test-local');
end
$$;
update public.tenant_integrations set secret_id = (select id from vault.secrets where name = 'turnos-test-prof-1')
 where id = '00000000-0000-0000-0000-000000000c01';
update public.tenant_integrations set secret_id = (select id from vault.secrets where name = 'turnos-test-prof-2')
 where id = '00000000-0000-0000-0000-000000000c02';
update public.tenant_integrations set secret_id = (select id from vault.secrets where name = 'turnos-test-local')
 where id = '00000000-0000-0000-0000-000000000c03';

update public.professionals set name = 'Con calendar (renombrado)' where id = '00000000-0000-0000-0000-0000000000b1';
select is(
  (select count(*) from vault.secrets where name = 'turnos-test-prof-1'),
  1::bigint, 'renombrar al profesional no toca su secret');

update public.professionals set is_active = false where id = '00000000-0000-0000-0000-0000000000b1';
select is(
  (select count(*) from vault.secrets where name = 'turnos-test-prof-1'),
  0::bigint, 'al pasar is_active=false se elimina de Vault el secret de su google_calendar');
select is(
  (select secret_id from public.tenant_integrations where id = '00000000-0000-0000-0000-000000000c01'),
  null::uuid, 'la integración deja de apuntar al secret eliminado');
select is(
  (select count(*) from vault.secrets where name in ('turnos-test-prof-2', 'turnos-test-local')),
  2::bigint, 'los secrets de otro profesional y de la integración del local no se tocan');
select lives_ok(
  $$update public.professionals set is_active = false where id = '00000000-0000-0000-0000-0000000000b4'$$,
  'dar de baja a un profesional sin integración no falla');
select lives_ok(
  $$update public.professionals set is_active = true where id = '00000000-0000-0000-0000-0000000000b1'$$,
  'reactivar al profesional no falla (y no recrea el secret)');
select is(
  (select count(*) from vault.secrets where name = 'turnos-test-prof-1'),
  0::bigint, 'reactivar no recrea el secret');

-- ---------------------------------------------------------------------------
-- Unicidad de la integración por profesional (índices de plataforma + FK compuesta)
-- ---------------------------------------------------------------------------
insert into public.tenant_integrations (tenant_id, integration_type, professional_id)
values ('00000000-0000-0000-0000-0000000000a1', 'mercadopago', '00000000-0000-0000-0000-0000000000b5');
select throws_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, professional_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'mercadopago', '00000000-0000-0000-0000-0000000000b5')$$,
  '23505', null, 'no se repite (tenant, tipo, profesional)');
select lives_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type, professional_id)
    values ('00000000-0000-0000-0000-0000000000a1', 'mercadopago', '00000000-0000-0000-0000-0000000000b6')$$,
  'profesionales distintos pueden tener el mismo tipo de integración');
select lives_ok(
  $$insert into public.tenant_integrations (tenant_id, integration_type)
    values ('00000000-0000-0000-0000-0000000000a1', 'mercadopago')$$,
  'la integración de tenant y la por-profesional del mismo tipo conviven');

select * from finish();
rollback;
