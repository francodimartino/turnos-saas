-- Trigger de transiciones de tenants.status (§2.2).
begin;
select plan(16);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'trans-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'trans-b');

-- active <-> suspended
select lives_ok(
  $$update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-0000000000a1'$$,
  'active -> suspended es válida');
select ok(
  (select deactivated_at is null from public.tenants where id = '00000000-0000-0000-0000-0000000000a1'),
  'suspender no arranca el reloj de purga (deactivated_at sigue null)');
select lives_ok(
  $$update public.tenants set status = 'active' where id = '00000000-0000-0000-0000-0000000000a1'$$,
  'suspended -> active es válida');

-- active -> inactive arranca el reloj; inactive -> active lo limpia
select lives_ok(
  $$update public.tenants set status = 'inactive' where id = '00000000-0000-0000-0000-0000000000a1'$$,
  'active -> inactive es válida');
select ok(
  (select deactivated_at is not null from public.tenants where id = '00000000-0000-0000-0000-0000000000a1'),
  'pasar a inactive setea deactivated_at');
select lives_ok(
  $$update public.tenants set status = 'active' where id = '00000000-0000-0000-0000-0000000000a1'$$,
  'inactive -> active (reactivación) es válida');
select ok(
  (select deactivated_at is null from public.tenants where id = '00000000-0000-0000-0000-0000000000a1'),
  'reactivar limpia deactivated_at');

-- suspended -> inactive
update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-0000000000a2';
select lives_ok(
  $$update public.tenants set status = 'inactive' where id = '00000000-0000-0000-0000-0000000000a2'$$,
  'suspended -> inactive es válida');

-- inválidas
select throws_ok(
  $$update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-0000000000a2'$$,
  '23514', null, 'inactive -> suspended es rechazada');
select throws_ok(
  $$update public.tenants set status = 'purged' where id = '00000000-0000-0000-0000-0000000000a1'$$,
  '23514', null, 'active -> purged es rechazada');

-- inactive -> purged y purged terminal
select lives_ok(
  $$update public.tenants set status = 'purged' where id = '00000000-0000-0000-0000-0000000000a2'$$,
  'inactive -> purged es válida');
select throws_ok(
  $$update public.tenants set status = 'active' where id = '00000000-0000-0000-0000-0000000000a2'$$,
  '23514', null, 'purged -> active es rechazada (terminal)');
select throws_ok(
  $$update public.tenants set status = 'inactive' where id = '00000000-0000-0000-0000-0000000000a2'$$,
  '23514', null, 'purged -> inactive es rechazada (terminal)');
select throws_ok(
  $$update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-0000000000a2'$$,
  '23514', null, 'purged -> suspended es rechazada (terminal)');

-- valores fuera del enum
select throws_ok(
  $$update public.tenants set status = 'borrado' where id = '00000000-0000-0000-0000-0000000000a1'$$,
  '23514', null, 'un status fuera del enum es rechazado');
select throws_ok(
  $$update public.tenants set status = null where id = '00000000-0000-0000-0000-0000000000a1'$$,
  '23502', null, 'status NULL es rechazado');

select * from finish();
rollback;
