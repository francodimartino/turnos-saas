-- Migración de plataforma: audit_log_encadenar busca la fila anterior de la cadena por índice.
--
-- Problema: el trigger de 20260103001800_audit_log_hash_chain.sql busca la última fila de la cadena con
-- "tenant_id is not distinct from new.tenant_id order by chain_seq desc limit 1". Esa condición no puede usar los
-- índices únicos (tenant_id, chain_seq) ni el parcial de plataforma: cada INSERT de auditoría recorría la tabla
-- entera con el advisory lock tomado, y el costo crece con el volumen.
--
-- Solución: se reemplaza SOLO el cuerpo de la función con dos ramas indexables: "tenant_id = new.tenant_id" (usa
-- audit_log_chain_tenant_uq) y "tenant_id is null" para la cadena de plataforma (usa el índice parcial
-- audit_log_chain_plataforma_uq). El resultado es el mismo: advisory lock por cadena, misma canonicalización y
-- mismo cálculo de hash. El trigger, verify_audit_chain, los índices y los permisos no cambian (create or replace
-- conserva el revoke; se repite por claridad). No toca datos.

create or replace function public.audit_log_encadenar()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_ultimo_seq bigint;
  v_ultimo_hash bytea;
begin
  -- una cadena por tenant (y una para tenant null); el lock dura hasta el fin de la transacción
  perform pg_advisory_xact_lock(hashtextextended('audit_log_chain:' || coalesce(new.tenant_id::text, 'plataforma'), 0));

  if new.tenant_id is null then
    -- cadena de plataforma: índice parcial audit_log_chain_plataforma_uq
    select a.chain_seq, a.hash into v_ultimo_seq, v_ultimo_hash
      from public.audit_log a
     where a.tenant_id is null
     order by a.chain_seq desc
     limit 1;
  else
    -- cadena del tenant: índice audit_log_chain_tenant_uq (tenant_id, chain_seq)
    select a.chain_seq, a.hash into v_ultimo_seq, v_ultimo_hash
      from public.audit_log a
     where a.tenant_id = new.tenant_id
     order by a.chain_seq desc
     limit 1;
  end if;

  new.chain_seq := coalesce(v_ultimo_seq, 0) + 1;
  new.prev_hash := v_ultimo_hash;
  new.hash := public.audit_log_calcular_hash(v_ultimo_hash, new.id, new.tenant_id, new.actor_type, new.actor_id,
                                             new.action, new.entity, new.entity_id, new.metadata, new.created_at);
  return new;
end
$$;

revoke all on function public.audit_log_encadenar() from public, anon, authenticated, service_role;
