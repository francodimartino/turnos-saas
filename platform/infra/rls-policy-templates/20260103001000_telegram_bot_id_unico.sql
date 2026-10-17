-- Migración de plataforma 9: el id numérico del bot de Telegram es único entre tenants.
-- Un mismo bot (config.bot_id) no puede estar conectado a dos negocios distintos: el id sale del token que
-- carga el owner, y sin esta unicidad un token repetido mezclaría los mensajes entrantes de dos tenants.
-- Aditiva: índice único parcial; las filas existentes sin bot_id no participan (NULL no choca).
-- Volver a guardar la integración del mismo tenant con el mismo bot_id no falla: ya hay una sola fila por
-- (tenant, tipo) y se actualiza.

create unique index tenant_integrations_telegram_bot_id_uidx
  on public.tenant_integrations ((config ->> 'bot_id'))
  where integration_type = 'telegram_bot' and config ? 'bot_id';
