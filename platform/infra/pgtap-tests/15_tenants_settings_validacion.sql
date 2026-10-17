-- Validación de tenants.settings: booking_approval_timeout_hours (entero 1-168, §2.10/§7.1) y los límites de reserva
-- booking_max_active_per_customer (1-50), booking_max_per_hour (1-60) y booking_min_notice_minutes (0-1440).
begin;
select plan(43);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'settings-a');

select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-sin', '{"direccion": "Calle 1"}')$$,
  'sin la clave, cualquier settings es válido');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-1', '{"booking_approval_timeout_hours": 1}')$$,
  '1 es válido (límite inferior)');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-168', '{"booking_approval_timeout_hours": 168}')$$,
  '168 es válido (límite superior)');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-24', '{"booking_approval_timeout_hours": 24}')$$,
  '24 es válido');

select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-0', '{"booking_approval_timeout_hours": 0}')$$,
  '23514', null, '0 es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-169', '{"booking_approval_timeout_hours": 169}')$$,
  '23514', null, '169 es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-neg', '{"booking_approval_timeout_hours": -5}')$$,
  '23514', null, 'un negativo es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-dec', '{"booking_approval_timeout_hours": 24.5}')$$,
  '23514', null, 'un decimal es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-str', '{"booking_approval_timeout_hours": "24"}')$$,
  '23514', null, 'un string es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-null', '{"booking_approval_timeout_hours": null}')$$,
  '23514', null, 'un null JSON es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 's-bool', '{"booking_approval_timeout_hours": true}')$$,
  '23514', null, 'un booleano es rechazado');

select lives_ok(
  $$update public.tenants set settings = '{"booking_approval_timeout_hours": 48}'
    where id = '00000000-0000-0000-0000-0000000000a1'$$,
  'un UPDATE con un valor válido es aceptado');
select throws_ok(
  $$update public.tenants set settings = '{"booking_approval_timeout_hours": 500}'
    where id = '00000000-0000-0000-0000-0000000000a1'$$,
  '23514', null, 'un UPDATE con un valor inválido es rechazado');
select is(
  (select settings -> 'booking_approval_timeout_hours' from public.tenants
    where id = '00000000-0000-0000-0000-0000000000a1'),
  '48'::jsonb, 'tras el rechazo queda el último valor válido');

select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l1', '{"booking_max_active_per_customer": 1}')$$,
  'booking_max_active_per_customer: el mínimo es válido');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l2', '{"booking_max_active_per_customer": 50}')$$,
  'booking_max_active_per_customer: el máximo es válido');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l3', '{"booking_max_active_per_customer": 0}')$$,
  '23514', null, 'booking_max_active_per_customer: bajo el mínimo es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l4', '{"booking_max_active_per_customer": 51}')$$,
  '23514', null, 'booking_max_active_per_customer: sobre el máximo es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l5', '{"booking_max_active_per_customer": 1.5}')$$,
  '23514', null, 'booking_max_active_per_customer: un decimal es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l6', '{"booking_max_active_per_customer": "5"}')$$,
  '23514', null, 'booking_max_active_per_customer: un string es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l7', '{"booking_max_active_per_customer": null}')$$,
  '23514', null, 'booking_max_active_per_customer: un null JSON es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l8', '{"booking_max_active_per_customer": true}')$$,
  '23514', null, 'booking_max_active_per_customer: un booleano es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l9', '{"booking_max_active_per_customer": -3}')$$,
  '23514', null, 'booking_max_active_per_customer: un negativo es rechazado');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l10', '{"booking_max_per_hour": 1}')$$,
  'booking_max_per_hour: el mínimo es válido');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l11', '{"booking_max_per_hour": 60}')$$,
  'booking_max_per_hour: el máximo es válido');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l12', '{"booking_max_per_hour": 0}')$$,
  '23514', null, 'booking_max_per_hour: bajo el mínimo es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l13', '{"booking_max_per_hour": 61}')$$,
  '23514', null, 'booking_max_per_hour: sobre el máximo es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l14', '{"booking_max_per_hour": 1.5}')$$,
  '23514', null, 'booking_max_per_hour: un decimal es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l15', '{"booking_max_per_hour": "5"}')$$,
  '23514', null, 'booking_max_per_hour: un string es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l16', '{"booking_max_per_hour": null}')$$,
  '23514', null, 'booking_max_per_hour: un null JSON es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l17', '{"booking_max_per_hour": true}')$$,
  '23514', null, 'booking_max_per_hour: un booleano es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l18', '{"booking_max_per_hour": -3}')$$,
  '23514', null, 'booking_max_per_hour: un negativo es rechazado');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l19', '{"booking_min_notice_minutes": 0}')$$,
  'booking_min_notice_minutes: el mínimo es válido');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l20', '{"booking_min_notice_minutes": 1440}')$$,
  'booking_min_notice_minutes: el máximo es válido');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l21', '{"booking_min_notice_minutes": -1}')$$,
  '23514', null, 'booking_min_notice_minutes: bajo el mínimo es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l22', '{"booking_min_notice_minutes": 1441}')$$,
  '23514', null, 'booking_min_notice_minutes: sobre el máximo es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l23', '{"booking_min_notice_minutes": 1.5}')$$,
  '23514', null, 'booking_min_notice_minutes: un decimal es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l24', '{"booking_min_notice_minutes": "5"}')$$,
  '23514', null, 'booking_min_notice_minutes: un string es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l25', '{"booking_min_notice_minutes": null}')$$,
  '23514', null, 'booking_min_notice_minutes: un null JSON es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l26', '{"booking_min_notice_minutes": true}')$$,
  '23514', null, 'booking_min_notice_minutes: un booleano es rechazado');
select lives_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l-todas', '{"booking_approval_timeout_hours": 24, "booking_max_active_per_customer": 5, "booking_max_per_hour": 3, "booking_min_notice_minutes": 30}')$$,
  'las cuatro claves juntas con los defaults son válidas');
select throws_ok(
  $$update public.tenants set settings = '{"booking_max_per_hour": 61}'
    where id = '00000000-0000-0000-0000-0000000000a1'$$,
  '23514', null, 'un UPDATE con un límite de reserva inválido es rechazado');
select throws_ok(
  $$insert into public.tenants (name, slug, settings) values ('S', 'l-mixta', '{"booking_approval_timeout_hours": 24, "booking_min_notice_minutes": -1}')$$,
  '23514', null, 'una clave inválida junto a una válida rechaza el settings');

select * from finish();
rollback;
