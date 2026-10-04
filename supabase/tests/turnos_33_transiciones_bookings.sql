-- bookings: transiciones válidas de estado. cancelled y completed son estados FINALES; solo
-- pending->confirmed, pending->cancelled, confirmed->completed y confirmed->cancelled (o mantener
-- el mismo estado) pasan el trigger BEFORE UPDATE. La sesión del pack se devuelve una sola vez.
begin;
select plan(26);

insert into public.tenants (id, name, slug) values ('00000000-0000-0000-0000-0000000000a1', 'A', 'trans-a');
insert into public.professionals (id, tenant_id, name) values ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof 1');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1');

insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at) values
  ('00000000-0000-0000-0000-000000000301', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-01 10:00+00', '2030-09-01 10:30+00'), 'pending', '2030-09-01 08:00+00'),
  ('00000000-0000-0000-0000-000000000302', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-02 10:00+00', '2030-09-02 10:30+00'), 'pending', '2030-09-02 08:00+00'),
  ('00000000-0000-0000-0000-000000000303', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-03 10:00+00', '2030-09-03 10:30+00'), 'confirmed', null),
  ('00000000-0000-0000-0000-000000000304', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-04 10:00+00', '2030-09-04 10:30+00'), 'confirmed', null),
  ('00000000-0000-0000-0000-000000000305', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-05 10:00+00', '2030-09-05 10:30+00'), 'pending', '2030-09-05 08:00+00'),
  ('00000000-0000-0000-0000-000000000306', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-06 10:00+00', '2030-09-06 10:30+00'), 'confirmed', null),
  ('00000000-0000-0000-0000-000000000307', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-07 10:00+00', '2030-09-07 10:30+00'), 'cancelled', null),
  ('00000000-0000-0000-0000-000000000308', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-08 10:00+00', '2030-09-08 10:30+00'), 'completed', null),
  ('00000000-0000-0000-0000-000000000309', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-09 10:00+00', '2030-09-09 10:30+00'), 'cancelled', null),
  ('00000000-0000-0000-0000-000000000310', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-10 10:00+00', '2030-09-10 10:30+00'), 'cancelled', null),
  ('00000000-0000-0000-0000-000000000311', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-11 10:00+00', '2030-09-11 10:30+00'), 'cancelled', null),
  ('00000000-0000-0000-0000-000000000312', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-12 10:00+00', '2030-09-12 10:30+00'), 'completed', null),
  ('00000000-0000-0000-0000-000000000313', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-13 10:00+00', '2030-09-13 10:30+00'), 'completed', null),
  ('00000000-0000-0000-0000-000000000314', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-14 10:00+00', '2030-09-14 10:30+00'), 'completed', null),
  ('00000000-0000-0000-0000-000000000315', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-15 10:00+00', '2030-09-15 10:30+00'), 'pending', '2030-09-15 08:00+00'),
  ('00000000-0000-0000-0000-000000000316', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-16 10:00+00', '2030-09-16 10:30+00'), 'confirmed', null);

select lives_ok(
  $$update public.bookings set status = 'confirmed'
     where id = '00000000-0000-0000-0000-000000000301'$$,
  'pending -> confirmed es válida');
select lives_ok(
  $$update public.bookings set status = 'cancelled'
     where id = '00000000-0000-0000-0000-000000000302'$$,
  'pending -> cancelled es válida');
select lives_ok(
  $$update public.bookings set status = 'completed'
     where id = '00000000-0000-0000-0000-000000000303'$$,
  'confirmed -> completed es válida');
select lives_ok(
  $$update public.bookings set status = 'cancelled'
     where id = '00000000-0000-0000-0000-000000000304'$$,
  'confirmed -> cancelled es válida');
select lives_ok(
  $$update public.bookings set status = 'pending', pending_expires_at = '2030-09-05 08:00+00'
     where id = '00000000-0000-0000-0000-000000000305'$$,
  'pending -> pending es válida');
select lives_ok(
  $$update public.bookings set status = 'confirmed'
     where id = '00000000-0000-0000-0000-000000000306'$$,
  'confirmed -> confirmed es válida');
select lives_ok(
  $$update public.bookings set status = 'cancelled'
     where id = '00000000-0000-0000-0000-000000000307'$$,
  'cancelled -> cancelled es válida');
select lives_ok(
  $$update public.bookings set status = 'completed'
     where id = '00000000-0000-0000-0000-000000000308'$$,
  'completed -> completed es válida');
select throws_ok(
  $$update public.bookings set status = 'confirmed'
     where id = '00000000-0000-0000-0000-000000000309'$$,
  '23514', null, 'cancelled -> confirmed es rechazada');
select throws_ok(
  $$update public.bookings set status = 'pending', pending_expires_at = '2030-09-10 08:00+00'
     where id = '00000000-0000-0000-0000-000000000310'$$,
  '23514', null, 'cancelled -> pending es rechazada');
select throws_ok(
  $$update public.bookings set status = 'completed'
     where id = '00000000-0000-0000-0000-000000000311'$$,
  '23514', null, 'cancelled -> completed es rechazada');
select throws_ok(
  $$update public.bookings set status = 'cancelled'
     where id = '00000000-0000-0000-0000-000000000312'$$,
  '23514', null, 'completed -> cancelled es rechazada');
select throws_ok(
  $$update public.bookings set status = 'confirmed'
     where id = '00000000-0000-0000-0000-000000000313'$$,
  '23514', null, 'completed -> confirmed es rechazada');
select throws_ok(
  $$update public.bookings set status = 'pending', pending_expires_at = '2030-09-14 08:00+00'
     where id = '00000000-0000-0000-0000-000000000314'$$,
  '23514', null, 'completed -> pending es rechazada');
select throws_ok(
  $$update public.bookings set status = 'completed'
     where id = '00000000-0000-0000-0000-000000000315'$$,
  '23514', null, 'pending -> completed es rechazada');
select throws_ok(
  $$update public.bookings set status = 'pending', pending_expires_at = '2030-09-16 08:00+00'
     where id = '00000000-0000-0000-0000-000000000316'$$,
  '23514', null, 'confirmed -> pending es rechazada');

-- al pasar a confirmed/cancelled pending_expires_at queda null aun sin tocarlo
select is(
  (select count(*) from public.bookings
    where id in ('00000000-0000-0000-0000-000000000301', '00000000-0000-0000-0000-000000000302') and pending_expires_at is not null),
  0::bigint, 'pending -> confirmed/cancelled deja pending_expires_at en null');
select is(
  (select status from public.bookings where id = '00000000-0000-0000-0000-000000000309'),
  'cancelled', 'el intento rechazado no cambia el estado (sigue cancelled)');

-- un UPDATE de varias filas con una transición inválida se rechaza entero
select throws_ok(
  $$update public.bookings set status = 'confirmed' where id in ('00000000-0000-0000-0000-000000000309', '00000000-0000-0000-0000-000000000312')$$,
  '23514', null, 'un UPDATE de varias filas con una transición inválida falla');

-- sesión del pack: se devuelve una sola vez
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-000000000a31', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 3, 'manual_grant');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id) values
  ('00000000-0000-0000-0000-000000000331', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-01 10:00+00', '2030-10-01 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a31'),
  ('00000000-0000-0000-0000-000000000332', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-10-02 10:00+00', '2030-10-02 10:30+00'), 'confirmed', '00000000-0000-0000-0000-000000000a31');
update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-000000000331';
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a31'),
  2, 'cancelar devuelve la sesión (3 -> 2)');
select throws_ok(
  $$update public.bookings set status = 'confirmed' where id = '00000000-0000-0000-0000-000000000331'$$,
  '23514', null, 'no se puede reactivar el booking cancelado');
update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-000000000331';
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a31'),
  2, 'cancelar de nuevo (mismo estado) no devuelve la sesión por segunda vez');
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a31'),
  2, 'tras los intentos la sesión quedó devuelta una sola vez');

update public.bookings set status = 'completed' where id = '00000000-0000-0000-0000-000000000332';
select throws_ok(
  $$update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-000000000332'$$,
  '23514', null, 'completed con pack no puede pasar a cancelled');
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-000000000a31'),
  2, 'completed -> cancelled rechazada: la sesión no se devuelve');

select is(
  (select count(*) from pg_trigger
    where tgrelid = 'public.bookings'::regclass and tgname = 'bookings_transiciones_estado'
      and (tgtype & 2) = 2 and (tgtype & 16) <> 0 and (tgtype & 1) = 1),
  1::bigint, 'existe el trigger BEFORE UPDATE fila por fila de transiciones');

select * from finish();
rollback;
