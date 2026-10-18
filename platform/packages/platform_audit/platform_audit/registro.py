"""`registrar`: único punto de entrada para escribir `audit_log` desde Python.

Llama a `public.write_audit_log(...)` (SECURITY DEFINER): el INSERT directo no existe ni está permitido a los
roles de aplicación. El tipo de actor lo deriva la base del rol de la conexión (`session_user`) y el tenant sale
del contexto de la transacción (`platform_tenancy`), nunca de parámetros del llamador.

La metadata del audit es para identificadores opacos y datos operativos, sin PII (Ley 25.326, §6.1): se
rechaza cualquier clave de PII conocida, también anidada.
"""

import json
import uuid
from typing import Any

# Claves de PII que no pueden ir en metadata. Es una lista propia de platform_audit a propósito: platform_*
# no depende de apps/ (la de logs vive en apps/shared/observabilidad).
CLAVES_PII = frozenset(
    {
        "phone",
        "name",
        "channel_user_id",
        "text",
        "body",
        "message_text",
        "message_body",
        "content",
        "caption",
        "email",
        "address",
    }
)

_SQL = "select public.write_audit_log($1, $2, $3, $4, $5)"


class MetadataConPII(ValueError):
    """La metadata del audit contiene una clave de PII. El mensaje nombra la clave, nunca el valor."""


PROFUNDIDAD_MAXIMA_METADATA = 20


def _claves_pii(valor: Any, nivel: int = 0) -> set[str]:
    if nivel > PROFUNDIDAD_MAXIMA_METADATA:
        raise MetadataConPII(f"metadata demasiado anidada: máximo {PROFUNDIDAD_MAXIMA_METADATA} niveles")
    encontradas: set[str] = set()
    if isinstance(valor, dict):
        for clave, hijo in valor.items():
            if isinstance(clave, str) and clave.lower() in CLAVES_PII:
                encontradas.add(clave.lower())
            encontradas |= _claves_pii(hijo, nivel + 1)
    elif isinstance(valor, (list, tuple)):
        for hijo in valor:
            encontradas |= _claves_pii(hijo, nivel + 1)
    return encontradas


def _serializar(metadata: Any) -> str | None:
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise TypeError("metadata debe ser un dict (objeto JSON) o None")
    prohibidas = _claves_pii(metadata)
    if prohibidas:
        raise MetadataConPII(f"metadata con claves de PII no permitidas en audit_log: {sorted(prohibidas)}")
    try:
        return json.dumps(metadata)
    except (TypeError, ValueError):
        # No se incluye el error original: nombra el valor que no se pudo serializar.
        raise TypeError("metadata no es serializable a JSON") from None


async def registrar(
    conn: Any,
    action: str,
    entity: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    tenant_id: uuid.UUID | str | None = None,
) -> uuid.UUID:
    """Escribe una fila de `audit_log` vía `write_audit_log` y devuelve su id.

    `conn` es la conexión de la transacción abierta con `platform_tenancy` (el contexto de tenant/actor vive
    en esa transacción). `tenant_id` solo lo aceptan `platform_admin` y `purge_job`; para bot y dashboard la
    base lo rechaza si no coincide con el contexto.
    """
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action es obligatoria")
    cuerpo = _serializar(metadata)
    return await conn.fetchval(_SQL, action, entity, entity_id, cuerpo, tenant_id)
