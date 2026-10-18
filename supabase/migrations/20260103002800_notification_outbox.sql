-- Migración de plataforma: outbox de notificaciones (cola persistente de avisos al cliente, §6.4).
--
-- Problema: los avisos se reintentaban como BackgroundTasks en memoria; un redeploy a mitad de un envío dejaba el
-- aviso perdido en 'pending'. Solución: la intención de avisar se escribe en la MISMA transacción que el cambio de
-- turno (si la transacción hace rollback, no queda aviso) y un envío posterior, o el reescaneo del job de
-- vencimiento, la lleva a 'sent' o 'failed'.
--
-- INTERFAZ PARA EL BACKEND (bot, dashboard-backend, job de vencimiento)
--   Todo pasa por funciones SECURITY DEFINER; ningún rol de aplicación toca la tabla con SQL directo.
--
--   public.enqueue_notification(p_end_customer_id uuid, p_channel text, p_template text,
--                               p_params jsonb default '{}', p_first_attempt_delay_seconds integer default 300)
--     returns uuid
--     - Roles: bot_service, dashboard_service, scheduler_job. Se llama DENTRO de la transacción del cambio de turno,
--       con contexto de tenant (app.current_tenant_id): sin contexto o con un cliente inexistente/de otro tenant
--       lanza insufficient_privilege (42501).
--     - Devuelve el id de la fila en el outbox ('pending'), o NULL si el cliente ya fue anonimizado (no hay a quién
--       avisar y no se retiene nada: no se encola).
--     - p_params: objeto JSON con identificadores y datos NO personales (id del booking, fecha, nombre del
--       servicio...). NUNCA el texto renderizado ni nombre, teléfono o identificador de canal: el destinatario se
--       resuelve al enviar desde end_customers. Debe ser un objeto de hasta 4000 caracteres.
--     - p_first_attempt_delay_seconds (0..86400): ventana en la que el envío inmediato post-COMMIT es el dueño de la
--       fila; pasada esa ventana la toma el reescaneo (claim). Es el "X minutos configurable" del reescaneo.
--
--   public.complete_notification_outbox(p_id uuid, p_status text, p_last_error text default null,
--                                       p_retry_in_seconds integer default null) returns boolean
--     - Roles: bot_service, dashboard_service, scheduler_job. Exige contexto de tenant (el del dueño de la fila).
--     - p_status: 'sent' | 'failed' (finales) | 'pending' (reprogramar; exige p_retry_in_seconds 0..86400).
--     - Solo actúa sobre filas 'pending': devuelve true si la actualizó, false si no existe, es de otro tenant o ya
--       estaba en estado final (idempotente: el envío inmediato y el reescaneo no se pisan).
--     - p_last_error: código/mensaje del proveedor, truncado a 500; nunca el texto enviado ni PII.
--
--   public.claim_notification_outbox(p_limit integer default 20, p_lease_seconds integer default 300,
--                                    p_max_attempts integer default 5)
--     returns table (id, tenant_id, end_customer_id, channel, template, params, attempts)
--     - Rol: scheduler_job (el job de reescaneo). Corre entre tenants, sin contexto de tenant: el job fija el
--       contexto de cada fila (tenant_id) antes de llamar a complete_notification_outbox.
--     - Reclama hasta p_limit (1..500) filas 'pending' con next_attempt_at <= now(), con FOR UPDATE SKIP LOCKED, y
--       en la misma sentencia las arrienda: next_attempt_at pasa a now() + p_lease_seconds (30..86400) y attempts
--       suma 1. Dos corridas concurrentes nunca reciben la misma fila; si el job muere a mitad del lote, las filas
--       reclamadas y no completadas vuelven a estar disponibles al vencer el arriendo.
--     - Las filas 'pending' vencidas que ya alcanzaron p_max_attempts reclamos (2..20) pasan a 'failed' sin
--       devolverse (un job que muere siempre en la misma fila no la reprocesa para siempre).
--
--   public.prune_notification_outbox(p_retencion_dias integer default 30) returns integer
--     - Rol: scheduler_job. Borra las filas finales ('sent'/'failed') con más de p_retencion_dias (1..365) días.
--
-- Datos personales: la fila no guarda texto, nombre, teléfono ni identificador de canal. Aun así,
-- anonymize_end_customer_core borra las filas del cliente y purge_tenant_data las del tenant (más abajo).
-- Privilegios (§3.2): ningún rol de aplicación tiene grants sobre la tabla; RLS con tenant_isolation por defensa
-- en profundidad.

create table public.notification_outbox (
  id uuid primary key default gen_random_uuid(),
  -- desnormalizado desde end_customers.tenant_id y forzado por trigger
  tenant_id uuid not null,
  end_customer_id uuid not null,
  channel text not null check (channel in ('telegram', 'whatsapp')),
  template text not null check (length(template) between 1 and 100),
  params jsonb not null default '{}'::jsonb
    check (jsonb_typeof(params) = 'object' and length(params::text) <= 4000),
  -- 'pending' (por enviar) | 'sent' | 'failed' (intentos agotados o error permanente)
  status text not null default 'pending' check (status in ('pending', 'sent', 'failed')),
  attempts integer not null default 0 check (attempts >= 0),
  next_attempt_at timestamptz not null default now(),
  -- solo código/mensaje del proveedor, NUNCA el texto enviado
  last_error text null check (length(last_error) <= 500),
  created_at timestamptz not null default now(),
  processed_at timestamptz null,
  check ((status = 'pending') = (processed_at is null)),
  unique (tenant_id, id),
  constraint notification_outbox_tenant_end_customer_fkey
    foreign key (tenant_id, end_customer_id)
    references public.end_customers (tenant_id, id) on delete cascade
);

create index notification_outbox_pending_idx
  on public.notification_outbox (next_attempt_at) where status = 'pending';
create index notification_outbox_end_customer_idx
  on public.notification_outbox (tenant_id, end_customer_id);
create index notification_outbox_processed_idx
  on public.notification_outbox (processed_at) where status <> 'pending';

-- tenant_id se copia desde end_customers, ignorando lo que mande la aplicación (§7, nota de RLS).
create function public.notification_outbox_forzar_tenant_id()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_tenant_id uuid;
begin
  select ec.tenant_id into v_tenant_id
  from public.end_customers ec
  where ec.id = new.end_customer_id;

  if v_tenant_id is null then
    raise exception 'end_customer % inexistente', new.end_customer_id
      using errcode = 'foreign_key_violation';
  end if;

  new.tenant_id := v_tenant_id;
  return new;
end
$$;

create trigger notification_outbox_forzar_tenant_id
  before insert or update on public.notification_outbox
  for each row execute function public.notification_outbox_forzar_tenant_id();

alter table public.notification_outbox enable row level security;

create policy tenant_isolation on public.notification_outbox
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

revoke all on public.notification_outbox from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- enqueue_notification
-- ---------------------------------------------------------------------------
create function public.enqueue_notification(
  p_end_customer_id uuid,
  p_channel text,
  p_template text,
  p_params jsonb default '{}'::jsonb,
  p_first_attempt_delay_seconds integer default 300
)
returns uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_deleted_at timestamptz;
  v_id uuid;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  if p_first_attempt_delay_seconds is null or p_first_attempt_delay_seconds not between 0 and 86400 then
    raise exception 'demora del primer intento invalida' using errcode = 'invalid_parameter_value';
  end if;

  select ec.deleted_at into v_deleted_at
    from public.end_customers ec
   where ec.id = p_end_customer_id and ec.tenant_id = v_ctx;
  if not found then
    raise exception 'cliente inexistente o de otro tenant' using errcode = 'insufficient_privilege';
  end if;
  if v_deleted_at is not null then
    return null;  -- anonimizado: no hay a quien avisar y no se retiene nada
  end if;

  insert into public.notification_outbox
    (tenant_id, end_customer_id, channel, template, params, next_attempt_at)
  values
    (v_ctx, p_end_customer_id, p_channel, p_template, coalesce(p_params, '{}'::jsonb),
     now() + make_interval(secs => p_first_attempt_delay_seconds))
  returning id into v_id;

  return v_id;
end
$$;

revoke all on function public.enqueue_notification(uuid, text, text, jsonb, integer)
  from public, anon, authenticated, service_role;
grant execute on function public.enqueue_notification(uuid, text, text, jsonb, integer)
  to bot_service, dashboard_service, scheduler_job;

-- ---------------------------------------------------------------------------
-- complete_notification_outbox
-- ---------------------------------------------------------------------------
create function public.complete_notification_outbox(
  p_id uuid,
  p_status text,
  p_last_error text default null,
  p_retry_in_seconds integer default null
)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_filas integer;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  if p_status is null or p_status not in ('sent', 'failed', 'pending') then
    raise exception 'estado invalido' using errcode = 'invalid_parameter_value';
  end if;
  if p_status = 'pending'
     and (p_retry_in_seconds is null or p_retry_in_seconds not between 0 and 86400) then
    raise exception 'reprogramar exige p_retry_in_seconds entre 0 y 86400' using errcode = 'invalid_parameter_value';
  end if;

  update public.notification_outbox o
     set status = p_status,
         last_error = left(p_last_error, 500),
         processed_at = case when p_status = 'pending' then null else now() end,
         next_attempt_at = case when p_status = 'pending'
                                then now() + make_interval(secs => p_retry_in_seconds)
                                else o.next_attempt_at end
   where o.id = p_id and o.tenant_id = v_ctx and o.status = 'pending';
  get diagnostics v_filas = row_count;
  return v_filas = 1;
end
$$;

revoke all on function public.complete_notification_outbox(uuid, text, text, integer)
  from public, anon, authenticated, service_role;
grant execute on function public.complete_notification_outbox(uuid, text, text, integer)
  to bot_service, dashboard_service, scheduler_job;

-- ---------------------------------------------------------------------------
-- claim_notification_outbox
-- ---------------------------------------------------------------------------
create function public.claim_notification_outbox(
  p_limit integer default 20,
  p_lease_seconds integer default 300,
  p_max_attempts integer default 5
)
returns table (
  id uuid,
  tenant_id uuid,
  end_customer_id uuid,
  channel text,
  template text,
  params jsonb,
  attempts integer
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
#variable_conflict use_column
begin
  if p_limit is null or p_limit not between 1 and 500
     or p_lease_seconds is null or p_lease_seconds not between 30 and 86400
     or p_max_attempts is null or p_max_attempts not between 2 and 20 then
    raise exception 'parametros invalidos' using errcode = 'invalid_parameter_value';
  end if;

  -- vencidas que ya agotaron los reclamos: dead-letter, no se reprocesan
  update public.notification_outbox o
     set status = 'failed',
         last_error = 'intentos agotados sin confirmacion de envio',
         processed_at = now()
   where o.status = 'pending'
     and o.next_attempt_at <= now()
     and o.attempts >= p_max_attempts;

  return query
  with elegidas as (
    select q.id
      from public.notification_outbox q
     where q.status = 'pending' and q.next_attempt_at <= now()
     order by q.next_attempt_at, q.id
     limit p_limit
       for update skip locked
  )
  update public.notification_outbox o
     set attempts = o.attempts + 1,
         next_attempt_at = now() + make_interval(secs => p_lease_seconds)
    from elegidas e
   where o.id = e.id
  returning o.id, o.tenant_id, o.end_customer_id, o.channel, o.template, o.params, o.attempts;
end
$$;

revoke all on function public.claim_notification_outbox(integer, integer, integer)
  from public, anon, authenticated, service_role;
grant execute on function public.claim_notification_outbox(integer, integer, integer) to scheduler_job;

-- ---------------------------------------------------------------------------
-- prune_notification_outbox
-- ---------------------------------------------------------------------------
create function public.prune_notification_outbox(p_retencion_dias integer default 30)
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_filas integer;
begin
  if p_retencion_dias is null or p_retencion_dias not between 1 and 365 then
    raise exception 'retencion invalida' using errcode = 'invalid_parameter_value';
  end if;

  delete from public.notification_outbox o
   where o.status <> 'pending'
     and o.processed_at < now() - make_interval(days => p_retencion_dias);
  get diagnostics v_filas = row_count;
  return v_filas;
end
$$;

revoke all on function public.prune_notification_outbox(integer)
  from public, anon, authenticated, service_role;
grant execute on function public.prune_notification_outbox(integer) to scheduler_job;

-- ---------------------------------------------------------------------------
-- Anonimización y purga: las filas del outbox se borran
-- ---------------------------------------------------------------------------
-- Idénticas a las versiones vigentes (20260103000100_conexion.sql y 20260103001600_purga_desde_purging.sql), con
-- el borrado del outbox; misma firma y mismos grants.

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

  -- avisos pendientes de enviar (notification_outbox): se borran, no se anonimizan; la anonimización los descarta
  delete from public.notification_outbox o
   where o.end_customer_id = p_id and o.tenant_id = v_tenant_id;

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
                        'notifications_log.last_error', 'notification_outbox'),
      'bookings_cancelled', v_cancelados));
end
$$;

revoke all on function public.anonymize_end_customer_core(uuid, text, uuid)
  from public, anon, authenticated, service_role;

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
  v_outbox integer;
begin
  -- el lock evita que una reactivación concurrente cruce la validación
  select t.status into v_status from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status = 'inactive' then
    -- valida inactive + gracia vencida bajo lock y pasa a purging (o rechaza): desde acá no se puede reactivar
    perform public.begin_tenant_purge(p_tenant_id);
  elsif v_status <> 'purging' then
    raise exception 'purga rechazada: el tenant no está inactive ni en purga' using errcode = 'check_violation';
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

  -- avisos del outbox que pudieran quedar (p. ej. de un end_customer ya anonimizado antes): se borran todos
  delete from public.notification_outbox o where o.tenant_id = p_tenant_id;
  get diagnostics v_outbox = row_count;

  update public.professionals p
     set name = c_nombre_anonimo
   where p.tenant_id = p_tenant_id and p.name <> c_nombre_anonimo;
  get diagnostics v_profesionales = row_count;

  perform public.audit_log_append(p_tenant_id, 'system', v_actor_id, 'tenant_purge_step', 'tenants', p_tenant_id,
    jsonb_build_object('step', 'anonymize_end_customers', 'count', v_n,
                       'professionals_anonymized', v_profesionales,
                       'outbox_deleted', v_outbox));

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
  -- y el email y nombre de invitación pendiente (PII) de los que nunca vincularon cuenta
  update public.tenant_users tu set pending_invite_email = null, pending_invite_name = null
   where tu.tenant_id = p_tenant_id
     and (tu.pending_invite_email is not null or tu.pending_invite_name is not null);
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
