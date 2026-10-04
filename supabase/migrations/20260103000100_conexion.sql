-- Migración de conexión: une la plataforma (migraciones 20260101*) con el dominio de turnos (20260102*).
-- Ver ARCHITECTURE.md §2.2, §2.10, §3.2, §3.5 y §4.2. Es una migración del producto: la plantilla de
-- plataforma no lleva lógica de producto.
--
-- Qué hace (todo con create or replace, misma firma que lo que reemplaza; migración no destructiva:
-- no borra ni altera datos ni columnas):
--   1. payments_forzar_tenant_id  agrega las ramas por payable_type: 'booking_deposit' resuelve el tenant
--                                 real desde bookings y 'booking_pack' desde booking_packs. En INSERT corrige
--                                 el tenant_id recibido al real; en UPDATE rechaza mover el pago a otro tenant.
--   2. anonymize_end_customer_core  agrega las ramas de dominio de §3.5: quita los datos del pagador de
--                                 payments.webhook_payload, cancela los bookings pending futuros del cliente
--                                 (la sesión del pack la devuelve el trigger de bookings, no esta función) y
--                                 deja los confirmed. Es la función que llaman tanto el borrado individual
--                                 (anonymize_end_customer) como la purga de tenant: ambos ejecutan las ramas.
--   3. purge_tenant_data          pasa a anonimizar también professionals.name (§2.2). Mismos pasos auditados
--                                 y mismo comportamiento; la cantidad de professionals anonimizados queda en
--                                 el metadata del paso 'anonymize_end_customers' (sin pasos nuevos).
--   4. calendar_scrub_targets     pares (integration_id, google_calendar_event_id) a limpiar en Google Calendar
--                                 tras anonimizar (§3.5) o purgar (paso 0 de §2.2). Con integration_id null cuando
--                                 el profesional no tiene integración de calendar: el caller registra
--                                 calendar_scrub_failed con el id del evento (sin PII).
--
-- calendar_scrub_targets sigue el patrón de anonymize_end_customer: la función pública (SECURITY DEFINER)
-- deriva el actor de session_user y delega en calendar_scrub_targets_core, que recibe el actor ya decidido,
-- corre como invocador y no tiene EXECUTE para nadie (así pgTAP prueba los rechazos; lo que depende de
-- session_user se prueba con conexiones reales de cada rol).
--
-- EXECUTE: las funciones nuevas revocan de PUBLIC, anon, authenticated y service_role y se otorgan solo a
-- los roles de §3.2. Todas fijan search_path = '' y usan nombres calificados.

-- ---------------------------------------------------------------------------
-- 1. payments: tenant_id real desde el padre (§2.10)
-- ---------------------------------------------------------------------------
-- Corre como invocador (como los demás triggers de tenant_id): si el padre no es visible para quien
-- escribe (RLS), el pago se rechaza igual que uno con un payable_id inexistente.
create or replace function public.payments_forzar_tenant_id()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_tenant_id uuid;
begin
  if tg_op = 'UPDATE' and new.tenant_id is distinct from old.tenant_id then
    raise exception 'payments.tenant_id no se puede modificar'
      using errcode = 'check_violation';
  end if;

  -- sin padre (todavía) no hay de dónde derivar el tenant: rige el recibido (not null en la tabla)
  if new.payable_id is null then
    return new;
  end if;

  -- un UPDATE que no toca el padre no tiene nada nuevo que resolver (p. ej. el webhook que cambia el estado)
  if tg_op = 'UPDATE'
     and new.payable_id is not distinct from old.payable_id
     and new.payable_type is not distinct from old.payable_type then
    return new;
  end if;

  if new.payable_type = 'booking_deposit' then
    select b.tenant_id into v_tenant_id
      from public.bookings b
     where b.id = new.payable_id;
  elsif new.payable_type = 'booking_pack' then
    select bp.tenant_id into v_tenant_id
      from public.booking_packs bp
     where bp.id = new.payable_id;
  end if;

  if v_tenant_id is null then
    raise exception 'payments: % % inexistente', new.payable_type, new.payable_id
      using errcode = 'foreign_key_violation';
  end if;

  -- un pago nunca se re-apunta a un padre de otro tenant
  if tg_op = 'UPDATE' and v_tenant_id <> old.tenant_id then
    raise exception 'payments: el payable_id pertenece a otro tenant'
      using errcode = 'check_violation';
  end if;

  new.tenant_id := v_tenant_id;
  return new;
end
$$;

-- ---------------------------------------------------------------------------
-- 2. Anonimización del end_customer con las ramas de dominio (§3.5)
-- ---------------------------------------------------------------------------
-- Misma lógica y misma firma que la de la migración de plataforma 5, más: payer de payments,
-- cancelación de bookings pending futuros y 'payments.payer' en la constancia del audit_log.
create or replace function public.anonymize_end_customer_core(p_id uuid, p_actor_type text, p_actor_id uuid)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_caller_tenant uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_tenant_id uuid;
  v_channel text;
  v_channel_user_id text;
  v_cancelados integer;
begin
  if p_actor_type is null or p_actor_type not in ('bot', 'staff', 'platform_admin', 'system') then
    raise exception 'actor_type no autorizado para anonimizar' using errcode = 'insufficient_privilege';
  end if;

  if p_actor_type in ('platform_admin', 'system') then
    select ec.tenant_id, ec.channel, ec.channel_user_id
      into v_tenant_id, v_channel, v_channel_user_id
      from public.end_customers ec
     where ec.id = p_id
       for update;
    if not found then
      raise exception 'end_customer inexistente' using errcode = 'no_data_found';
    end if;
  else
    -- NULL <> tenant no dispararía la excepción (NULL se trata como false en un IF): se exige no nulo
    if v_caller_tenant is null then
      raise exception 'tenant mismatch: contexto de tenant requerido' using errcode = 'insufficient_privilege';
    end if;
    select ec.tenant_id, ec.channel, ec.channel_user_id
      into v_tenant_id, v_channel, v_channel_user_id
      from public.end_customers ec
     where ec.id = p_id and ec.tenant_id = v_caller_tenant
       for update;
    if not found then
      raise exception 'tenant mismatch: end_customer inexistente o de otro tenant'
        using errcode = 'insufficient_privilege';
    end if;
  end if;

  -- Idempotente: si channel_user_id ya es null (ya anonimizado) se conserva el hash existente.
  -- Sin la clave HMAC en Vault pseudonymize_channel_user devuelve NULL y el check de end_customers
  -- (canal <=> identificador o hash) hace fallar la operación: falla cerrada, sin dejar la fila a medias.
  update public.end_customers ec
     set phone = null,
         name = null,
         extra_data = '{}'::jsonb,
         deleted_at = coalesce(ec.deleted_at, now()),
         channel_user_hash = case
           when v_channel_user_id is null then ec.channel_user_hash
           else public.pseudonymize_channel_user(v_tenant_id, v_channel, v_channel_user_id)
         end,
         channel_user_id = null
   where ec.id = p_id;

  -- copias de PII fuera de end_customers (§2.10)

  -- datos del pagador en el payload crudo del webhook (de los bookings y packs del cliente);
  -- provider_payment_id y el resto del payload se conservan: permiten consultar el pago en Mercado Pago
  update public.payments p
     set webhook_payload = (((p.webhook_payload - 'payer') #- '{additional_info,payer}') #- '{card,cardholder}')
   where p.tenant_id = v_tenant_id
     and p.webhook_payload is not null
     and ((p.payable_type = 'booking_deposit'
           and p.payable_id in (select b.id from public.bookings b
                                 where b.tenant_id = v_tenant_id and b.end_customer_id = p_id))
          or (p.payable_type = 'booking_pack'
              and p.payable_id in (select bp.id from public.booking_packs bp
                                    where bp.tenant_id = v_tenant_id and bp.end_customer_id = p_id)));

  update public.notifications_log nl
     set last_error = null
   where nl.end_customer_id = p_id and nl.tenant_id = v_tenant_id;

  -- turnos futuros pendientes: se cancelan y liberan el horario (decisión de §3.5). La sesión del pack, si
  -- usaban una, la devuelve el trigger AFTER UPDATE de bookings, nunca esta función. Los confirmed quedan:
  -- el dashboard los muestra "sin cliente identificable" (end_customers.deleted_at).
  update public.bookings b
     set status = 'cancelled',
         pending_expires_at = null
   where b.tenant_id = v_tenant_id
     and b.end_customer_id = p_id
     and b.status = 'pending'
     and lower(b.time_range) > now();
  get diagnostics v_cancelados = row_count;

  delete from public.bot_sessions bs
   where bs.tenant_id = v_tenant_id
     and bs.channel = v_channel
     and bs.channel_user_id = v_channel_user_id;

  -- constancia del borrado: fecha, actor y campos, sin retener ningún valor original
  perform public.audit_log_append(
    v_tenant_id, p_actor_type, p_actor_id, 'anonymize_end_customer', 'end_customers', p_id,
    jsonb_build_object('fields_anonymized',
      jsonb_build_array('phone', 'name', 'extra_data', 'channel_user_id', 'payments.payer',
                        'notifications_log.last_error'),
      'bookings_cancelled', v_cancelados));
end
$$;

revoke all on function public.anonymize_end_customer_core(uuid, text, uuid)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. Purga de tenant: también anonimiza professionals.name (§2.2)
-- ---------------------------------------------------------------------------
-- Idéntica a la de la migración de plataforma 5, salvo el paso 1, que además anonimiza el nombre de los
-- professionals (dato personal del personal). No agrega pasos auditados: el conteo queda en el metadata
-- del paso 'anonymize_end_customers'. Idempotente: un professional ya anonimizado no se vuelve a contar.
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
  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'collect_auth_users', 'count', v_n));

  return query
    select tu.auth_user_id
      from public.tenant_users tu
     where tu.tenant_id = p_tenant_id and tu.auth_user_id is not null
     order by tu.id;
end
$$;

revoke all on function public.purge_tenant_data(uuid) from public, anon, authenticated, service_role;
grant execute on function public.purge_tenant_data(uuid) to purge_job;

-- ---------------------------------------------------------------------------
-- 4. calendar_scrub_targets (§3.5, §2.2 paso 0)
-- ---------------------------------------------------------------------------
-- Lógica, con el actor ya decidido por quien llama (nunca un parámetro de un caller externo).
--   bot / staff      exigen contexto de tenant no nulo e igual a p_tenant_id (NULL <> tenant no dispararía
--                    la excepción en un IF: se chequea el nulo aparte).
--   platform_admin   cruza tenants, como anonymize_end_customer.
--   system           (purge_job) re-valida en la base que el tenant esté inactive con el período vencido:
--                    el paso 0 corre antes de purge_tenant_data y no puede depender de su validación.
-- Devuelve los eventos de Google Calendar de los bookings del tenant (de todos los estados, pasados y
-- futuros), o solo los del cliente si se indica p_end_customer_id. Un end_customer de otro tenant no
-- devuelve nada: el filtro por tenant_id siempre rige.
create function public.calendar_scrub_targets_core(p_tenant_id uuid, p_end_customer_id uuid, p_actor_type text)
returns table (integration_id uuid, google_calendar_event_id text)
language plpgsql
set search_path = ''
as $$
declare
  v_caller_tenant uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
begin
  if p_actor_type is null or p_actor_type not in ('bot', 'staff', 'platform_admin', 'system') then
    raise exception 'actor_type no autorizado para calendar_scrub_targets'
      using errcode = 'insufficient_privilege';
  end if;

  if p_tenant_id is null then
    raise exception 'tenant_id requerido' using errcode = 'invalid_parameter_value';
  end if;

  if p_actor_type in ('bot', 'staff') then
    if v_caller_tenant is null or v_caller_tenant <> p_tenant_id then
      raise exception 'tenant mismatch: el tenant pedido no es el del contexto'
        using errcode = 'insufficient_privilege';
    end if;
  elsif p_actor_type = 'system' then
    if not exists (select 1 from public.list_tenants_due_for_purge() l where l.tenant_id = p_tenant_id) then
      raise exception 'purga rechazada: el tenant no está inactive con el período de gracia vencido'
        using errcode = 'check_violation';
    end if;
  end if;

  return query
    select (select ti.id
              from public.tenant_integrations ti
             where ti.tenant_id = b.tenant_id
               and ti.integration_type = 'google_calendar'
               and ti.professional_id = b.professional_id),
           b.google_calendar_event_id
      from public.bookings b
     where b.tenant_id = p_tenant_id
       and b.google_calendar_event_id is not null
       and (p_end_customer_id is null or b.end_customer_id = p_end_customer_id)
     order by b.google_calendar_event_id, b.id;
end
$$;

revoke all on function public.calendar_scrub_targets_core(uuid, uuid, text)
  from public, anon, authenticated, service_role;

-- Función pública. El actor se deriva del rol real que llamó (session_user), nunca de un parámetro.
create function public.calendar_scrub_targets(p_tenant_id uuid, p_end_customer_id uuid default null)
returns table (integration_id uuid, google_calendar_event_id text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor_type text := public.audit_actor_type(session_user);
begin
  if v_actor_type is null then
    raise exception 'rol no autorizado para calendar_scrub_targets: %', session_user
      using errcode = 'insufficient_privilege';
  end if;

  return query
    select t.integration_id, t.google_calendar_event_id
      from public.calendar_scrub_targets_core(p_tenant_id, p_end_customer_id, v_actor_type) t;
end
$$;

revoke all on function public.calendar_scrub_targets(uuid, uuid) from public, anon, authenticated, service_role;
grant execute on function public.calendar_scrub_targets(uuid, uuid)
  to bot_service, dashboard_service, platform_admin, purge_job;
