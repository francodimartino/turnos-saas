-- Tablas de turnos (§7.1): checks, not null, enums y exclusion constraint contra el doble-booking.
begin;
select plan(56);

select is(
  (select count(*) from pg_tables
    where schemaname = 'public'
      and tablename in ('professionals', 'availability_rules', 'availability_blocks',
                        'professional_services', 'services', 'bookings', 'booking_packs')),
  7::bigint, 'existen las 7 tablas de turnos');

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'tur-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'tur-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof 1'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1', 'Prof 2');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1');

-- ---------------------------------------------------------------------------
-- availability_rules
-- ---------------------------------------------------------------------------
select lives_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', 1, '09:00', '18:00')$$,
  'una regla de disponibilidad válida se inserta');
select throws_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', 1, '10:00', '10:00')$$,
  '23514', null, 'end_time = start_time es rechazado');
select throws_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', 1, '18:00', '09:00')$$,
  '23514', null, 'end_time < start_time (ventana invertida) es rechazado');
select throws_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', 7, '09:00', '18:00')$$,
  '23514', null, 'day_of_week = 7 es rechazado');
select throws_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', -1, '09:00', '18:00')$$,
  '23514', null, 'day_of_week = -1 es rechazado');
select throws_ok(
  $$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', null, '09:00', '18:00')$$,
  '23502', null, 'day_of_week NULL es rechazado');

-- ---------------------------------------------------------------------------
-- availability_blocks
-- ---------------------------------------------------------------------------
select lives_ok(
  $$insert into public.availability_blocks (tenant_id, professional_id, time_range, reason)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-02-01 00:00+00', '2030-02-08 00:00+00'), 'vacaciones')$$,
  'un bloqueo de un profesional se inserta');
select lives_ok(
  $$insert into public.availability_blocks (tenant_id, professional_id, time_range)
    values ('00000000-0000-0000-0000-0000000000a1', null,
            tstzrange('2030-05-01 00:00+00', '2030-05-02 00:00+00'))$$,
  'professional_id NULL bloquea todo el local');
select throws_ok(
  $$insert into public.availability_blocks (tenant_id, professional_id, time_range)
    values ('00000000-0000-0000-0000-0000000000a1', null,
            tstzrange('2030-05-01 00:00+00', '2030-05-01 00:00+00'))$$,
  '23514', null, 'un time_range vacío es rechazado');
select throws_ok(
  $$insert into public.availability_blocks (tenant_id, professional_id, time_range)
    values ('00000000-0000-0000-0000-0000000000a1', null, null)$$,
  '23502', null, 'time_range NULL es rechazado');

-- ---------------------------------------------------------------------------
-- services
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.services (tenant_id, name, duration_minutes, price)
    values ('00000000-0000-0000-0000-0000000000a1', 'X', 0, 10)$$,
  '23514', null, 'duration_minutes = 0 es rechazado');
select throws_ok(
  $$insert into public.services (tenant_id, name, duration_minutes, price)
    values ('00000000-0000-0000-0000-0000000000a1', 'X', -15, 10)$$,
  '23514', null, 'duration_minutes negativo es rechazado');
select throws_ok(
  $$insert into public.services (tenant_id, name, duration_minutes, price)
    values ('00000000-0000-0000-0000-0000000000a1', 'X', 30, -1)$$,
  '23514', null, 'precio negativo es rechazado');
select throws_ok(
  $$insert into public.services (tenant_id, name, duration_minutes, price, deposit_amount)
    values ('00000000-0000-0000-0000-0000000000a1', 'X', 30, 10, -1)$$,
  '23514', null, 'deposit_amount negativo es rechazado');
select throws_ok(
  $$insert into public.services (tenant_id, name, duration_minutes, price)
    values ('00000000-0000-0000-0000-0000000000a1', 'X', null, 10)$$,
  '23502', null, 'duration_minutes NULL es rechazado');
select throws_ok(
  $$insert into public.services (tenant_id, name, duration_minutes, price)
    values ('00000000-0000-0000-0000-0000000000a1', 'X', 30, null)$$,
  '23502', null, 'price NULL es rechazado');
select lives_ok(
  $$insert into public.services (tenant_id, name, duration_minutes, price, deposit_amount)
    values ('00000000-0000-0000-0000-0000000000a1', 'Y', 30, 0, 0)$$,
  'precio 0 y deposit_amount 0 son válidos');

-- ---------------------------------------------------------------------------
-- professional_services
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
            '00000000-0000-0000-0000-0000000000e1', 'manual')$$,
  '23514', null, 'confirmation_mode fuera del enum es rechazado');
select throws_ok(
  $$insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
            '00000000-0000-0000-0000-0000000000e1', null)$$,
  '23502', null, 'confirmation_mode NULL es rechazado');
insert into public.professional_services (tenant_id, professional_id, service_id)
values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
        '00000000-0000-0000-0000-0000000000e1');
select is(
  (select confirmation_mode from public.professional_services
    where professional_id = '00000000-0000-0000-0000-0000000000b1'),
  'auto', 'confirmation_mode vale auto por defecto');
select throws_ok(
  $$insert into public.professional_services (tenant_id, professional_id, service_id)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
            '00000000-0000-0000-0000-0000000000e1')$$,
  '23505', null, 'la clave (professional_id, service_id) no se repite');

-- ---------------------------------------------------------------------------
-- bookings: enums, not null, check de pending y exclusion constraint
-- ---------------------------------------------------------------------------
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status)
values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1',
        '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
        '00000000-0000-0000-0000-0000000000b1',
        tstzrange('2030-01-01 10:00+00', '2030-01-01 11:00+00'), 'confirmed');

select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), 'rebotado')$$,
  '23514', null, 'status fuera del enum es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), null)$$,
  '23502', null, 'status NULL es rechazado (un NULL escaparía al exclude)');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, deposit_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), 'confirmed', 'debe')$$,
  '23514', null, 'deposit_status fuera del enum es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, deposit_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), 'confirmed', null)$$,
  '23502', null, 'deposit_status NULL es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', null,
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), 'confirmed')$$,
  '23502', null, 'professional_id NULL es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            null, 'confirmed')$$,
  '23502', null, 'time_range NULL es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 10:00+00'), 'confirmed')$$,
  '23514', null, 'un time_range vacío en un booking es rechazado');

-- (status = 'pending') = (pending_expires_at is not null)
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), 'pending')$$,
  '23514', null, 'pending sin pending_expires_at es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), 'confirmed', '2030-02-28 10:00+00')$$,
  '23514', null, 'confirmed con pending_expires_at es rechazado');
select lives_ok(
  $$insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, pending_expires_at)
    values ('00000000-0000-0000-0000-0000000000d2', '00000000-0000-0000-0000-0000000000a1',
            '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
            '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-03-01 10:00+00', '2030-03-01 11:00+00'), '2030-02-28 10:00+00')$$,
  'un booking sin status explícito nace pending (con pending_expires_at)');
select is(
  (select status || '/' || deposit_status from public.bookings
    where id = '00000000-0000-0000-0000-0000000000d2'),
  'pending/not_required', 'defaults: status pending y deposit_status not_required');
select throws_ok(
  $$update public.bookings set pending_expires_at = null
     where id = '00000000-0000-0000-0000-0000000000d2'$$,
  '23514', null, 'un pending no puede perder pending_expires_at');
select lives_ok(
  $$update public.bookings set status = 'confirmed', pending_expires_at = null
     where id = '00000000-0000-0000-0000-0000000000d2'$$,
  'al confirmar se limpia pending_expires_at en el mismo UPDATE');

-- exclusion constraint: doble-booking del mismo profesional
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-01-01 10:30+00', '2030-01-01 11:30+00'), 'confirmed')$$,
  '23P01', null, 'doble-booking del mismo profesional (superposición parcial) es rechazado');
select throws_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-01-01 10:00+00', '2030-01-01 11:00+00'), 'pending', '2030-01-01 09:00+00')$$,
  '23P01', null, 'un pending también reserva el horario');
select lives_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b2',
            tstzrange('2030-01-01 10:00+00', '2030-01-01 11:00+00'), 'confirmed')$$,
  'el mismo horario con otro profesional es válido');
select lives_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-01-01 11:00+00', '2030-01-01 12:00+00'), 'confirmed')$$,
  'turnos contiguos (el límite superior es exclusivo) son válidos');
select lives_ok(
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-01-01 10:00+00', '2030-01-01 11:00+00'), 'cancelled')$$,
  'un booking cancelled no choca con uno vigente');

update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-0000000000d1';
select lives_ok(
  $$insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000000d3', '00000000-0000-0000-0000-0000000000a1',
            '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
            '00000000-0000-0000-0000-0000000000b1',
            tstzrange('2030-01-01 10:00+00', '2030-01-01 11:00+00'), 'confirmed')$$,
  'el horario de un booking cancelled queda libre para otro');
select throws_ok(
  $$update public.bookings set status = 'confirmed'
     where id = '00000000-0000-0000-0000-0000000000d1'$$,
  '23514', null, 'reactivar un cancelled es rechazado (estado final), aunque el horario esté tomado');
select throws_ok(
  $$update public.bookings set time_range = tstzrange('2030-01-01 10:30+00', '2030-01-01 11:30+00')
     where id = '00000000-0000-0000-0000-0000000000d3'$$,
  '23P01', null, 'mover un booking sobre otro del mismo profesional es rechazado');

-- ---------------------------------------------------------------------------
-- booking_packs
-- ---------------------------------------------------------------------------
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 6, 'manual_grant')$$,
  '23514', null, 'used_sessions > total_sessions es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 0, 'manual_grant')$$,
  '23514', null, 'total_sessions = 0 es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', null, 'manual_grant')$$,
  '23502', null, 'total_sessions NULL es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, -1, 'manual_grant')$$,
  '23514', null, 'used_sessions negativo es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'regalo')$$,
  '23514', null, 'origin fuera del enum es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, null)$$,
  '23502', null, 'origin NULL es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin, payment_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'purchase', 'debe')$$,
  '23514', null, 'payment_status fuera del enum es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin, payment_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'purchase', null)$$,
  '23502', null, 'payment_status NULL es rechazado');
-- check bidireccional de origin/payment_status
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin, payment_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'manual_grant', 'pending')$$,
  '23514', null, 'manual_grant con pago pendiente es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin, payment_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'manual_grant', 'paid')$$,
  '23514', null, 'manual_grant marcado como pagado es rechazado');
select throws_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin, payment_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'purchase', 'not_required')$$,
  '23514', null, 'purchase marcado como no-requiere-pago es rechazado');
select lives_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'manual_grant')$$,
  'manual_grant con los defaults (not_required, used_sessions 0) es válido');
select lives_ok(
  $$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin, payment_status)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'purchase', 'pending'),
           ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
            '00000000-0000-0000-0000-0000000000e1', 5, 'purchase', 'paid')$$,
  'purchase pending y purchase paid son válidos');

select * from finish();
rollback;
