-- Web de reservas (§2.12): aceptación de la política de privacidad y tablas de sesiones y códigos de acceso.
-- Migración no destructiva: columnas nulas y tablas nuevas.
--
-- Ni el token de la cookie ni el código de 6 dígitos ni el email del pedido se guardan en claro: la aplicación
-- guarda solo su hash (SHA-256 o HMAC-SHA-256 en hexadecimal, 64 caracteres en minúsculas). Un código de 6 dígitos
-- sin clave secreta se adivina por fuerza bruta, así que su hash debe ser un HMAC con una clave que la base no ve.
-- Ningún rol de aplicación recibe grants sobre estas tablas: el rol de la web los recibirá cuando se cree.

-- ---------------------------------------------------------------------------
-- Aceptación de la política de privacidad: versión y fecha, las dos o ninguna
-- ---------------------------------------------------------------------------
alter table public.end_customers
  add column if not exists politica_version text null,
  add column if not exists politica_aceptada_at timestamptz null;

alter table public.end_customers
  add constraint end_customers_politica_version_largo check (
    politica_version is null or length(politica_version) between 1 and 50
  ),
  add constraint end_customers_politica_completa check (
    (politica_version is null) = (politica_aceptada_at is null)
  );

-- ---------------------------------------------------------------------------
-- sesiones_web: sesión de la web, revocable. En la cookie va el token; acá, solo su hash.
-- ---------------------------------------------------------------------------
create table public.sesiones_web (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null,
  end_customer_id uuid not null,
  token_hash text not null check (token_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  -- dura hasta 30 días desde que se crea
  expira_at timestamptz not null,
  revocada_at timestamptz null,
  check (expira_at > created_at and expira_at <= created_at + interval '30 days'),
  constraint sesiones_web_tenant_end_customer_fkey
    foreign key (tenant_id, end_customer_id)
    references public.end_customers (tenant_id, id)
    on delete cascade
);

-- la sesión se busca por el hash del token, antes de conocer el tenant: único en toda la tabla
create unique index sesiones_web_token_hash_uidx on public.sesiones_web (token_hash);
create index sesiones_web_end_customer_idx on public.sesiones_web (tenant_id, end_customer_id);

alter table public.sesiones_web enable row level security;

create policy tenant_isolation on public.sesiones_web
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

-- ---------------------------------------------------------------------------
-- codigos_web: código de 6 dígitos enviado por email. Vale 10 minutos y admite 5 intentos.
-- ---------------------------------------------------------------------------
create table public.codigos_web (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id),
  -- hash del email al que se pidió el código (el email no se guarda acá)
  email_hash text not null check (email_hash ~ '^[0-9a-f]{64}$'),
  codigo_hash text not null check (codigo_hash ~ '^[0-9a-f]{64}$'),
  intentos smallint not null default 0 check (intentos between 0 and 5),
  created_at timestamptz not null default now(),
  expira_at timestamptz not null,
  usado_at timestamptz null,
  -- hash de la IP de quien lo pidió, para el tope por IP; nunca la IP en claro
  ip_hash text null check (ip_hash is null or ip_hash ~ '^[0-9a-f]{64}$'),
  check (expira_at > created_at and expira_at <= created_at + interval '10 minutes')
);

-- topes por hora: códigos pedidos por email y por IP en la última hora
create index codigos_web_email_idx on public.codigos_web (tenant_id, email_hash, created_at desc);
create index codigos_web_ip_idx on public.codigos_web (tenant_id, ip_hash, created_at desc) where ip_hash is not null;

alter table public.codigos_web enable row level security;

create policy tenant_isolation on public.codigos_web
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

-- Sin grants para bot_service, dashboard_service ni platform_admin_readonly: son tablas de la web.
revoke all on public.sesiones_web, public.codigos_web from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Anonimización y purga: las sesiones y los códigos de la web no sobreviven al olvido.
-- Son triggers y no una redefinición de anonymize_end_customer_core / purge_tenant_data, así cubren cualquier
-- camino que anonimice una ficha o purgue un tenant. SECURITY DEFINER: quien anonimiza no tiene grants acá.
-- ---------------------------------------------------------------------------
create function public.borrar_sesiones_web_de_ficha_anonimizada()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.sesiones_web s
   where s.tenant_id = new.tenant_id and s.end_customer_id = new.id;
  return null;
end
$$;

revoke all on function public.borrar_sesiones_web_de_ficha_anonimizada() from public, anon, authenticated, service_role;

create trigger borrar_sesiones_web_al_anonimizar
  after update of deleted_at on public.end_customers
  for each row
  when (old.deleted_at is null and new.deleted_at is not null)
  execute function public.borrar_sesiones_web_de_ficha_anonimizada();

-- Los códigos guardan el hash del email, no la ficha: no se pueden ligar a una persona. Al terminar la purga del
-- tenant se borran todos, junto con las sesiones que pudieran quedar.
create function public.borrar_datos_web_de_tenant_purgado()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.sesiones_web s where s.tenant_id = new.id;
  delete from public.codigos_web c where c.tenant_id = new.id;
  return null;
end
$$;

revoke all on function public.borrar_datos_web_de_tenant_purgado() from public, anon, authenticated, service_role;

create trigger borrar_datos_web_al_purgar
  after update of status on public.tenants
  for each row
  when (new.status = 'purged' and old.status is distinct from 'purged')
  execute function public.borrar_datos_web_de_tenant_purgado();
