-- Cabezas de las cadenas de auditoría para anclarlas fuera de la base (Ola 4, tarea 15).
--
-- Problema: la cadena de hashes del audit_log (decisión D6) detecta ediciones y borrados del medio, pero quien
-- controla la base puede truncar el final de una cadena sin que nada lo delate: para detectarlo hace falta
-- copiar periódicamente el último hash de cada cadena a un lugar que la base no controle.
--
-- Cambio (no destructivo: solo una función nueva, sin tocar datos ni tablas):
--   * audit_chain_heads()  SECURITY DEFINER; devuelve una fila por cadena (cada tenant con auditoría y la cadena
--     de plataforma con tenant_id null) con tenant_id, chain_seq y hash de la última fila. No devuelve metadata
--     ni datos personales. EXECUTE solo para purge_job (job diario que ancla las cabezas) y platform_admin.
--   * Costo: recorre los tenants por salto en el índice audit_log_chain_tenant_uq (tenant_id, chain_seq) y toma
--     la última fila de cada cadena con una búsqueda por índice (la de plataforma, por audit_log_chain_plataforma_uq);
--     nunca recorre la tabla entera.

create or replace function public.audit_chain_heads()
returns table (tenant_id uuid, chain_seq bigint, hash bytea)
language sql
stable
security definer
set search_path = ''
as $$
  with recursive tenants_con_auditoria(tenant_id) as (
    (select a.tenant_id from public.audit_log a where a.tenant_id is not null order by a.tenant_id limit 1)
    union all
    select (select a.tenant_id from public.audit_log a
             where a.tenant_id > t.tenant_id order by a.tenant_id limit 1)
      from tenants_con_auditoria t
     where t.tenant_id is not null
  )
  select t.tenant_id, h.chain_seq, h.hash
    from tenants_con_auditoria t
   cross join lateral (select a.chain_seq, a.hash from public.audit_log a
                        where a.tenant_id = t.tenant_id order by a.chain_seq desc limit 1) h
   where t.tenant_id is not null
  union all
  select null::uuid, p.chain_seq, p.hash
    from (select a.chain_seq, a.hash from public.audit_log a
           where a.tenant_id is null order by a.chain_seq desc limit 1) p
$$;

revoke all on function public.audit_chain_heads() from public, anon, authenticated, service_role;
grant execute on function public.audit_chain_heads() to purge_job, platform_admin;
