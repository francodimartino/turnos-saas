-- Canales del cliente (§2.12, §2.10): una persona es una ficha de end_customers con varios canales
-- verificados debajo (email, Telegram, WhatsApp). Migración no destructiva: tabla nueva, columna
-- email nueva y copia de los datos actuales. channel y channel_user_id de end_customers NO se tocan.

-- ---------------------------------------------------------------------------
-- end_customers.email
-- ---------------------------------------------------------------------------
alter table public.end_customers add column if not exists email text null;

alter table public.end_customers
  add constraint end_customers_email_formato check (
    email is null
    or (length(email) between 3 and 254 and email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
  );

-- la unión con una ficha existente se hace por email verificado, sin distinguir mayúsculas
create unique index end_customers_tenant_email_uidx
  on public.end_customers (tenant_id, lower(email))
  where email is not null;

-- ---------------------------------------------------------------------------
-- canales_cliente
-- ---------------------------------------------------------------------------
create table public.canales_cliente (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null,
  end_customer_id uuid not null,
  -- 'telegram' | 'email' | 'whatsapp'
  canal text not null check (canal in ('telegram', 'email', 'whatsapp')),
  -- telegram_user_id, email en minúsculas o wa_id
  identificador text not null check (length(identificador) between 1 and 320),
  -- null = declarado pero todavía no verificado
  verificado_at timestamptz null,
  created_at timestamptz not null default now(),
  -- la misma persona en dos negocios son dos fichas y dos filas: la unicidad es por tenant
  unique (tenant_id, canal, identificador),
  -- el email se guarda normalizado para que la unicidad no distinga mayúsculas
  check (canal <> 'email' or identificador = lower(identificador)),
  -- el canal solo puede colgar de una ficha del mismo tenant
  constraint canales_cliente_tenant_end_customer_fkey
    foreign key (tenant_id, end_customer_id)
    references public.end_customers (tenant_id, id)
    on delete cascade
);

create index canales_cliente_end_customer_idx on public.canales_cliente (tenant_id, end_customer_id);

alter table public.canales_cliente enable row level security;

create policy tenant_isolation on public.canales_cliente
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

-- Grants exactos. Los roles de la web y del enviador se crean y se autorizan aparte.
grant select, insert on public.canales_cliente to bot_service;
grant update (verificado_at) on public.canales_cliente to bot_service;
grant select, insert on public.canales_cliente to dashboard_service;
-- unir dos fichas mueve los canales de una a la otra
grant update (end_customer_id, verificado_at) on public.canales_cliente to dashboard_service;
grant select on public.canales_cliente to platform_admin_readonly;

-- ---------------------------------------------------------------------------
-- Copia de los canales actuales: cada (channel, channel_user_id) pasa como canal verificado.
-- Las fichas anonimizadas (sin identificador en claro) no tienen nada que copiar.
-- Re-ejecutable: lo que ya está copiado no se duplica.
-- ---------------------------------------------------------------------------
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at, created_at)
select tenant_id, id, channel, channel_user_id, created_at, created_at
  from public.end_customers
 where channel is not null and channel_user_id is not null
on conflict (tenant_id, canal, identificador) do nothing;
