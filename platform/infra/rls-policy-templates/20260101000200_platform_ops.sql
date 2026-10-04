-- Migración de plataforma 2: tablas operativas (integraciones, clientes finales, auditoría,
-- mensajes entrantes, sesiones del bot, log de notificaciones y pagos).
-- Ver ARCHITECTURE.md §1.7.7, §2.3, §2.6, §2.10, la nota de RLS de §7 y §4.2.
--
-- Alcance: tablas, checks, índices y triggers de invariantes. RLS, policies y grants van en la
-- migración siguiente; las funciones SECURITY DEFINER de negocio, después. Las ramas de dominio
-- del trigger de payments (bookings, booking_packs) pertenecen a las migraciones del producto.
--
-- Criterio de integridad (§1.7.7): toda columna enum-como-texto lleva check + not null.
-- Criterio de aislamiento (nota de RLS de §7): toda FK entre tablas del tenant es compuesta
-- (tenant_id, x_id) contra un unique (tenant_id, id) del padre, y tenant_id de las tablas hijas
-- lo fuerza un trigger, nunca la aplicación.

-- ---------------------------------------------------------------------------
-- tenant_integrations (§2.6, §2.10)
-- ---------------------------------------------------------------------------
create table public.tenant_integrations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id),
  -- 'telegram_bot' (dev/test) | 'whatsapp_business' (producción) | 'google_calendar' | 'mercadopago'
  integration_type text not null
    check (integration_type in ('telegram_bot', 'whatsapp_business', 'google_calendar', 'mercadopago')),
  -- solo para integraciones por-profesional (ej. google_calendar); la FK compuesta hacia
  -- professionals (tenant_id, id) la agrega la migración del dominio, que crea esa tabla
  professional_id uuid null,
  -- referencia a Vault, nunca la credencial en claro. unique: ni siquiera entre tenants distintos
  -- pueden dos filas compartir el mismo secret
  secret_id uuid null unique,
  -- datos NO secretos: 'webhook_key' (telegram_bot), 'phone_number_id' (whatsapp_business)
  config jsonb not null default '{}' check (jsonb_typeof(config) = 'object'),
  created_at timestamptz not null default now(),
  -- clave objetivo de FKs compuestas de tablas hijas
  unique (tenant_id, id)
);

create index tenant_integrations_tenant_id_idx on public.tenant_integrations (tenant_id);

-- un unique normal no protege filas con professional_id NULL entre sí (NULL nunca es igual a NULL)
create unique index tenant_integrations_tenant_tipo_uidx
  on public.tenant_integrations (tenant_id, integration_type)
  where professional_id is null;
create unique index tenant_integrations_tenant_tipo_profesional_uidx
  on public.tenant_integrations (tenant_id, integration_type, professional_id)
  where professional_id is not null;

-- identificadores de resolución de tenant: únicos globales (cross-tenant), §2.6
create unique index tenant_integrations_webhook_key_uidx
  on public.tenant_integrations ((config ->> 'webhook_key'));
create unique index tenant_integrations_phone_number_id_uidx
  on public.tenant_integrations ((config ->> 'phone_number_id'));

-- ---------------------------------------------------------------------------
-- end_customers (§2.3, §2.8, §2.10)
-- ---------------------------------------------------------------------------
create table public.end_customers (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id),
  -- null en altas manuales todavía no vinculadas a un canal
  channel text null check (channel in ('telegram', 'whatsapp')),
  -- telegram_user_id / wa_id; null en altas manuales o tras anonimizar
  channel_user_id text null,
  -- solo en filas anonimizadas (§2.3); nunca lo calcula la aplicación
  channel_user_hash text null,
  -- phone en E.164 (§2.8). Nunca not null: anonymize_end_customer los pone en null
  phone text null,
  name text null,
  extra_data jsonb not null default '{}' check (jsonb_typeof(extra_data) = 'object'),
  deleted_at timestamptz null,
  created_at timestamptz not null default now(),
  -- varias filas con channel_user_id null no chocan (NULL nunca es igual a NULL): es lo correcto
  unique (tenant_id, channel, channel_user_id),
  unique (tenant_id, id),
  -- hay canal si y solo si hay un identificador (en claro o en hash)
  check ((channel is null) = (channel_user_id is null and channel_user_hash is null))
);

-- deduplicación de §2.8
create index end_customers_tenant_phone_idx on public.end_customers (tenant_id, phone);
-- recontacto de §2.3
create index end_customers_tenant_channel_hash_idx
  on public.end_customers (tenant_id, channel, channel_user_hash);

-- ---------------------------------------------------------------------------
-- audit_log (§2.10): solo-agregar
-- ---------------------------------------------------------------------------
create table public.audit_log (
  id uuid primary key default gen_random_uuid(),
  -- nullable: acciones de platform_admin sin tenant único (ej. editar platform_settings)
  tenant_id uuid null references public.tenants (id),
  actor_type text not null check (actor_type in ('staff', 'bot', 'system', 'platform_admin')),
  actor_id uuid null,
  action text not null,
  entity text null,
  entity_id uuid null,
  metadata jsonb null,
  created_at timestamptz not null default now()
);

create index audit_log_tenant_id_idx on public.audit_log (tenant_id);

-- Ningún rol modifica ni borra: el trigger lo hace cumplir también para el dueño de la tabla.
-- Se escribe únicamente vía write_audit_log() (SECURITY DEFINER, migración de funciones).
create function public.audit_log_solo_agregar()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'audit_log es solo-agregar: % no está permitido', tg_op
    using errcode = 'insufficient_privilege';
end
$$;

create trigger audit_log_solo_agregar_row
  before update or delete on public.audit_log
  for each row execute function public.audit_log_solo_agregar();

create trigger audit_log_solo_agregar_truncate
  before truncate on public.audit_log
  for each statement execute function public.audit_log_solo_agregar();

-- ---------------------------------------------------------------------------
-- inbound_messages (§2.6): deduplicación de reentregas
-- ---------------------------------------------------------------------------
create table public.inbound_messages (
  tenant_id uuid not null references public.tenants (id),
  channel text not null check (channel in ('telegram', 'whatsapp')),
  -- update_id (Telegram, único por bot) / message id (WhatsApp)
  external_message_id text not null,
  received_at timestamptz not null default now(),
  primary key (tenant_id, channel, external_message_id)
);

-- el Cron Job diario borra filas de más de 7 días
create index inbound_messages_received_at_idx on public.inbound_messages (received_at);

-- ---------------------------------------------------------------------------
-- bot_sessions (§2.6): estado de la conversación
-- ---------------------------------------------------------------------------
create table public.bot_sessions (
  tenant_id uuid not null references public.tenants (id),
  channel text not null check (channel in ('telegram', 'whatsapp')),
  channel_user_id text not null,
  state jsonb not null default '{}' check (jsonb_typeof(state) = 'object'),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, channel, channel_user_id)
);

-- descarte de sesiones sin actividad por 1 hora
create index bot_sessions_updated_at_idx on public.bot_sessions (updated_at);

-- ---------------------------------------------------------------------------
-- notifications_log (§2.10, §6.4)
-- ---------------------------------------------------------------------------
create table public.notifications_log (
  id uuid primary key default gen_random_uuid(),
  -- desnormalizado desde end_customers.tenant_id y forzado por trigger
  tenant_id uuid not null,
  end_customer_id uuid not null,
  channel text not null check (channel in ('telegram', 'whatsapp')),
  template text null,
  -- 'pending' (se crea así en el primer intento) | 'sent' | 'failed' (tras agotar reintentos)
  status text not null default 'pending' check (status in ('pending', 'sent', 'failed')),
  retry_count integer not null default 0 check (retry_count >= 0),
  -- solo código/mensaje de error del proveedor, NUNCA el texto enviado
  last_error text null,
  sent_at timestamptz null,
  unique (tenant_id, id),
  constraint notifications_log_tenant_end_customer_fkey
    foreign key (tenant_id, end_customer_id)
    references public.end_customers (tenant_id, id)
);

create index notifications_log_tenant_id_idx on public.notifications_log (tenant_id);
create index notifications_log_end_customer_idx on public.notifications_log (end_customer_id);

-- tenant_id se copia desde end_customers, ignorando lo que mande la aplicación.
-- Corre como invocador: si el end_customer no es visible para quien escribe, falla.
create function public.notifications_log_forzar_tenant_id()
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

create trigger notifications_log_forzar_tenant_id
  before insert or update on public.notifications_log
  for each row execute function public.notifications_log_forzar_tenant_id();

-- ---------------------------------------------------------------------------
-- payments (§2.10): genérica, sin ramas de dominio
-- ---------------------------------------------------------------------------
create table public.payments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id),
  payable_type text not null check (payable_type in ('booking_deposit', 'booking_pack')),
  -- id de bookings/booking_packs; polimórfico, sin FK
  payable_id uuid null,
  provider text not null default 'mercadopago' check (provider in ('mercadopago')),
  provider_payment_id text null,
  amount numeric not null check (amount >= 0),
  status text not null check (status in ('pending', 'approved', 'rejected', 'refunded')),
  -- payload crudo del webhook (respaldo ante reclamos); al anonimizar se quitan datos del pagador
  webhook_payload jsonb null,
  created_at timestamptz not null default now(),
  -- idempotencia: un webhook reintentado no puede procesarse dos veces
  unique (provider, provider_payment_id),
  unique (tenant_id, id)
);

create index payments_tenant_id_idx on public.payments (tenant_id);

-- Como payable_type/payable_id es polimórfico, resolver el tenant_id real exige una rama por
-- tipo (bookings, booking_packs). Esas tablas no existen en la plataforma: las ramas las agrega,
-- con create or replace de esta función, la migración del producto que conecta ambos dominios
-- (§4.2). Acá el trigger solo garantiza la parte que no depende del dominio: un pago nunca se
-- mueve de tenant.
create function public.payments_forzar_tenant_id()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' and new.tenant_id is distinct from old.tenant_id then
    raise exception 'payments.tenant_id no se puede modificar'
      using errcode = 'check_violation';
  end if;

  -- ramas por payable_type: ver comentario de la función
  return new;
end
$$;

create trigger payments_forzar_tenant_id
  before insert or update on public.payments
  for each row execute function public.payments_forzar_tenant_id();
