-- bookings: transiciones válidas de estado, forzadas en la base.
-- 'cancelled' y 'completed' son estados FINALES. Válidas: pending->confirmed, pending->cancelled,
-- confirmed->completed, confirmed->cancelled y mantener el mismo estado. Cualquier otra se rechaza.
-- Al salir de 'pending' (confirmed/cancelled/completed) pending_expires_at queda en null: el check
-- (status = 'pending') = (pending_expires_at is not null) lo exige, y el trigger lo limpia para que
-- ningún backend tenga que acordarse. Con los estados finales la sesión del pack (trigger AFTER
-- UPDATE de la migración de tablas) nunca se devuelve dos veces. Migración no destructiva.

create function public.bookings_validar_transicion_estado()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.status = old.status then
    return new;
  end if;

  if not ((old.status = 'pending'   and new.status in ('confirmed', 'cancelled'))
       or (old.status = 'confirmed' and new.status in ('completed', 'cancelled'))) then
    raise exception 'transición de estado inválida en bookings: % -> %', old.status, new.status
      using errcode = '23514';
  end if;

  -- sale de 'pending': ya no hay vencimiento (new.status nunca es 'pending' en este punto)
  new.pending_expires_at := null;
  return new;
end
$$;

-- BEFORE UPDATE de status; ordena después de bookings_forzar_tenant_id y antes de bookings_validar_pack
create trigger bookings_transiciones_estado
  before update of status on public.bookings
  for each row execute function public.bookings_validar_transicion_estado();
