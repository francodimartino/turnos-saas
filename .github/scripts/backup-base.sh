#!/usr/bin/env bash
# Backup externo de la base: pg_dump -> cifrado con age -> bucket S3-compatible (Cloudflare R2).
#
# El dump viaja por un pipe directo a `age`: nunca existe en disco sin cifrar. Solo el archivo
# `.age` se escribe y se sube. Todo valor sensible llega por variables de entorno (secrets del repo).
#
# Variables obligatorias:
#   BACKUP_DATABASE_URL    conexion a la base (pooler en modo sesion o conexion directa)
#   BACKUP_AGE_PUBLIC_KEY  clave publica age (age1...); la privada solo la tiene el responsable
#   BACKUP_BUCKET          nombre del bucket
#   BACKUP_ENDPOINT        endpoint S3 del bucket (https://<cuenta>.r2.cloudflarestorage.com)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   credenciales del bucket (las toma `aws` del entorno)
# Opcionales:
#   BACKUP_PREFIJO         carpeta dentro del bucket (por defecto "turnos-saas")
#   BACKUP_TMP             carpeta temporal (por defecto un mktemp)
set -euo pipefail

faltan=()
for var in BACKUP_DATABASE_URL BACKUP_AGE_PUBLIC_KEY BACKUP_BUCKET BACKUP_ENDPOINT AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
  [ -n "${!var:-}" ] || faltan+=("$var")
done
if [ "${#faltan[@]}" -gt 0 ]; then
  echo "::error::Faltan variables obligatorias: ${faltan[*]}" >&2
  exit 1
fi

# Solo claves publicas age: una clave privada (AGE-SECRET-KEY-...) nunca debe estar en el runner.
case "$BACKUP_AGE_PUBLIC_KEY" in
  age1*) ;;
  *) echo "::error::BACKUP_AGE_PUBLIC_KEY no es una clave publica age (debe empezar con age1)." >&2; exit 1 ;;
esac

PREFIJO="${BACKUP_PREFIJO:-turnos-saas}"
TMP="${BACKUP_TMP:-$(mktemp -d)}"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

sello="$(date -u +%Y%m%dT%H%M%SZ)"
carpeta="$PREFIJO/$(date -u +%Y/%m)"
archivo="$TMP/base-$sello.dump.age"

echo "Generando el dump cifrado..."
# --exclude-schema=vault: los secretos de Vault no salen de Supabase por este camino.
pg_dump --format=custom --no-owner --no-privileges --exclude-schema=vault "$BACKUP_DATABASE_URL" \
  | age --recipient "$BACKUP_AGE_PUBLIC_KEY" --output "$archivo"

[ -s "$archivo" ] || { echo "::error::El dump cifrado quedo vacio." >&2; exit 1; }
cabecera="$(head -c 21 "$archivo")"
[ "$cabecera" = "age-encryption.org/v1" ] || { echo "::error::El archivo no es un contenedor age; no se sube." >&2; exit 1; }

echo "Subiendo $(basename "$archivo") ($(wc -c < "$archivo") bytes) al bucket..."
aws s3 cp "$archivo" "s3://$BACKUP_BUCKET/$carpeta/$(basename "$archivo")" --endpoint-url "$BACKUP_ENDPOINT" --only-show-errors
echo "Backup subido: $carpeta/$(basename "$archivo")"
