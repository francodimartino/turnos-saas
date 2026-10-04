-- purge_tenant_data (§2.2): además anula tenant_users.pending_invite_email del tenant (PII de una invitación
-- que ya no va a aceptar nadie). Parte de la versión vigente (20260103000100_conexion.sql): misma firma,
-- mismos cuatro pasos auditados, mismos grants e idempotente. El conteo de emails anulados va en el metadata del
-- paso 'collect_auth_users' (nunca el email).
create or replace function public.purge_tenant_data(p_tenant_id uuid)
returns setof uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  c_nombre_anonimo constant text := 'Profesional anonimizado';
  v_status text;
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_id uuid;
  v_n integer;
  v_profesionales integer;
  v_secret_ids uuid[];
  v_emails integer;
begin
  -- el lock evita que una reactivación concurrente cruce la validación
  select t.status into v_status from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status <> 'inactive' then
    raise exception 'purga rechazada: el tenant no está inactive' using errcode = 'check_violation';
  end if;
  if not exists (select 1 from public.list_tenants_due_for_purge() l where l.tenant_id = p_tenant_id) then
    raise exception 'purga rechazada: el período de gracia no venció' using errcode = 'check_violation';
  end if;

  -- 1) end_customers (anonymize_end_customer_core con actor system, como session_user = purge_job)
  --    y el nombre de los professionals del tenant
  v_n := 0;
  for v_id in
    select ec.id from public.end_customers ec
     where ec.tenant_id = p_tenant_id and ec.deleted_at is null
     order by ec.id
  loop
    perform public.anonymize_end_customer_core(v_id, 'system', v_actor_id);
    v_n := v_n + 1;
  end loop;

  update public.professionals p
     set name = c_nombre_anonimo
   where p.tenant_id = p_tenant_id and p.name <> c_nombre_anonimo;
  get diagnostics v_profesionales = row_count;

  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'anonymize_end_customers', 'count', v_n,
                       'professionals_anonymized', v_profesionales));

  -- 2) sesiones del bot que quedaron (de personas sin end_customer: su identificador es el teléfono)
  delete from public.bot_sessions bs where bs.tenant_id = p_tenant_id;
  get diagnostics v_n = row_count;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'delete_bot_sessions', 'count', v_n));

  -- 3) secrets de Vault de todas sus integraciones: las credenciales quedan inválidas
  select coalesce(array_agg(ti.secret_id), '{}'::uuid[]) into v_secret_ids
    from public.tenant_integrations ti
   where ti.tenant_id = p_tenant_id and ti.secret_id is not null;
  delete from vault.secrets s where s.id = any (v_secret_ids);
  get diagnostics v_n = row_count;
  update public.tenant_integrations ti set secret_id = null
   where ti.tenant_id = p_tenant_id and ti.secret_id is not null;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'delete_vault_secrets', 'count', v_n));

  -- 4) cuentas de Auth a eliminar por el job (la fila de tenant_users sobrevive con auth_user_id null)
  select count(*) into v_n
    from public.tenant_users tu
   where tu.tenant_id = p_tenant_id and tu.auth_user_id is not null;
  -- y el email de invitación pendiente (PII) de los que nunca vincularon cuenta
  update public.tenant_users tu set pending_invite_email = null
   where tu.tenant_id = p_tenant_id and tu.pending_invite_email is not null;
  get diagnostics v_emails = row_count;
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'collect_auth_users', 'count', v_n, 'pending_invite_emails_cleared', v_emails));

  return query
    select tu.auth_user_id
      from public.tenant_users tu
     where tu.tenant_id = p_tenant_id and tu.auth_user_id is not null
     order by tu.id;
end
$$;

revoke all on function public.purge_tenant_data(uuid) from public, anon, authenticated, service_role;
grant execute on function public.purge_tenant_data(uuid) to purge_job;
