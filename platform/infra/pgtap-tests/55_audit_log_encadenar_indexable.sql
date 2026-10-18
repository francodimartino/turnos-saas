-- audit_log_encadenar busca la fila anterior de la cadena por índice (no recorre la tabla con el lock tomado).
-- Se cargan filas suficientes, se analiza la tabla y, con enable_seqscan = off (solo en la transaccion), se
-- verifica con EXPLAIN que las dos busquedas del trigger (tenant y plataforma) usan Index Scan y no Seq Scan.
begin;
select plan(7);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000d1', 'D1', 'idx-1'),
  ('00000000-0000-0000-0000-0000000000d2', 'D2', 'idx-2');

insert into public.audit_log (tenant_id, actor_type, action)
  select '00000000-0000-0000-0000-0000000000d1', 'staff', 'idx_a' from generate_series(1, 1500);
insert into public.audit_log (tenant_id, actor_type, action)
  select '00000000-0000-0000-0000-0000000000d2', 'staff', 'idx_b' from generate_series(1, 1500);
insert into public.audit_log (tenant_id, actor_type, action)
  select null, 'platform_admin', 'idx_p' from generate_series(1, 1500);

analyze public.audit_log;
set local enable_seqscan = off;

create function pg_temp.plan_de(p_sql text) returns text
language plpgsql as $f$
declare r text; salida text := '';
begin
  for r in execute 'explain ' || p_sql loop salida := salida || r || E'\n'; end loop;
  return salida;
end
$f$;

select matches(
  pg_temp.plan_de($q$select a.chain_seq, a.hash from public.audit_log a
    where a.tenant_id = '00000000-0000-0000-0000-0000000000d1' order by a.chain_seq desc limit 1$q$),
  'Index Scan', 'la busqueda de la cadena de un tenant usa Index Scan');
select doesnt_match(
  pg_temp.plan_de($q$select a.chain_seq, a.hash from public.audit_log a
    where a.tenant_id = '00000000-0000-0000-0000-0000000000d1' order by a.chain_seq desc limit 1$q$),
  'Seq Scan', 'la busqueda de la cadena de un tenant no usa Seq Scan');
select matches(
  pg_temp.plan_de($q$select a.chain_seq, a.hash from public.audit_log a
    where a.tenant_id is null order by a.chain_seq desc limit 1$q$),
  'Index Scan', 'la busqueda de la cadena de plataforma usa Index Scan');
select doesnt_match(
  pg_temp.plan_de($q$select a.chain_seq, a.hash from public.audit_log a
    where a.tenant_id is null order by a.chain_seq desc limit 1$q$),
  'Seq Scan', 'la busqueda de la cadena de plataforma no usa Seq Scan');

-- la funcion ya no usa la condicion no indexable
select unlike(pg_get_functiondef('public.audit_log_encadenar()'::regprocedure), '%is not distinct from%',
  'audit_log_encadenar no usa is not distinct from');

-- el comportamiento no cambia: la cadena sigue correlativa e integra
select is((select max(chain_seq) from public.audit_log where tenant_id = '00000000-0000-0000-0000-0000000000d1'),
  1500::bigint, 'la cadena del tenant quedo correlativa (1..1500)');
do $$ begin execute 'grant platform_admin to current_user with inherit false, set true'; end $$;
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000d2'), null::uuid,
  'verify_audit_chain sigue dando integra');

select * from finish();
rollback;
