-- Invariante del último owner activo de un tenant (§2.9).
begin;
select plan(12);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'owner-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'owner-b');
insert into auth.users (id) values ('00000000-0000-0000-0000-0000000000c1');
insert into public.tenant_users (id, tenant_id, auth_user_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
   '00000000-0000-0000-0000-0000000000c1', true);
insert into public.tenant_users (id, tenant_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1', false);

select throws_ok(
  $$update public.tenant_users set is_owner = false where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'no se puede quitar is_owner al último owner activo');
select throws_ok(
  $$update public.tenant_users set is_active = false where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'no se puede desactivar al último owner activo');
select throws_ok(
  $$delete from public.tenant_users where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'no se puede borrar al último owner activo');
select throws_ok(
  $$update public.tenant_users set tenant_id = '00000000-0000-0000-0000-0000000000a2'
    where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'no se puede mover de tenant al último owner activo');

-- un no-owner se puede borrar sin problema
select lives_ok(
  $$delete from public.tenant_users where id = '00000000-0000-0000-0000-0000000000b2'$$,
  'un tenant_user que no es owner se puede borrar');

-- un owner inactivo no cuenta como "otro owner"
insert into public.tenant_users (id, tenant_id, is_owner, is_active) values
  ('00000000-0000-0000-0000-0000000000b4', '00000000-0000-0000-0000-0000000000a1', true, false);
select throws_ok(
  $$update public.tenant_users set is_owner = false where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'un owner inactivo no sirve de reemplazo');

-- con un segundo owner activo, el primero sí se puede degradar
insert into public.tenant_users (id, tenant_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b3', '00000000-0000-0000-0000-0000000000a1', true);
select lives_ok(
  $$update public.tenant_users set is_owner = false where id = '00000000-0000-0000-0000-0000000000b1'$$,
  'con otro owner activo se puede quitar is_owner');
select throws_ok(
  $$update public.tenant_users set is_active = false where id = '00000000-0000-0000-0000-0000000000b3'$$,
  '23514', null, 'el segundo owner pasó a ser el último: no se puede desactivar');

-- un solo UPDATE que dejaría al tenant sin owners activos también se rechaza
update public.tenant_users set is_owner = true where id = '00000000-0000-0000-0000-0000000000b1';
select throws_ok(
  $$update public.tenant_users set is_active = false
    where tenant_id = '00000000-0000-0000-0000-0000000000a1' and is_owner$$,
  '23514', null, 'un UPDATE masivo que desactiva a todos los owners es rechazado');

-- el owner de otro tenant no cuenta
insert into public.tenant_users (id, tenant_id, is_owner) values
  ('00000000-0000-0000-0000-0000000000b5', '00000000-0000-0000-0000-0000000000a2', true);
update public.tenant_users set is_owner = false where id = '00000000-0000-0000-0000-0000000000b3';
select throws_ok(
  $$update public.tenant_users set is_owner = false where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'el owner de otro tenant no cuenta como reemplazo');

-- borrar la cuenta de Auth no dispara el trigger: la fila sobrevive con auth_user_id null
select lives_ok(
  $$delete from auth.users where id = '00000000-0000-0000-0000-0000000000c1'$$,
  'borrar la cuenta Auth del owner no rompe (on delete set null)');
select ok(
  (select auth_user_id is null from public.tenant_users where id = '00000000-0000-0000-0000-0000000000b1'),
  'la fila de tenant_users sobrevive con auth_user_id null');

select * from finish();
rollback;
