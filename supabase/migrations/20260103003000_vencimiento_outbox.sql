-- Reescaneo del outbox por el job de vencimiento (§6.4).
--
-- 1) expire_pending_bookings() encola, EN LA MISMA SENTENCIA que cancela el turno, el aviso de vencimiento en
--    notification_outbox (plantilla 'turno_vencido', params {booking_id, inicio}) para cada turno con notify = true.
--    Así, si el job muere después de vencer y antes de avisar, el aviso no se pierde: la fila queda 'pending' y el
--    reescaneo de la corrida siguiente la envía. Primer intento a los 5 minutos (ventana del envío inmediato del
--    propio job, que cierra la fila al terminar).
--    Interfaz: columnas anteriores intactas (mismo nombre y orden) y una nueva AL FINAL:
--      outbox_id uuid  -- id de la fila encolada; NULL si notify = false (nada que avisar)
--    release_expired_pending_overlaps (la usa el bot) NO cambia: sus avisos siguen yendo por liberados.py.
--    Cambiar el tipo de retorno obliga a drop + create; los permisos se re-otorgan igual (solo scheduler_job).
--
-- 2) resolve_outbox_delivery(p_outbox_id): lo que el job necesita para armar y enviar el aviso de una fila ya
--    reclamada (scheduler_job no tiene grants sobre ninguna tabla). Devuelve el destinatario (identificador del
--    chat, NULL si el cliente fue anonimizado), el estado del tenant y los datos del turno (servicio, profesional,
--    inicio, vencimiento). Solo responde por filas del outbox en estado 'pending'; sin fila o sin turno, ninguna
--    fila. Solo EXECUTE para scheduler_job.

drop function public.expire_pending_bookings();

create function public.expire_pending_bookings()
returns table (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean,
               end_customer_id uuid, starts_at timestamptz, service_name text, outbox_id uuid)
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
    returning b.tenant_id, b.id, b.end_customer_id, b.service_id, lower(b.time_range) as inicio
  ),
  destinos as (
    select v.tenant_id as tenant_id,
           v.id as booking_id,
           v.end_customer_id as end_customer_id,
           v.inicio as inicio,
           s.name as service_name,
           ec.channel as channel,
           ec.channel_user_id as channel_user_id,
           (ec.deleted_at is null) as vigente,
           n.notificar as notificar
      from vencidos v
      join public.tenants t on t.id = v.tenant_id
      join public.services s on s.tenant_id = v.tenant_id and s.id = v.service_id
      left join public.end_customers ec
        on ec.tenant_id = v.tenant_id and ec.id = v.end_customer_id
     cross join lateral (
       select (t.status = 'active'
               and ec.channel is not null
               and ec.channel_user_id is not null) as notificar
     ) n
  ),
  encolados as (
    insert into public.notification_outbox
      (tenant_id, end_customer_id, channel, template, params, next_attempt_at)
    select d.tenant_id, d.end_customer_id, d.channel, 'turno_vencido',
           jsonb_build_object('booking_id', d.booking_id, 'inicio', d.inicio),
           now() + interval '300 seconds'
      from destinos d
     where d.notificar and d.vigente and d.channel in ('telegram', 'whatsapp')
    returning public.notification_outbox.id as outbox_id,
              (public.notification_outbox.params ->> 'booking_id')::uuid as booking_id
  )
  select d.tenant_id,
         d.booking_id,
         case when d.notificar then d.channel end,
         case when d.notificar then d.channel_user_id end,
         d.notificar,
         case when d.notificar then d.end_customer_id end,
         d.inicio,
         d.service_name,
         e.outbox_id
    from destinos d
    left join encolados e on e.booking_id = d.booking_id
$$;

revoke all on function public.expire_pending_bookings() from public, anon, authenticated, service_role;
grant execute on function public.expire_pending_bookings() to scheduler_job;

create function public.resolve_outbox_delivery(p_outbox_id uuid)
returns table (channel text, channel_user_id text, tenant_active boolean, booking_status text,
               service_name text, professional_name text, starts_at timestamptz, pending_expires_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select o.channel,
         case when ec.deleted_at is null then ec.channel_user_id end,
         (t.status = 'active'),
         b.status::text,
         s.name,
         p.name,
         lower(b.time_range),
         b.pending_expires_at
    from public.notification_outbox o
    join public.tenants t on t.id = o.tenant_id
    join public.end_customers ec on ec.tenant_id = o.tenant_id and ec.id = o.end_customer_id
    join public.bookings b
      on b.tenant_id = o.tenant_id
     and b.id = (o.params ->> 'booking_id')::uuid
    join public.services s on s.tenant_id = b.tenant_id and s.id = b.service_id
    left join public.professionals p on p.tenant_id = b.tenant_id and p.id = b.professional_id
   where o.id = p_outbox_id
     and o.status = 'pending'
$$;

revoke all on function public.resolve_outbox_delivery(uuid) from public, anon, authenticated, service_role;
grant execute on function public.resolve_outbox_delivery(uuid) to scheduler_job;
