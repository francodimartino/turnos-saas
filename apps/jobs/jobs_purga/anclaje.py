"""Anclaje de las cabezas de la cadena de auditoría fuera de la base (ARCHITECTURE.md, decisión D6; Ola 4).

La cadena de hashes del `audit_log` delata ediciones y borrados del medio, pero no que alguien con control de la
base trunque el final de una cadena. Para detectarlo, al terminar cada corrida el job emite un log estructurado
`audit_chain_anchor` por cadena (cada tenant con auditoría y la de plataforma, con `tenant_id` null) leído con
`audit_chain_heads()`: el log sale a un sistema que la base no controla y el último hash conocido queda ahí.

El log lleva solo `tenant_id`, `chain_seq` y `hash`, sin ningún dato personal. El hash va en base64url sin
relleno (no en hexadecimal): una cadena hexadecimal de 64 caracteres puede contener una racha de 10 a 13 dígitos
que el filtro de teléfonos de los logs reemplazaría por `[filtrado]`, y el ancla quedaría inservible.
"""

import base64
import logging
from typing import Any

from platform_admin import ReportadorErrores

log = logging.getLogger("jobs_purga")

_SQL_CABEZAS = "select tenant_id, chain_seq, hash from public.audit_chain_heads()"


def _codificar(hash_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(hash_bytes)).decode("ascii").rstrip("=")


async def anclar_cadenas(pool: Any, reportador: ReportadorErrores) -> bool:
    """Emite un `audit_chain_anchor` por cadena. Nunca lanza: devuelve False si no pudo leer las cabezas, tras
    reportarlo con el evento `audit_chain_anchor_fallido` (la purga y los reintentos ya terminaron)."""
    try:
        async with pool.acquire() as conn:
            filas = await conn.fetch(_SQL_CABEZAS)
        anclas = [
            (None if f["tenant_id"] is None else str(f["tenant_id"]), int(f["chain_seq"]), _codificar(f["hash"]))
            for f in filas
        ]
    except Exception as error:  # noqa: BLE001 - un fallo del anclaje no frena el job
        log.error("audit_chain_anchor_fallido", extra={"motivo": type(error).__name__})
        reportador.capturar_excepcion(error, paso="audit_chain_anchor")
        return False
    for tenant_id, chain_seq, hash_cadena in anclas:
        log.info("audit_chain_anchor", extra={"tenant_id": tenant_id, "chain_seq": chain_seq, "hash": hash_cadena})
    return True
