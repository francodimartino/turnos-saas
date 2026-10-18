-- Retiro de view_reports del catálogo (20260103001900): sin la clave y sin asignaciones.
begin;
select plan(5);

select is(
  (select count(*) from public.permissions where key = 'view_reports'),
  0::bigint, 'el catálogo permissions no lista view_reports');
select is(
  (select array_agg(key order by key) from public.permissions),
  array['approve_bookings', 'delete_customers', 'edit_prices', 'manage_customers', 'manage_schedule',
        'manage_staff', 'view_bookings'],
  'el resto del catálogo quedó intacto');

-- Reproduce el estado previo (clave + asignación) y corre las sentencias de la migración.
insert into public.tenants (id, name, slug) values ('00000000-0000-0000-0000-0000000000a1', 'A', 'vr-a');
insert into public.tenant_users (id, tenant_id) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1');
insert into public.permissions (key) values ('view_reports');
insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key) values
  (null, '00000000-0000-0000-0000-0000000000b1', 'view_reports'),
  (null, '00000000-0000-0000-0000-0000000000b1', 'edit_prices');

delete from public.tenant_user_permissions where permission_key = 'view_reports';
delete from public.permissions where key = 'view_reports';

select is(
  (select count(*) from public.tenant_user_permissions where permission_key = 'view_reports'),
  0::bigint, 'ningún tenant_user conserva view_reports');
select is(
  (select count(*) from public.permissions where key = 'view_reports'),
  0::bigint, 'la clave desaparece del catálogo');
select is(
  (select array_agg(permission_key) from public.tenant_user_permissions
    where tenant_user_id = '00000000-0000-0000-0000-0000000000b1'),
  array['edit_prices'], 'las demás asignaciones del tenant_user quedan');

select * from finish();
rollback;
