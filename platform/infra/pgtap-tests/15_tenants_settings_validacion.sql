-- Validación de tenants.settings->'booking_approval_timeout_hours' (entero 1-168, §2.10/§7.1).
begin;
select plan(14);

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

select * from finish();
rollback;
