-- Migración de plataforma: configuración de los avisos al negocio y canal email en notifications_log (§2.12, §2.10).
--
--   1. avisos_negocio_config: por miembro del staff y por canal, quién recibe los avisos del negocio
--      (reserva, cancelación, movimiento, pedido de aprobación). Una fila por (tenant, miembro).
--      Regla por defecto, que rige para quien NO tiene fila: reciben el owner y quienes tienen el permiso
--      approve_bookings. Una fila reemplaza al default de ese miembro (recibe_avisos y canal_email la deciden).
--      canal_whatsapp existe pero queda en falso: todavía no hay envío por WhatsApp y la base no admite
--      activarlo. Se habilita con una migración posterior cuando el canal exista.
--      Regla de seguridad del negocio: un cambio no puede dejarlo sin ningún destinatario de email.
--      El chequeo es un trigger de restricción (inmediato; quien intercambia destinatarios en una misma
--      transacción puede usar set constraints ... deferred).
--   2. notifications_log.channel admite 'email'. record_notification_attempt no valida el canal por su cuenta
--      (lo hace el CHECK de la tabla), así que acepta 'email' sin cambios y un canal desconocido sigue siendo
--      check_violation.
--
-- Grants: dashboard_service SELECT/INSERT/UPDATE por columna, sin DELETE (volver al default = poner los
-- valores del default, no borrar); platform_admin_readonly SELECT. Sin funciones SECURITY DEFINER nuevas.

create table public.avisos_negocio_config (
  tenant_id uuid not null,
  tenant_user_id uuid not null,
  recibe_avisos boolean not null default true,
  canal_email boolean not null default true,
  -- WhatsApp todavía no envía: la base lo mantiene en falso
  canal_whatsapp boolean not null default false,
  primary key (tenant_id, tenant_user_id),
  foreign key (tenant_id, tenant_user_id) references public.tenant_users (tenant_id, id),
  constraint avisos_negocio_config_whatsapp_deshabilitado check (not canal_whatsapp)
);

comment on table public.avisos_negocio_config is
  'Quién recibe los avisos al negocio y por qué canales. Una fila por miembro del staff. Sin fila rige el default: reciben el owner y quienes tienen el permiso approve_bookings, por email. canal_whatsapp queda en falso y sin efecto hasta que exista el envío por WhatsApp. Un cambio no puede dejar al negocio sin ningún destinatario de email.';

alter table public.avisos_negocio_config enable row level security;

create policy tenant_isolation on public.avisos_negocio_config
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

-- Cantidad de destinatarios de email que tiene un negocio con la configuración actual (default incluido).
-- Corre como invocador: ve solo lo que su rol y su tenant dejan ver.
create function public.avisos_negocio_destinatarios_email(p_tenant_id uuid)
returns integer
language sql
stable
set search_path = ''
as $$
  select count(*)::integer
    from public.tenant_users tu
    left join public.avisos_negocio_config c
      on c.tenant_id = tu.tenant_id and c.tenant_user_id = tu.id
   where tu.tenant_id = p_tenant_id
     and tu.is_active
     and case
           when c.tenant_user_id is null then
             tu.is_owner or exists (
               select 1 from public.tenant_user_permissions p
                where p.tenant_user_id = tu.id and p.permission_key = 'approve_bookings')
           else c.recibe_avisos and c.canal_email
         end
$$;

revoke all on function public.avisos_negocio_destinatarios_email(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.avisos_negocio_destinatarios_email(uuid) to dashboard_service;

create function public.avisos_negocio_exigir_destinatario()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if public.avisos_negocio_destinatarios_email(new.tenant_id) < 1 then
    raise exception 'el negocio no puede quedar sin ningún destinatario de avisos por email'
      using errcode = 'check_violation';
  end if;
  return null;
end
$$;

revoke all on function public.avisos_negocio_exigir_destinatario()
  from public, anon, authenticated, service_role;

create constraint trigger avisos_negocio_exigir_destinatario
  after insert or update on public.avisos_negocio_config
  deferrable initially immediate
  for each row execute function public.avisos_negocio_exigir_destinatario();

grant select (tenant_id, tenant_user_id, recibe_avisos, canal_email, canal_whatsapp)
  on public.avisos_negocio_config to dashboard_service, platform_admin_readonly;
grant insert (tenant_id, tenant_user_id, recibe_avisos, canal_email, canal_whatsapp)
  on public.avisos_negocio_config to dashboard_service;
grant update (recibe_avisos, canal_email, canal_whatsapp)
  on public.avisos_negocio_config to dashboard_service;

-- Canal email en el registro de avisos.
alter table public.notifications_log drop constraint notifications_log_channel_check;
alter table public.notifications_log
  add constraint notifications_log_channel_check check (channel in ('telegram', 'whatsapp', 'email'));
