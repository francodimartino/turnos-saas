#!/usr/bin/env bash
# Escaneo de secretos AUTÓNOMO — sin ninguna dependencia de herramientas externas al proyecto.
#
# Este archivo se copia DENTRO del proyecto y sigue funcionando después de retirar el
# hook de pre-commit y en el CI (.github/workflows). Solo necesita bash y git.
#
# Uso:
#   sin argumentos        → escanea el diff staged (modo hook de pre-commit)
#   <rango git>           → escanea ese rango, ej. en CI:  bash escanear-secretos.sh origin/main...HEAD
#
# Solo mira líneas agregadas y reporta patrón + archivo, NUNCA el valor encontrado.

set -euo pipefail

if [ -n "${1:-}" ]; then
    DIFF="$(git diff -U0 "$1")"
else
    DIFF="$(git diff --cached -U0)"
fi

PATRONES=(
    "clave de acceso AWS|AKIA[0-9A-Z]{16}"
    "API key estilo OpenAI/Stripe|sk-[a-zA-Z0-9]{20,}"
    "clave privada|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    "password literal|[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd][[:space:]]*=[[:space:]]*['\"][^'\"]{4,}['\"]"
    "clave secreta de Stripe|(sk|rk)_live_[0-9A-Za-z]{20,}"
    "token de GitHub|(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}"
    "token de GitHub (fine-grained)|github_pat_[A-Za-z0-9_]{22,}"
    "API key de Google|AIza[0-9A-Za-z_-]{35}"
    "token de Slack|xox[baprs]-[0-9A-Za-z-]{10,}"
    "JWT (clave de Supabase)|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    "token de bot de Telegram|(^|[^0-9A-Za-z])[0-9]{8,10}:[A-Za-z0-9_-]{35}([^A-Za-z0-9_-]|$)"
    "URL de Postgres con credenciales|postgres(ql)?://[^:/@[:space:]\"']+:[^@[:space:]\"']+@[^/[:space:]\"']+"
    "client secret de Google (GOCSPX)|GOCSPX-[A-Za-z0-9_-]{10,}"
    "credencial de Mercado Pago (APP_USR)|APP_USR-[0-9A-Za-z-]{10,}"
    "DSN de Sentry|https?://[0-9a-f]{32}(:[0-9a-f]{32})?@[A-Za-z0-9.-]*sentry[A-Za-z0-9.-]*"
    "secret key de Supabase (sb_secret)|sb_secret_[A-Za-z0-9_-]{10,}"
    "access token de Supabase (sbp)|sbp_[0-9a-f]{20,}"
)

# Valores locales o de ejemplo conocidos que NO son secretos: si TODA coincidencia de un
# patron en la linea coincide con la exclusion de su nombre, no se reporta.
excluir_para() {
    case "$1" in
        "URL de Postgres con credenciales")
            # base local del repo, host "h" de los tests, y clave que ES una plantilla: la
            # exclusion de [<{$] aplica solo a la zona de la clave (justo tras "usuario:"), no a
            # toda la coincidencia: "u:ab$cd9{x}@host" es una clave real y se marca. Un host (o puerto)
            # que es plantilla, "@${HOST}" o "@127.0.0.1:{puerto}", tampoco expone nada
            echo '@(127\.0\.0\.1|localhost|host\.docker\.internal|h)(:[0-9]+|:[<{$][^/]*)?$|@[<{$]|^postgres(ql)?://[^:/@]+:[<{$]' ;;
        "JWT (clave de Supabase)")
            # claves demo de Supabase local (anon/service_role): payload con iss "supabase-demo"
            echo '^eyJ[A-Za-z0-9_-]+\.eyJpc3MiOiJzdXBhYmFzZS1kZW1v' ;;
        "token de bot de Telegram")
            # token de ejemplo de la documentacion de Telegram, usado en tests
            echo '123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw' ;;
        *) echo '' ;;
    esac
}

# Exit 0 si la linea tiene al menos una coincidencia del patron que no esta excluida.
hay_hallazgo() {
    local nombre="$1" patron="$2" linea="$3" excl coincidencias
    coincidencias="$(printf '%s\n' "$linea" | grep -Eo -- "$patron" || true)"
    [ -n "$coincidencias" ] || return 1
    excl="$(excluir_para "$nombre")"
    if [ -n "$excl" ]; then
        coincidencias="$(printf '%s\n' "$coincidencias" | grep -Ev -- "$excl" || true)"
    fi
    [ -n "$coincidencias" ]
}

ARCHIVO="?"
HALLAZGOS=0
SEP=$'\001'
ANOTADO="$(mktemp)"
trap 'rm -f "$ANOTADO"' EXIT

# Una pasada (solo builtins de bash): cada linea agregada queda como "archivo<SEP>contenido".
# Despues cada patron se busca con un unico grep sobre todo el archivo: mucho mas rapido
# que un grep por linea y por patron cuando el diff es grande.
while IFS= read -r linea; do
    case "$linea" in
        "+++ "*) ARCHIVO="${linea#+++ b/}"; continue ;;
        "+"*) printf '%s\n' "$ARCHIVO$SEP${linea#+}" ;;
    esac
done <<< "$DIFF" > "$ANOTADO"

for par in "${PATRONES[@]}"; do
    nombre="${par%%|*}"
    patron="${par#*|}"
    while IFS= read -r candidata; do
        [ -n "$candidata" ] || continue
        if hay_hallazgo "$nombre" "$patron" "${candidata#*"$SEP"}"; then
            echo "❌ posible secreto — $nombre en ${candidata%%"$SEP"*}"
            HALLAZGOS=$((HALLAZGOS + 1))
        fi
    done < <(grep -Ea -- "$patron" "$ANOTADO" || true)
done

if [ "$HALLAZGOS" -gt 0 ]; then
    echo "Commit/push bloqueado: $HALLAZGOS hallazgo(s). Sacá el secreto del código (usá una variable de entorno) y, si ya estuvo commiteado, rotalo."
    exit 1
fi

exit 0
