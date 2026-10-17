-- Cadena de hashes de audit_log (tamper-evidence): una cadena por tenant y una para tenant null.
-- La concurrencia real con dos conexiones esta en tests/integracion/test_audit_log_cadena.py; aca se prueba
-- el resultado y que el advisory lock por cadena queda tomado.
-- La cadena de plataforma (tenant null) puede tener filas previas de otros tests: se prueba relativa a ellas.
begin;
select plan(23);

do $$
begin
  execute 'grant platform_admin to current_user with inherit false, set true';
end
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000c1', 'C1', 'cad-1'),
  ('00000000-0000-0000-0000-0000000000c2', 'C2', 'cad-2');

-- vacia -> integra
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c1'), null::uuid,
  'una cadena vacia es integra');

-- varias escrituras: insert directo, audit_log_append (lo usan las funciones SQL) y varias filas en un statement
insert into public.audit_log (id, tenant_id, actor_type, action, metadata) values
  ('00000000-0000-0000-0000-00000000c101', '00000000-0000-0000-0000-0000000000c1', 'staff', 'cad_1', '{"k": 1}'),
  ('00000000-0000-0000-0000-00000000c102', '00000000-0000-0000-0000-0000000000c1', 'staff', 'cad_2', null);
select public.audit_log_append('00000000-0000-0000-0000-0000000000c1', 'system', null, 'cad_3', 'x', null, '{"a": [1, 2]}');
insert into public.audit_log (id, tenant_id, actor_type, action) values
  ('00000000-0000-0000-0000-00000000c201', '00000000-0000-0000-0000-0000000000c2', 'staff', 'cad_b1'),
  ('00000000-0000-0000-0000-00000000c202', '00000000-0000-0000-0000-0000000000c2', 'staff', 'cad_b2'),
  ('00000000-0000-0000-0000-00000000c001', null, 'platform_admin', 'cad_p1'),
  ('00000000-0000-0000-0000-00000000c002', null, 'platform_admin', 'cad_p2');

select is((select array_agg(chain_seq order by chain_seq) from public.audit_log
            where tenant_id = '00000000-0000-0000-0000-0000000000c1'), array[1, 2, 3]::bigint[],
  'chain_seq correlativo por tenant');
select is((select chain_seq from public.audit_log where id = '00000000-0000-0000-0000-00000000c201'), 1::bigint,
  'otro tenant tiene su propia cadena (arranca en 1)');
select is((select b.chain_seq - a.chain_seq from public.audit_log a, public.audit_log b
            where a.id = '00000000-0000-0000-0000-00000000c001' and b.id = '00000000-0000-0000-0000-00000000c002'),
  1::bigint, 'las filas de plataforma (tenant null) forman su propia cadena, correlativa');
select ok((select prev_hash is null from public.audit_log where id = '00000000-0000-0000-0000-00000000c101'),
  'la primera fila de la cadena tiene prev_hash null');
select is((select prev_hash from public.audit_log where id = '00000000-0000-0000-0000-00000000c102'),
  (select hash from public.audit_log where id = '00000000-0000-0000-0000-00000000c101'),
  'prev_hash apunta al hash de la fila anterior del mismo tenant');
select is((select prev_hash from public.audit_log where id = '00000000-0000-0000-0000-00000000c002'),
  (select hash from public.audit_log where id = '00000000-0000-0000-0000-00000000c001'),
  'la cadena de plataforma encadena sus filas entre si');
select is((select count(distinct hash) from public.audit_log where tenant_id = '00000000-0000-0000-0000-0000000000c1'),
  3::bigint, 'los hashes de la cadena son distintos');

-- un valor de hash provisto por quien inserta se ignora
insert into public.audit_log (id, tenant_id, actor_type, action, chain_seq, prev_hash, hash) values
  ('00000000-0000-0000-0000-00000000c1ff', '00000000-0000-0000-0000-0000000000c1', 'bot', 'cad_forzado',
   99, '\x00', '\x01');
select is((select chain_seq from public.audit_log where id = '00000000-0000-0000-0000-00000000c1ff'), 4::bigint,
  'el trigger sobrescribe chain_seq/prev_hash/hash provistos por quien inserta');

-- integra
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c1'), null::uuid,
  'cadena intacta: verify_audit_chain devuelve null');
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c2'), null::uuid, 'cadena de otro tenant intacta');
select is(public.verify_audit_chain(null), null::uuid, 'cadena de plataforma intacta');

-- el advisory lock de la cadena queda tomado hasta el fin de la transaccion
select ok(exists(select 1 from pg_locks where locktype = 'advisory' and pid = pg_backend_pid() and granted),
  'el insert toma un advisory lock transaccional por cadena');

-- permisos
select ok(has_function_privilege('platform_admin', 'public.verify_audit_chain(uuid)', 'execute'),
  'platform_admin ejecuta verify_audit_chain');
select is_empty(
  $$select r from unnest(array['anon', 'authenticated', 'service_role', 'dashboard_service', 'bot_service',
                               'purge_job', 'scheduler_job', 'platform_admin_readonly']) r
     where has_function_privilege(r, 'public.verify_audit_chain(uuid)', 'execute')$$,
  'ningun otro rol ejecuta verify_audit_chain');
select ok(not has_function_privilege('anon',
  'public.audit_log_calcular_hash(bytea, uuid, uuid, text, uuid, text, text, uuid, jsonb, timestamptz)', 'execute'),
  'la funcion de hash no es ejecutable por roles de aplicacion');

-- el trigger solo-agregar sigue vigente
select throws_ok($$delete from public.audit_log where id = '00000000-0000-0000-0000-00000000c102'$$,
  '42501', null, 'el trigger solo-agregar sigue rechazando DELETE');

-- alterar una fila (deshabilitando el trigger solo-agregar como duenio) rompe la cadena en esa fila
alter table public.audit_log disable trigger audit_log_solo_agregar_row;
update public.audit_log set action = 'cad_alterado' where id = '00000000-0000-0000-0000-00000000c102';
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c1'),
  '00000000-0000-0000-0000-00000000c102'::uuid, 'una fila alterada se detecta y se devuelve su id');
update public.audit_log set action = 'cad_2' where id = '00000000-0000-0000-0000-00000000c102';
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c1'), null::uuid, 'restaurada, vuelve a verificar');

-- un hash pisado a mano se detecta
update public.audit_log set hash = '\xdeadbeef' where id = '00000000-0000-0000-0000-00000000c101';
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c1'),
  '00000000-0000-0000-0000-00000000c101'::uuid, 'un hash pisado a mano se detecta');
update public.audit_log set hash = public.audit_log_calcular_hash(null, id, tenant_id, actor_type, actor_id, action,
  entity, entity_id, metadata, created_at) where id = '00000000-0000-0000-0000-00000000c101';

-- borrar una fila del medio: la siguiente queda con un salto de chain_seq
delete from public.audit_log where id = '00000000-0000-0000-0000-00000000c102';
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c1'),
  (select id from public.audit_log where tenant_id = '00000000-0000-0000-0000-0000000000c1' and chain_seq = 3),
  'una fila borrada del medio se detecta en la fila siguiente');
-- borrar la primera de la cadena: la segunda ya no empieza en 1
delete from public.audit_log where id = '00000000-0000-0000-0000-00000000c201';
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000c2'),
  '00000000-0000-0000-0000-00000000c202'::uuid, 'borrar el inicio de una cadena se detecta');
alter table public.audit_log enable trigger audit_log_solo_agregar_row;

-- platform_admin la ejecuta de verdad (definer: no depende de RLS)
create function pg_temp.como_admin() returns text language plpgsql as $f$
declare
  v text;
begin
  set local role platform_admin;
  v := coalesce(public.verify_audit_chain(null)::text, '<null>');
  reset role;
  return v;
end
$f$;
select is(pg_temp.como_admin(), '<null>', 'platform_admin ejecuta verify_audit_chain (cadena de plataforma)');

select * from finish();
rollback;
