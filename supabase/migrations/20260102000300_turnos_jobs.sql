-- Migración de dominio 3: funciones del Cron Job de vencimiento de turnos y sesiones
-- (ARCHITECTURE.md §5.4.1, §3.2). Migración no destructiva: solo agrega funciones.
--
-- El rol scheduler_job no tiene grants sobre ninguna tabla: opera únicamente con EXECUTE en
-- funciones SECURITY DEFINER (search_path vacío). Todas parten sin EXECUTE (revoke explícito de
-- PUBLIC y de los roles de la API) y se otorgan solo a scheduler_job.
--
-- Decisión de diseño sobre notifications_log: §3.2 menciona INSERT/UPDATE directo para
-- scheduler_job. Para no darle grants sobre una tabla (el rol queda con cero acceso a tablas, igual
-- que purge_job) el registro del aviso al cliente pasa por record_notification_attempt, una función
-- SECURITY DEFINER que exige contexto de tenant y que el cliente sea de ese tenant.
-- find_integration y get_integration_secret ya tienen EXECUTE para scheduler_job (plataforma 2).

-- ---------------------------------------------------------------------------
-- expire_pending_bookings(): pasa a 'cancelled' los pending vencidos (libera el horario) y devuelve
-- a quién avisar. La sesión del pack la devuelve el trigger AFTER UPDATE de bookings (una sola vez,
-- porque 'cancelled' es estado final); el trigger de transiciones limpia pending_expires_at.
-- notify es false si el tenant no está 'active' o el cliente no tiene canal vinculado; en ese caso
-- channel/channel_user_id se devuelven en null (mínimo de dato personal necesario).
-- Idempotente: una segunda corrida no encuentra pending vencidos.
-- ---------------------------------------------------------------------------
create function public.expire_pending_bookings()
returns table (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean)
language sql
volatile
security definer
set search_path = ''
as $$
  with vencidos as (
    update public.bookings b
       set status = 'cancelled'
     where b.status = 'pending'
       and b.pending_expires_at <= now()
    returning b.tenant_id, b.id, b.end_customer_id
  )
  select v.tenant_id,
         v.id,
         case when n.notificar then ec.channel end,
         case when n.notificar then ec.channel_user_id end,
         n.notificar
    from vencidos v
    join public.tenants t on t.id = v.tenant_id
    left join public.end_customers ec
      on ec.tenant_id = v.tenant_id and ec.id = v.end_customer_id
   cross join lateral (
     select (t.status = 'active'
             and ec.channel is not null
             and ec.channel_user_id is not null) as notificar
   ) n
$$;

revoke all on function public.expire_pending_bookings() from public, anon, authenticated, service_role;
grant execute on function public.expire_pending_bookings() to scheduler_job;

-- ---------------------------------------------------------------------------
-- prune_bot_sessions(): borra las sesiones del bot sin actividad hace más de 1 hora (§2.6).
-- Devuelve la cantidad borrada.
-- ---------------------------------------------------------------------------
create function public.prune_bot_sessions()
returns integer
language sql
volatile
security definer
set search_path = ''
as $$
  with borradas as (
    delete from public.bot_sessions s
     where s.updated_at < now() - interval '1 hour'
    returning 1
  )
  select count(*)::integer from borradas
$$;

revoke all on function public.prune_bot_sessions() from public, anon, authenticated, service_role;
grant execute on function public.prune_bot_sessions() to scheduler_job;

-- ---------------------------------------------------------------------------
-- record_notification_attempt(): registra el aviso al cliente en notifications_log (§6.4).
-- Con p_notification_id null crea la fila (status 'pending' o el indicado); con id, actualiza
-- el intento de ese cliente. Exige contexto de tenant (app.current_tenant_id) y que el
-- end_customer sea de ese tenant: inexistente y ajeno dan el mismo error. 'sent' y 'failed' son
-- finales. p_last_error es solo código/mensaje del proveedor (nunca el texto enviado ni PII):
-- se trunca a 500 caracteres.
-- ---------------------------------------------------------------------------
create function public.record_notification_attempt(
  p_end_customer_id uuid,
  p_channel text,
  p_template text,
  p_status text,
  p_retry_count integer default 0,
  p_last_error text default null,
  p_notification_id uuid default null
)
returns uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_id uuid;
  v_status_actual text;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  if not exists (
    select 1 from public.end_customers ec
     where ec.id = p_end_customer_id and ec.tenant_id = v_ctx
  ) then
    raise exception 'cliente inexistente o de otro tenant' using errcode = 'insufficient_privilege';
  end if;

  if p_notification_id is null then
    insert into public.notifications_log
      (tenant_id, end_customer_id, channel, template, status, retry_count, last_error, sent_at)
    values
      (v_ctx, p_end_customer_id, p_channel, p_template, p_status, coalesce(p_retry_count, 0),
       left(p_last_error, 500), case when p_status = 'sent' then now() end)
    returning id into v_id;
    return v_id;
  end if;

  select nl.status into v_status_actual
    from public.notifications_log nl
   where nl.id = p_notification_id
     and nl.tenant_id = v_ctx
     and nl.end_customer_id = p_end_customer_id
   for update;

  if not found then
    raise exception 'notificación inexistente o de otro tenant' using errcode = 'insufficient_privilege';
  end if;

  if v_status_actual <> 'pending' then
    raise exception 'la notificación % ya está en estado final (%)', p_notification_id, v_status_actual
      using errcode = 'check_violation';
  end if;

  update public.notifications_log
     set status = p_status,
         retry_count = coalesce(p_retry_count, 0),
         last_error = left(p_last_error, 500),
         sent_at = case when p_status = 'sent' then now() end
   where id = p_notification_id and tenant_id = v_ctx;

  return p_notification_id;
end
$$;

revoke all on function public.record_notification_attempt(uuid, text, text, text, integer, text, uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.record_notification_attempt(uuid, text, text, text, integer, text, uuid)
  to scheduler_job;
