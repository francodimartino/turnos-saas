-- Migración de dominio 1: tablas de turnos (estética/peluquería), con checks, FKs compuestas,
-- exclusion constraint contra el doble-booking y triggers de invariantes.
-- Ver ARCHITECTURE.md §7 (nota de RLS) y §7.1, y §1.7.7.
--
-- Alcance: tablas, checks, índices y triggers. RLS, policies y grants van en la migración
-- siguiente; las funciones de vencimiento de pendientes, después; la conexión con payments,
-- en la migración que une ambos dominios.
--
-- Criterio de integridad (§1.7.7): toda columna enum-como-texto lleva check + not null.
-- Criterio de aislamiento (nota de RLS de §7): toda tabla con id propio y referenciada por otras
-- lleva unique (tenant_id, id); toda FK hacia otra tabla del tenant es compuesta
-- (tenant_id, x_id); y tenant_id de las tablas hijas lo fuerza un trigger BEFORE INSERT/UPDATE
-- copiándolo del padre, nunca la aplicación.
--
-- Decisiones sobre columnas que §7.1 no marca como not null: day_of_week, start_time, end_time,
-- duration_minutes, price y total_sessions son not null (un NULL escaparía a su check, §1.7.7);
-- bookings.time_range también rechaza rangos vacíos (un rango vacío no se superpone con nada y
-- el exclude no prevendría el doble-booking).

-- ---------------------------------------------------------------------------
-- professionals (§7.1)
-- ---------------------------------------------------------------------------
create table public.professionals (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id),
  name text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  -- opcional: la cuenta del profesional, para que apruebe sus turnos (modo 'approval')
  tenant_user_id uuid null unique,
  unique (tenant_id, id),
  constraint professionals_tenant_user_fkey
    foreign key (tenant_id, tenant_user_id)
    references public.tenant_users (tenant_id, id)
);

create index professionals_tenant_id_idx on public.professionals (tenant_id);

-- FK compuesta pendiente de la migración de plataforma 2: una integración por-profesional
-- (professional_id no nulo) solo puede apuntar a un profesional de su propio tenant.
-- Con professional_id null (integración de tenant) la FK no se chequea.
alter table public.tenant_integrations
  add constraint tenant_integrations_tenant_professional_fkey
  foreign key (tenant_id, professional_id)
  references public.professionals (tenant_id, id);

-- ---------------------------------------------------------------------------
-- services (§7.1)
-- ---------------------------------------------------------------------------
create table public.services (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id),
  name text not null,
  -- define el tamaño del hueco a buscar
  duration_minutes integer not null,
  price numeric not null,
  -- seña del servicio; obligatoria si algún professional_services lo usa en modo 'deposit'
  -- (trigger de más abajo). Sin cobro real en el piloto
  deposit_amount numeric null,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  -- un 0/negativo generaría un time_range vacío y el exclusion constraint de bookings
  -- dejaría de prevenir el doble-booking
  check (duration_minutes > 0),
  check (price >= 0),
  check (deposit_amount is null or deposit_amount >= 0)
);

create index services_tenant_id_idx on public.services (tenant_id);

-- ---------------------------------------------------------------------------
-- availability_rules (§7.1)
-- ---------------------------------------------------------------------------
create table public.availability_rules (
  id uuid primary key default gen_random_uuid(),
  -- desnormalizado desde professionals.tenant_id y forzado por trigger
  tenant_id uuid not null,
  professional_id uuid not null,
  -- 0 = domingo ... 6 = sábado
  day_of_week integer not null,
  start_time time not null,
  end_time time not null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  constraint availability_rules_tenant_professional_fkey
    foreign key (tenant_id, professional_id)
    references public.professionals (tenant_id, id),
  -- una ventana invertida rompería el cálculo de huecos disponibles
  check (end_time > start_time),
  check (day_of_week between 0 and 6)
);

create index availability_rules_tenant_id_idx on public.availability_rules (tenant_id);
create index availability_rules_professional_idx on public.availability_rules (professional_id);

-- ---------------------------------------------------------------------------
-- availability_blocks (§7.1): feriados, vacaciones, ausencias puntuales
-- ---------------------------------------------------------------------------
create table public.availability_blocks (
  id uuid primary key default gen_random_uuid(),
  -- forzado por trigger desde professionals cuando professional_id no es null
  tenant_id uuid not null,
  -- null = bloquea todo el local
  professional_id uuid null,
  time_range tstzrange not null,
  reason text null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  -- con professional_id null la FK no se chequea (comportamiento correcto), pero tenant_id
  -- sigue siendo not null y con FK simple hacia tenants
  constraint availability_blocks_tenant_fkey
    foreign key (tenant_id) references public.tenants (id),
  constraint availability_blocks_tenant_professional_fkey
    foreign key (tenant_id, professional_id)
    references public.professionals (tenant_id, id),
  check (not isempty(time_range))
);

create index availability_blocks_tenant_id_idx on public.availability_blocks (tenant_id);
create index availability_blocks_time_range_idx on public.availability_blocks using gist (time_range);

-- ---------------------------------------------------------------------------
-- professional_services (§7.1): qué servicios hace cada profesional y cómo se confirman
-- ---------------------------------------------------------------------------
create table public.professional_services (
  tenant_id uuid not null,
  professional_id uuid not null,
  service_id uuid not null,
  -- 'auto' | 'approval' | 'deposit'
  confirmation_mode text not null default 'auto'
    check (confirmation_mode in ('auto', 'approval', 'deposit')),
  primary key (professional_id, service_id),
  constraint professional_services_tenant_professional_fkey
    foreign key (tenant_id, professional_id)
    references public.professionals (tenant_id, id),
  constraint professional_services_tenant_service_fkey
    foreign key (tenant_id, service_id)
    references public.services (tenant_id, id)
);

create index professional_services_tenant_id_idx on public.professional_services (tenant_id);
create index professional_services_service_idx on public.professional_services (service_id);

-- ---------------------------------------------------------------------------
-- booking_packs (§7.1)
-- ---------------------------------------------------------------------------
create table public.booking_packs (
  id uuid primary key default gen_random_uuid(),
  -- desnormalizado desde end_customers.tenant_id y forzado por trigger
  tenant_id uuid not null,
  end_customer_id uuid not null,
  service_id uuid not null,
  total_sessions integer not null,
  used_sessions integer not null default 0,
  -- 'purchase' (pago real) | 'manual_grant' (asignado por el tenant)
  origin text not null check (origin in ('purchase', 'manual_grant')),
  -- 'not_required' (manual_grant) | 'pending' | 'paid' (purchase, vía payments): sin esto, un
  -- pack comprado y no pagado podría redimirse igual
  payment_status text not null default 'not_required'
    check (payment_status in ('not_required', 'pending', 'paid')),
  expires_at timestamptz null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  constraint booking_packs_tenant_end_customer_fkey
    foreign key (tenant_id, end_customer_id)
    references public.end_customers (tenant_id, id),
  constraint booking_packs_tenant_service_fkey
    foreign key (tenant_id, service_id)
    references public.services (tenant_id, id),
  -- evita sobre-redimir el pack por un bug o una race condition
  check (used_sessions <= total_sessions),
  check (total_sessions > 0),
  check (used_sessions >= 0),
  -- bidireccional: evita tanto "regalado pero pendiente de pago" como "comprado pero marcado
  -- como no-requiere-pago"
  check (
    (origin = 'manual_grant' and payment_status = 'not_required')
    or (origin = 'purchase' and payment_status in ('pending', 'paid'))
  )
);

create index booking_packs_tenant_id_idx on public.booking_packs (tenant_id);
create index booking_packs_end_customer_idx on public.booking_packs (end_customer_id);

-- ---------------------------------------------------------------------------
-- bookings (§7.1)
-- ---------------------------------------------------------------------------
create table public.bookings (
  id uuid primary key default gen_random_uuid(),
  -- desnormalizado desde professionals.tenant_id y forzado por trigger
  tenant_id uuid not null,
  end_customer_id uuid not null,
  service_id uuid not null,
  professional_id uuid not null,
  -- derivado de start_at + duration_minutes del servicio
  time_range tstzrange not null,
  -- 'pending' | 'confirmed' | 'cancelled' | 'completed'. not null: un NULL escaparía al exclude
  status text not null default 'pending'
    check (status in ('pending', 'confirmed', 'cancelled', 'completed')),
  -- 'not_required' | 'pending' | 'paid'
  deposit_status text not null default 'not_required'
    check (deposit_status in ('not_required', 'pending', 'paid')),
  -- si se pagó con una sesión de un pack. Al pasar a 'cancelled' la sesión la devuelve UN ÚNICO
  -- trigger AFTER UPDATE de esta tabla (más abajo): ninguna otra función ni backend la devuelve
  booking_pack_id uuid null,
  -- solo en 'pending': menor entre now() + booking_approval_timeout_hours y el inicio del turno
  pending_expires_at timestamptz null,
  google_calendar_event_id text null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  constraint bookings_tenant_end_customer_fkey
    foreign key (tenant_id, end_customer_id)
    references public.end_customers (tenant_id, id),
  constraint bookings_tenant_service_fkey
    foreign key (tenant_id, service_id)
    references public.services (tenant_id, id),
  constraint bookings_tenant_professional_fkey
    foreign key (tenant_id, professional_id)
    references public.professionals (tenant_id, id),
  constraint bookings_tenant_pack_fkey
    foreign key (tenant_id, booking_pack_id)
    references public.booking_packs (tenant_id, id),
  -- Postgres rechaza físicamente un booking que se superponga con otro vigente del mismo
  -- profesional, incluso ante dos requests simultáneos. Usa btree_gist (schema extensions)
  constraint bookings_sin_doble_booking
    exclude using gist (professional_id with =, time_range with &&)
    where (status <> 'cancelled'),
  check ((status = 'pending') = (pending_expires_at is not null)),
  check (not isempty(time_range))
);

create index bookings_tenant_id_idx on public.bookings (tenant_id);
create index bookings_end_customer_idx on public.bookings (end_customer_id);
create index bookings_booking_pack_idx on public.bookings (booking_pack_id)
  where booking_pack_id is not null;
-- el Cron Job de vencimiento (§5.4.1) busca pendientes vencidos
create index bookings_pending_expires_idx on public.bookings (pending_expires_at)
  where status = 'pending';

-- ---------------------------------------------------------------------------
-- tenant_id forzado por trigger desde el padre (nota de RLS de §7)
-- Corren como invocador: si el padre no es visible para quien escribe, falla.
-- ---------------------------------------------------------------------------

-- availability_rules, availability_blocks, professional_services y bookings: desde professionals.
-- Con professional_id null (bloqueo de todo el local) no hay padre del que copiar: se respeta el
-- valor recibido, que valida RLS en la migración siguiente.
create function public.forzar_tenant_id_desde_profesional()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_tenant_id uuid;
begin
  if new.professional_id is null then
    return new;
  end if;

  select p.tenant_id into v_tenant_id
  from public.professionals p
  where p.id = new.professional_id;

  if v_tenant_id is null then
    raise exception 'professional % inexistente', new.professional_id
      using errcode = 'foreign_key_violation';
  end if;

  new.tenant_id := v_tenant_id;
  return new;
end
$$;

create trigger availability_rules_forzar_tenant_id
  before insert or update on public.availability_rules
  for each row execute function public.forzar_tenant_id_desde_profesional();

create trigger availability_blocks_forzar_tenant_id
  before insert or update on public.availability_blocks
  for each row execute function public.forzar_tenant_id_desde_profesional();

create trigger professional_services_forzar_tenant_id
  before insert or update on public.professional_services
  for each row execute function public.forzar_tenant_id_desde_profesional();

create trigger bookings_forzar_tenant_id
  before insert or update on public.bookings
  for each row execute function public.forzar_tenant_id_desde_profesional();

-- booking_packs: desde end_customers
create function public.booking_packs_forzar_tenant_id()
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

create trigger booking_packs_forzar_tenant_id
  before insert or update on public.booking_packs
  for each row execute function public.booking_packs_forzar_tenant_id();

-- ---------------------------------------------------------------------------
-- bookings: un pack solo puede redimirse con origin = 'manual_grant' o payment_status = 'paid'
-- (§7.1). Se valida al asignar el pack; cancelar después, con el pack ya en otro estado, igual
-- devuelve la sesión. Su nombre ordena después de bookings_forzar_tenant_id: usa new.tenant_id.
-- ---------------------------------------------------------------------------
create function public.bookings_validar_pack()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_origin text;
  v_payment_status text;
begin
  if new.booking_pack_id is null then
    return new;
  end if;
  if tg_op = 'UPDATE' and new.booking_pack_id is not distinct from old.booking_pack_id then
    return new;
  end if;

  select bp.origin, bp.payment_status into v_origin, v_payment_status
  from public.booking_packs bp
  where bp.id = new.booking_pack_id and bp.tenant_id = new.tenant_id;

  if v_origin is null then
    raise exception 'booking_pack % inexistente', new.booking_pack_id
      using errcode = 'foreign_key_violation';
  end if;

  if not (v_origin = 'manual_grant' or v_payment_status = 'paid') then
    raise exception 'el pack % no se puede redimir: es una compra con pago %', new.booking_pack_id, v_payment_status
      using errcode = 'check_violation';
  end if;

  return new;
end
$$;

create trigger bookings_validar_pack
  before insert or update of booking_pack_id on public.bookings
  for each row execute function public.bookings_validar_pack();

-- ---------------------------------------------------------------------------
-- bookings: UN ÚNICO trigger AFTER UPDATE devuelve la sesión del pack al pasar a 'cancelled'
-- (used_sessions -= 1), fila por fila: varios bookings del mismo pack en un solo UPDATE
-- devuelven una sesión cada uno. Ninguna función ni backend la devuelve por su cuenta: así no
-- se devuelve dos veces. Solo dispara en la transición hacia 'cancelled'.
-- ---------------------------------------------------------------------------
create function public.bookings_devolver_sesion_pack()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  update public.booking_packs
     set used_sessions = used_sessions - 1
   where id = new.booking_pack_id and tenant_id = new.tenant_id;
  return null;
end
$$;

create trigger bookings_devolver_sesion_pack
  after update on public.bookings
  for each row
  when (old.status <> 'cancelled' and new.status = 'cancelled' and new.booking_pack_id is not null)
  execute function public.bookings_devolver_sesion_pack();

-- ---------------------------------------------------------------------------
-- Seña obligatoria en modo 'deposit' (§7.1): professional_services no puede usar 'deposit' sobre
-- un servicio sin deposit_amount, ni se puede quitar deposit_amount mientras alguien lo use.
-- ---------------------------------------------------------------------------
create function public.professional_services_validar_senia()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_deposit_amount numeric;
begin
  if new.confirmation_mode <> 'deposit' then
    return new;
  end if;

  select s.deposit_amount into v_deposit_amount
  from public.services s
  where s.id = new.service_id and s.tenant_id = new.tenant_id;

  if v_deposit_amount is null then
    raise exception 'el servicio % no tiene deposit_amount: no admite el modo deposit', new.service_id
      using errcode = 'check_violation';
  end if;

  return new;
end
$$;

-- su nombre ordena después de professional_services_forzar_tenant_id: usa new.tenant_id
create trigger professional_services_validar_senia
  before insert or update on public.professional_services
  for each row execute function public.professional_services_validar_senia();

create function public.services_proteger_senia()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if exists (
    select 1
    from public.professional_services ps
    where ps.tenant_id = new.tenant_id
      and ps.service_id = new.id
      and ps.confirmation_mode = 'deposit'
  ) then
    raise exception 'el servicio % lo usa algún profesional en modo deposit: no se puede quitar deposit_amount', new.id
      using errcode = 'check_violation';
  end if;

  return new;
end
$$;

create trigger services_proteger_senia
  before update of deposit_amount on public.services
  for each row
  when (new.deposit_amount is null and old.deposit_amount is not null)
  execute function public.services_proteger_senia();

-- ---------------------------------------------------------------------------
-- Baja de un profesional (§7.1): al pasar is_active = false se elimina de Vault el secret de su
-- integración de google_calendar — si no, el sync seguiría empujando eventos al calendario
-- personal de alguien que ya no trabaja en el tenant. La integración queda sin secret_id.
-- SECURITY DEFINER: quien desactiva al profesional no tiene (ni debe tener) acceso a Vault.
-- Solo toca integraciones de ese profesional y de su tenant.
-- ---------------------------------------------------------------------------
create function public.professionals_baja_secret_calendar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_secret_ids uuid[];
begin
  select array_agg(ti.secret_id) into v_secret_ids
  from public.tenant_integrations ti
  where ti.tenant_id = new.tenant_id
    and ti.professional_id = new.id
    and ti.integration_type = 'google_calendar'
    and ti.secret_id is not null;

  if v_secret_ids is null then
    return null;
  end if;

  update public.tenant_integrations
     set secret_id = null
   where tenant_id = new.tenant_id
     and professional_id = new.id
     and integration_type = 'google_calendar';

  delete from vault.secrets where id = any (v_secret_ids);
  return null;
end
$$;

revoke all on function public.professionals_baja_secret_calendar() from public;

create trigger professionals_baja_secret_calendar
  after update of is_active on public.professionals
  for each row
  when (old.is_active and not new.is_active)
  execute function public.professionals_baja_secret_calendar();
