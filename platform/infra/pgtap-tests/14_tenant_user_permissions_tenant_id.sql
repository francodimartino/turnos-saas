-- tenant_user_permissions.tenant_id lo fuerza un trigger desde tenant_users (§2.4).
begin;
select plan(8);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'perm-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'perm-b');
insert into public.tenant_users (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2');

-- INSERT con un tenant_id deliberadamente distinto
select lives_ok(
  $$insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key)
    values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b1', 'view_bookings')$$,
  'un INSERT con tenant_id de otro tenant no falla...');
select is(
  (select tenant_id from public.tenant_user_permissions
    where tenant_user_id = '00000000-0000-0000-0000-0000000000b1' and permission_key = 'view_bookings'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  '...y el trigger lo corrige al tenant real del tenant_user');

-- UPDATE con un tenant_id deliberadamente distinto
update public.tenant_user_permissions
   set tenant_id = '00000000-0000-0000-0000-0000000000a2'
 where tenant_user_id = '00000000-0000-0000-0000-0000000000b1' and permission_key = 'view_bookings';
select is(
  (select tenant_id from public.tenant_user_permissions
    where tenant_user_id = '00000000-0000-0000-0000-0000000000b1' and permission_key = 'view_bookings'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'un UPDATE de tenant_id queda corregido por el trigger');

-- UPDATE de tenant_user_id: el tenant_id sigue al nuevo padre
update public.tenant_user_permissions
   set tenant_user_id = '00000000-0000-0000-0000-0000000000b2'
 where tenant_user_id = '00000000-0000-0000-0000-0000000000b1' and permission_key = 'view_bookings';
select is(
  (select tenant_id from public.tenant_user_permissions
    where tenant_user_id = '00000000-0000-0000-0000-0000000000b2' and permission_key = 'view_bookings'),
  '00000000-0000-0000-0000-0000000000a2'::uuid,
  'al cambiar tenant_user_id, tenant_id sigue al nuevo tenant_user');

select throws_ok(
  $$insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000f0', 'view_bookings')$$,
  '23503', null, 'un tenant_user inexistente es rechazado');
select throws_ok(
  $$insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key)
    values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b2', 'no_existe')$$,
  '23503', null, 'un permission_key fuera del catálogo es rechazado');
select throws_ok(
  $$insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key)
    values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b2', 'view_bookings')$$,
  '23505', null, 'un permiso duplicado para el mismo tenant_user es rechazado');
select lives_ok(
  $$insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key)
    values (null, '00000000-0000-0000-0000-0000000000b2', 'manage_schedule')$$,
  'tenant_id puede omitirse: lo completa el trigger');

select * from finish();
rollback;
