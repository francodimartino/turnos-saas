#!/usr/bin/env bash
# Levanta Supabase local en el runner con reintentos acotados.
# Motivo: al bajar las imagenes aparece de forma esporadica
# "toomanyrequests: Rate exceeded" (limite del registro de imagenes).
# - Login opcional al registro si existen DOCKERHUB_USERNAME y DOCKERHUB_TOKEN
#   (secrets del repo; sin ellos el CI pasa igual, anonimo).
# - Solo se levantan los servicios que usan las migraciones y los tests
#   (base + auth, que crea el esquema `auth`): menos imagenes que bajar y
#   menos tiempo de arranque.
# - Hasta 3 intentos, con `supabase stop` entre ellos.
set -uo pipefail

if [ -n "${DOCKERHUB_USERNAME:-}" ] && [ -n "${DOCKERHUB_TOKEN:-}" ]; then
  echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin \
    || echo "Login al registro fallo; se sigue sin autenticar."
fi

EXCLUIR="studio,mailpit,imgproxy,edge-runtime,logflare,vector,postgres-meta,supavisor,realtime,storage-api,postgrest,kong"

for intento in 1 2 3; do
  if supabase start -x "$EXCLUIR"; then
    exit 0
  fi
  echo "supabase start fallo (intento $intento de 3)."
  supabase stop --no-backup >/dev/null 2>&1 || true
  [ "$intento" -lt 3 ] && sleep $((intento * 20))
done
echo "supabase start no logro levantar tras 3 intentos."
exit 1
