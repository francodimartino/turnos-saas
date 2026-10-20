-- audit_chain_heads: una fila por cadena con la ultima posicion y hash, solo para purge_job
-- y platform_admin, y sin recorrer la tabla entera.
begin;
select plan(13);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'scheduler_job', 'platform_admin',
                               'platform_admin_readonly', 'purge_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

create function pg_temp.como(p_rol text, p_sql text) returns text
language plpgsql as $$
declare
  v_res text;
begin
  execute format('set role %I', p_rol);
  begin
    execute p_sql into v_res;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
  reset role;
  return v_res;
end
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'head-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'head-b'),
  ('00000000-0000-0000-0000-0000000000a3', 'C sin auditoria', 'head-c');

insert into public.audit_log (tenant_id, actor_type, action, metadata) values
  ('00000000-0000-0000-0000-0000000000a1', 'staff', 'head_a', '{"secreto": "x"}'),
  ('00000000-0000-0000-0000-0000000000a1', 'staff', 'head_a', '{}'),
  ('00000000-0000-0000-0000-0000000000a1', 'staff', 'head_a', '{}'),
  ('00000000-0000-0000-0000-0000000000a2', 'staff', 'head_b', '{}'),
  (null, 'platform_admin', 'head_p', '{}'),
  (null, 'platform_admin', 'head_p', '{}');

-- Cabezas de los tenants de este test (por tenant) y de plataforma.
select is(pg_temp.como('purge_job', $$select count(*)::text from public.audit_chain_heads() h
    where h.tenant_id in ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000a2')$$),
  '2', 'una fila por tenant con auditoria');
select is(pg_temp.como('purge_job', $$select h.chain_seq::text from public.audit_chain_heads() h
    where h.tenant_id = '00000000-0000-0000-0000-0000000000a1'$$), '3', 'la cabeza del tenant A es su ultima posicion');
select is(pg_temp.como('purge_job', $$select encode(h.hash, 'hex') from public.audit_chain_heads() h
    where h.tenant_id = '00000000-0000-0000-0000-0000000000a1'$$),
  (select encode(a.hash, 'hex') from public.audit_log a
    where a.tenant_id = '00000000-0000-0000-0000-0000000000a1' and a.chain_seq = 3), 'el hash es el de la ultima fila');
select is(pg_temp.como('purge_job', $$select h.chain_seq::text from public.audit_chain_heads() h
    where h.tenant_id = '00000000-0000-0000-0000-0000000000a2'$$), '1', 'la cabeza del tenant B es su unica fila');
select is(pg_temp.como('purge_job', $$select count(*)::text from public.audit_chain_heads() h
    where h.tenant_id = '00000000-0000-0000-0000-0000000000a3'$$), '0', 'un tenant sin auditoria no tiene cabeza');
select is(pg_temp.como('purge_job', $$select h.chain_seq::text || '|' || encode(h.hash, 'hex')
    from public.audit_chain_heads() h where h.tenant_id is null$$),
  (select a.chain_seq::text || '|' || encode(a.hash, 'hex') from public.audit_log a
    where a.tenant_id is null order by a.chain_seq desc limit 1), 'la cadena de plataforma (tenant_id null) tiene su cabeza');
select is(pg_temp.como('platform_admin', $$select count(*)::text from public.audit_chain_heads() h
    where h.tenant_id is null$$), '1', 'platform_admin la ejecuta');

-- Forma de la salida: sin metadata ni datos personales
select is((select string_agg(a, ',' order by n) from unnest(
  (select proargnames from pg_proc where oid = 'public.audit_chain_heads()'::regprocedure)) with ordinality t(a, n)),
  'tenant_id,chain_seq,hash', 'solo devuelve tenant_id, chain_seq y hash');

-- Permisos
select is((select string_agg(r, ',' order by r) from unnest(array['bot_service', 'dashboard_service', 'scheduler_job',
    'platform_admin_readonly', 'anon', 'authenticated', 'service_role']) r
    where has_function_privilege(r, 'public.audit_chain_heads()', 'execute')), null,
  'ningun otro rol de aplicacion la ejecuta');
select is(pg_temp.como('bot_service', $$select count(*)::text from public.audit_chain_heads()$$), 'err 42501',
  'bot_service no la ejecuta');

-- Plan: el cuerpo de la funcion (se explica el texto real de pg_proc) usa los indices, no recorre la tabla.
insert into public.audit_log (tenant_id, actor_type, action)
  select '00000000-0000-0000-0000-0000000000a2', 'staff', 'idx_b' from generate_series(1, 1500);
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

create temp table plan_cabezas as
  select pg_temp.plan_de((select prosrc from pg_proc where oid = 'public.audit_chain_heads()'::regprocedure)) as txt;

select doesnt_match((select txt from plan_cabezas), 'Seq Scan', 'las cabezas no recorren la tabla entera (sin Seq Scan)');
select matches((select txt from plan_cabezas), 'audit_log_chain_tenant_uq', 'usa el indice unico (tenant_id, chain_seq)');
select matches((select txt from plan_cabezas), 'audit_log_chain_plataforma_uq', 'usa el indice parcial de plataforma');

select * from finish();
rollback;
