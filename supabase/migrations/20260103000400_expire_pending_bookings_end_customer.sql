-- Migración de dominio: expire_pending_bookings() devuelve además end_customer_id (ARCHITECTURE.md
-- §5.4.1). El Cron Job lo necesita para registrar el aviso en notifications_log vía
-- record_notification_attempt, que exige el cliente; scheduler_job no tiene SELECT sobre tablas.
--
-- Cambiar el tipo de retorno obliga a drop + create de la función. No se pierden datos (la función
-- no almacena nada) y no hay objetos dependientes de ella; los grants se re-otorgan abajo.
-- Misma semántica que antes. end_customer_id va en null cuando notify es false, igual que
-- channel/channel_user_id (mínimo de dato personal necesario).

drop function public.expire_pending_bookings();

create function public.expire_pending_bookings()
returns table (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean,
               end_customer_id uuid)
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
         n.notificar,
         case when n.notificar then v.end_customer_id end
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
