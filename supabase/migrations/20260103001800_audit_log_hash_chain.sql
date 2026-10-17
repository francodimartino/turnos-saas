-- Migración de plataforma: cadena de hashes por tenant en audit_log (tamper-evidence).
--
-- Problema: audit_log es solo-agregar para los roles de aplicación y por trigger (UPDATE/DELETE/TRUNCATE se
-- rechazan), pero quien administra la base (rol dueño, superusuario) puede deshabilitar el trigger, alterar o
-- borrar filas y no deja rastro. La auditoría que usan el panel y soporte no sería confiable ante ese caso.
--
-- Solución: cada fila nueva guarda chain_seq (posición dentro de su cadena), prev_hash (hash de la fila anterior)
-- y hash = sha256(prev_hash || contenido canónico de la fila). Hay una cadena por tenant; las filas con
-- tenant_id null (acciones de plataforma) forman su propia cadena. verify_audit_chain(tenant) la recorre y
-- devuelve el id de la primera fila rota, o null si es íntegra.
--
-- DISEÑO
--   * Cobertura total: la cadena se calcula en un trigger BEFORE INSERT sobre audit_log, no en las funciones.
--     Cubre write_audit_log, audit_log_append (que llaman todas las funciones SQL de purga, conexión, etc.) y
--     cualquier INSERT directo del dueño. El trigger SOBRESCRIBE chain_seq/prev_hash/hash: un valor provisto por
--     quien inserta se ignora. audit_log_append y write_audit_log no cambian.
--   * Concurrencia: el trigger toma pg_advisory_xact_lock por cadena (una clave por tenant, otra para la cadena
--     de plataforma) antes de leer la última fila. Dos inserciones del mismo tenant se serializan; tenants
--     distintos no se bloquean entre sí. El lock se libera al terminar la transacción. Un índice único
--     (tenant_id, chain_seq) hace imposible una rama aunque el lock fallara (la segunda inserción falla en vez
--     de bifurcar). Dos transacciones que escriban auditoría de dos tenants en orden inverso pueden
--     deadlockear: las funciones existentes escriben de a un tenant por transacción, así que no ocurre hoy.
--   * Contenido canónico: jsonb_build_array(id, tenant_id, actor_type, actor_id, action, entity, entity_id,
--     metadata, created_at en UTC con microsegundos como texto), en texto. No depende de TimeZone ni de
--     DateStyle de la sesión.
--   * Filas existentes ("génesis"): la migración las encadena, por tenant, en orden (created_at, id), con
--     prev_hash null en la primera de cada cadena. NO se recalcula ni se altera ninguna columna previa: solo se
--     completan las tres columnas nuevas, con el trigger solo-agregar deshabilitado dentro de esta transacción
--     y vuelto a habilitar al final. Lo anterior a esta migración no era a prueba de manipulación: la cadena
--     protege lo que se haya escrito desde acá (una alteración previa queda "sellada" tal cual estaba).
--   * Quién ejecuta verify_audit_chain: SOLO platform_admin (EXECUTE revocado de PUBLIC/anon/authenticated/
--     service_role y del resto). SECURITY DEFINER para no depender de RLS; search_path vacío.
--
-- LIMITES (documentados, no hay forma de evitarlos solo dentro de la base)
--   * Borrar las últimas filas de una cadena (la cola) no se detecta: no queda ninguna fila posterior que
--     delate el hueco. Para eso hay que anclar afuera el último hash (p. ej. copiarlo periódicamente a otro
--     sistema) y compararlo.
--   * Quien tenga el rol dueño y pueda recalcular TODA la cadena desde el punto alterado puede forjarla; la
--     verificación detecta ediciones parciales, no a un adversario que rehace los hashes y además controla el
--     anclaje externo.
--   * verify_audit_chain recorre la cadena entera: a la escala del piloto es inmediato.
--
-- INTERFAZ PARA EL BACKEND (no incluida acá): select public.verify_audit_chain(:tenant_id) como platform_admin;
--   null = cadena íntegra (o vacía); un uuid = id de la primera fila rota. Con tenant_id null verifica la cadena
--   de plataforma.

-- ---------------------------------------------------------------------------
-- 1. Columnas nuevas (nullable: no tocan datos)
-- ---------------------------------------------------------------------------
alter table public.audit_log
  add column chain_seq bigint null,
  add column prev_hash bytea null,
  add column hash bytea null;

-- ---------------------------------------------------------------------------
-- 2. Hash canónico (puro). Sin EXECUTE de aplicación: lo usan el trigger y verify_audit_chain.
-- ---------------------------------------------------------------------------
create function public.audit_log_calcular_hash(
  p_prev_hash bytea,
  p_id uuid,
  p_tenant_id uuid,
  p_actor_type text,
  p_actor_id uuid,
  p_action text,
  p_entity text,
  p_entity_id uuid,
  p_metadata jsonb,
  p_created_at timestamptz
)
returns bytea
language sql
immutable
set search_path = ''
as $$
  select sha256(convert_to(
    coalesce(encode(p_prev_hash, 'hex'), '') || '|' ||
    jsonb_build_array(
      p_id, p_tenant_id, p_actor_type, p_actor_id, p_action, p_entity, p_entity_id, p_metadata,
      to_char(p_created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
    )::text,
    'UTF8'))
$$;

revoke all on function public.audit_log_calcular_hash(bytea, uuid, uuid, text, uuid, text, text, uuid, jsonb, timestamptz)
  from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. Función del trigger de encadenado (cubre TODA inserción, venga de donde venga; se crea el trigger en el paso 4)
-- ---------------------------------------------------------------------------
create function public.audit_log_encadenar()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_ultimo_seq bigint;
  v_ultimo_hash bytea;
begin
  -- una cadena por tenant (y una para tenant null); el lock dura hasta el fin de la transacción
  perform pg_advisory_xact_lock(hashtextextended('audit_log_chain:' || coalesce(new.tenant_id::text, 'plataforma'), 0));

  select a.chain_seq, a.hash into v_ultimo_seq, v_ultimo_hash
    from public.audit_log a
   where a.tenant_id is not distinct from new.tenant_id
   order by a.chain_seq desc
   limit 1;

  new.chain_seq := coalesce(v_ultimo_seq, 0) + 1;
  new.prev_hash := v_ultimo_hash;
  new.hash := public.audit_log_calcular_hash(v_ultimo_hash, new.id, new.tenant_id, new.actor_type, new.actor_id,
                                             new.action, new.entity, new.entity_id, new.metadata, new.created_at);
  return new;
end
$$;

revoke all on function public.audit_log_encadenar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4. Encadenar lo existente (por tenant, en orden created_at, id), NOT NULL, índices únicos y trigger.
--    Todo en UN solo bloque DO: el cliente de migraciones ejecuta cada sentencia suelta (sin transacción
--    común), y el LOCK TABLE, el backfill y la creación del trigger tienen que ser atómicos para que ninguna
--    inserción concurrente se cuele sin hash entre el backfill y el trigger. Solo completa las columnas nuevas.
-- ---------------------------------------------------------------------------
do $$
declare
  r record;
  v_tenant uuid := null;
  v_primera boolean := true;
  v_seq bigint := 0;
  v_prev bytea := null;
  v_hash bytea;
begin
  lock table public.audit_log in share row exclusive mode;
  alter table public.audit_log disable trigger audit_log_solo_agregar_row;

  for r in
    select * from public.audit_log order by tenant_id nulls first, created_at, id
  loop
    if v_primera or r.tenant_id is distinct from v_tenant then
      v_primera := false;
      v_tenant := r.tenant_id;
      v_seq := 0;
      v_prev := null;
    end if;
    v_seq := v_seq + 1;
    v_hash := public.audit_log_calcular_hash(v_prev, r.id, r.tenant_id, r.actor_type, r.actor_id, r.action,
                                             r.entity, r.entity_id, r.metadata, r.created_at);
    update public.audit_log set chain_seq = v_seq, prev_hash = v_prev, hash = v_hash where id = r.id;
    v_prev := v_hash;
  end loop;

  alter table public.audit_log enable trigger audit_log_solo_agregar_row;

  alter table public.audit_log
    alter column chain_seq set not null,
    alter column hash set not null;

  -- Una sola posición por cadena: imposible una rama. Los null no colisionan entre sí en un índice único normal,
  -- por eso la cadena de plataforma tiene su propio índice parcial.
  create unique index audit_log_chain_tenant_uq on public.audit_log (tenant_id, chain_seq)
    where tenant_id is not null;
  create unique index audit_log_chain_plataforma_uq on public.audit_log (chain_seq)
    where tenant_id is null;

  create trigger audit_log_encadenar_row
    before insert on public.audit_log
    for each row execute function public.audit_log_encadenar();
end
$$;

-- ---------------------------------------------------------------------------
-- 5. verify_audit_chain(tenant_id): id de la primera fila rota, o null si la cadena es íntegra
--    Rota = hash que no coincide con el contenido, prev_hash distinto del hash de la anterior, o salto en
--    chain_seq (fila borrada del medio o del inicio: la cadena debe empezar en 1 con prev_hash null).
-- ---------------------------------------------------------------------------
create function public.verify_audit_chain(p_tenant_id uuid)
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select c.id
    from (
      select a.id, a.chain_seq, a.prev_hash, a.hash,
             lag(a.hash) over w as hash_anterior,
             lag(a.chain_seq) over w as seq_anterior,
             public.audit_log_calcular_hash(a.prev_hash, a.id, a.tenant_id, a.actor_type, a.actor_id, a.action,
                                            a.entity, a.entity_id, a.metadata, a.created_at) as hash_esperado
        from public.audit_log a
       where a.tenant_id is not distinct from p_tenant_id
      window w as (order by a.chain_seq)
    ) c
   where c.hash is distinct from c.hash_esperado
      or c.prev_hash is distinct from c.hash_anterior
      or c.chain_seq is distinct from coalesce(c.seq_anterior, 0) + 1
   order by c.chain_seq
   limit 1
$$;

revoke all on function public.verify_audit_chain(uuid) from public, anon, authenticated, service_role;
grant execute on function public.verify_audit_chain(uuid) to platform_admin;
