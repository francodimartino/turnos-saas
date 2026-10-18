"""Outbox de avisos (§6.4): la intención de avisar se escribe en la misma transacción que el cambio de turno.

`encolar_aviso` llama a `public.enqueue_notification(...)` con la conexión del llamador: si la transacción hace
rollback no queda aviso. Tras el COMMIT el envío inmediato corre en segundo plano (`enviar_con_reintentos`) y,
al terminar, cierra la fila con `CompletadorOutbox` (`public.complete_notification_outbox`). Si el proceso muere
antes, la fila queda `pending` y el reescaneo del job de vencimiento la envía.

`params` lleva identificadores y datos no personales (id del turno, inicio): nunca el texto ni el destinatario;
el backend los resuelve al enviar. Un cliente anonimizado no encola nada (la función devuelve NULL).
"""

import json
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

from platform_notifications.registro import AbrirTransaccion

logger = logging.getLogger("platform_notifications.outbox")

PRIMER_INTENTO_EN_SEGUNDOS = 300  # ventana en que el envío inmediato tiene prioridad sobre el reescaneo


async def encolar_aviso(
    conn: Any,
    *,
    end_customer_id: uuid.UUID,
    channel: str,
    template: str,
    params: dict[str, Any] | None = None,
    primer_intento_en_segundos: int = PRIMER_INTENTO_EN_SEGUNDOS,
) -> uuid.UUID | None:
    """Encola el aviso en la transacción de `conn` (con contexto de tenant). `None` si el cliente está anonimizado."""
    return await conn.fetchval(
        "select public.enqueue_notification($1, $2, $3, $4::jsonb, $5)",
        end_customer_id,
        channel,
        template,
        json.dumps(params or {}),
        primer_intento_en_segundos,
    )


class CompletadorOutbox(ABC):
    @abstractmethod
    async def completar(
        self,
        outbox_id: uuid.UUID,
        status: str,
        last_error: str | None = None,
        retry_in_seconds: int | None = None,
    ) -> bool:
        """Cierra (`sent`/`failed`) o reprograma (`pending`) la fila. Idempotente; `False` si ya no estaba pending."""


class CompletadorOutboxFuncion(CompletadorOutbox):
    _SQL = "select public.complete_notification_outbox($1, $2, $3, $4)"

    def __init__(self, abrir: AbrirTransaccion) -> None:
        self._abrir = abrir

    async def completar(self, outbox_id, status, last_error=None, retry_in_seconds=None):
        async with self._abrir() as conn:
            return await conn.fetchval(self._SQL, outbox_id, status, last_error, retry_in_seconds)


async def completar_sin_lanzar(
    outbox: CompletadorOutbox | None, outbox_id: uuid.UUID | None, status: str, last_error: str | None = None
) -> None:
    """Cierra la fila del outbox sin propagar fallos: si no se puede, queda `pending` y el reescaneo la retoma."""
    if outbox is None or outbox_id is None:
        return
    try:
        await outbox.completar(outbox_id, status, last_error)
    except Exception as error:  # noqa: BLE001 - el envío ya ocurrió; el peor caso es un duplicado documentado
        logger.warning("outbox_sin_cerrar", extra={"error": type(error).__name__})
