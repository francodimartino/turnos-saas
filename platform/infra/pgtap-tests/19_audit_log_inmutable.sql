-- audit_log es solo-agregar: UPDATE, DELETE y TRUNCATE se rechazan (§2.10).
begin;
select plan(5);

insert into public.audit_log (id, actor_type, action, entity)
values ('00000000-0000-0000-0000-0000000000a9', 'system', 'crear', 'tenant');

select throws_ok(
  $$update public.audit_log set action = 'otra' where id = '00000000-0000-0000-0000-0000000000a9'$$,
  '42501', null, 'UPDATE sobre audit_log se rechaza (incluso como dueño/superusuario)');
select throws_ok(
  $$delete from public.audit_log where id = '00000000-0000-0000-0000-0000000000a9'$$,
  '42501', null, 'DELETE sobre audit_log se rechaza');
select throws_ok(
  $$truncate public.audit_log$$,
  '42501', null, 'TRUNCATE sobre audit_log se rechaza');
select is(
  (select action from public.audit_log where id = '00000000-0000-0000-0000-0000000000a9'),
  'crear', 'la fila sigue intacta');
select lives_ok(
  $$insert into public.audit_log (actor_type, action) values ('bot', 'agregar')$$,
  'INSERT sigue permitido');

select * from finish();
rollback;
