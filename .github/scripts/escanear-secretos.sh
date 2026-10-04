#!/usr/bin/env bash
# Escaneo de secretos AUTÓNOMO — sin ninguna dependencia del Arnés de Desarrollo.
#
# Este archivo se copia DENTRO del proyecto y sigue funcionando después de retirar el
# arnés (retirar.py lo instala como hook de pre-commit; infra-builder lo copia a
# .github/scripts/ para el CI). Solo necesita bash y git.
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
)

ARCHIVO="?"
HALLAZGOS=0

while IFS= read -r linea; do
    case "$linea" in
        "+++ "*) ARCHIVO="${linea#+++ b/}"; continue ;;
        "+"*) ;;
        *) continue ;;
    esac
    for par in "${PATRONES[@]}"; do
        nombre="${par%%|*}"
        patron="${par#*|}"
        if printf '%s' "$linea" | grep -Eq -- "$patron"; then
            echo "❌ posible secreto — $nombre en $ARCHIVO"
            HALLAZGOS=$((HALLAZGOS + 1))
        fi
    done
done <<< "$DIFF"

if [ "$HALLAZGOS" -gt 0 ]; then
    echo "Commit/push bloqueado: $HALLAZGOS hallazgo(s). Sacá el secreto del código (usá una variable de entorno) y, si ya estuvo commiteado, rotalo."
    exit 1
fi

exit 0
