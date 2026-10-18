-- Migración de turnos: índice para el listado de turnos por rango (SQL_LISTAR_BOOKINGS).
--
-- Problema: el listado filtra por "tenant_id = $1 and time_range && tstzrange(desde, hasta)" y ordena por
-- lower(time_range). Los índices existentes no lo sirven: bookings_tenant_id_idx solo acota por tenant (recorre
-- TODOS los turnos del tenant, de toda su historia, para quedarse con los de la ventana pedida) y el exclude
-- bookings_sin_doble_booking arranca por professional_id, que el listado no filtra.
--
-- Decisión: GiST (tenant_id, time_range), con btree_gist (ya instalada en el schema extensions; la usa el exclude).
-- Se descartó un btree (tenant_id, lower(time_range)): sirve el ORDER BY pero el solapamiento solo puede acotarse
-- por "lower < hasta", así que recorrería todo el pasado del tenant (crece con la historia); el GiST acota las dos
-- puntas del rango y su costo depende de los turnos de la ventana. El orden (lower(time_range), id) se resuelve con
-- un sort sobre las filas de la ventana, que son pocas (una semana o un mes de un tenant).
--
-- Idempotente (if not exists). No toca datos ni el exclude, y las demás consultas siguen igual.
--
-- Por qué no "create index concurrently": las migraciones corren dentro de una transacción (la CLI de Supabase
-- envía el archivo como un lote) y CONCURRENTLY no puede ejecutarse en una transacción. A la escala actual
-- (piloto: bookings con pocos miles de filas) el create index común bloquea las escrituras sobre bookings por una
-- fracción de segundo. Si bookings creciera a cientos de miles de filas antes de aplicar esto en una base real,
-- crear antes el índice a mano, fuera de la migración, con el mismo nombre y definición:
--   create index concurrently bookings_tenant_time_range_idx on public.bookings using gist (tenant_id, time_range);
-- y la migración (if not exists) lo reconoce y no hace nada.

create index if not exists bookings_tenant_time_range_idx
  on public.bookings using gist (tenant_id, time_range);
