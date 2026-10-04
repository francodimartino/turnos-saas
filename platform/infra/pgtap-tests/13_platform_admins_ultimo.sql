-- Invariante: no se puede borrar la última fila de platform_admins (§2.10).
begin;
select plan(7);

insert into auth.users (id) values
  ('00000000-0000-0000-0000-0000000000c1'),
  ('00000000-0000-0000-0000-0000000000c2');
insert into public.platform_admins (auth_user_id) values
  ('00000000-0000-0000-0000-0000000000c1'),
  ('00000000-0000-0000-0000-0000000000c2');

select throws_ok(
  $$delete from public.platform_admins$$,
  '23514', null, 'un DELETE que vacía la tabla es rechazado');
select is((select count(*) from public.platform_admins), 2::bigint,
  'tras el rechazo siguen las dos filas');

select lives_ok(
  $$delete from public.platform_admins where auth_user_id = '00000000-0000-0000-0000-0000000000c1'$$,
  'con dos admins se puede borrar uno');
select throws_ok(
  $$delete from public.platform_admins where auth_user_id = '00000000-0000-0000-0000-0000000000c2'$$,
  '23514', null, 'no se puede borrar al último platform_admin');
select throws_ok(
  $$delete from auth.users where id = '00000000-0000-0000-0000-0000000000c2'$$,
  '23514', null, 'el cascade desde auth.users tampoco puede borrar al último platform_admin');
select is((select count(*) from public.platform_admins), 1::bigint,
  'el último platform_admin sigue existiendo');
select is((select count(*) from auth.users where id = '00000000-0000-0000-0000-0000000000c2'), 1::bigint,
  'la cuenta Auth del último platform_admin sigue existiendo');

select * from finish();
rollback;
