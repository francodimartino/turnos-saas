"""Aviso de vencimiento al cliente cuyo turno `pending` vencido se libera para reservar su horario.

Punto único para todos los canales que liberan (`crear_turno(..., liberar_vencidos=True)`): el turno liberado
queda `cancelled`, así que el job de vencimiento ya no lo ve y el aviso lo encola quien liberó.

1. `encolar_avisos_liberados(conn, liberados)`: dentro de la MISMA transacción que la reserva (con la conexión
   del canal), un aviso por liberado en el outbox (`encolar_aviso`, plantilla `turno_vencido`, `params` solo con
   `booking_id` e `inicio`, sin texto ni datos del cliente). Si la transacción hace rollback no queda aviso.
   No encola para quien no tiene a quién avisar (sin canal, destinatario o turno) ni para un cliente anonimizado.
2. Después del COMMIT, `pedido_de_envio(aviso)` arma el `EnvioPedido` con el texto compartido
   (`turnos_negocio.mensaje_vencimiento`, el mismo que usa el job) y el id de la fila del outbox: el canal lo envía
   con `enviar_con_reintentos` y un `CompletadorOutbox`, que la cierra `sent` o `failed`. Si el proceso muere
   antes, la fila queda `pending` y la reenvía el reescaneo del job de vencimiento.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from platform_notifications import EnvioPedido, encolar_aviso
from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_vencimiento

from turnos_dominio.alta import Liberado


@dataclass(frozen=True)
class AvisoLiberado:
    """Un aviso ya encolado: el liberado y la fila del outbox que hay que cerrar tras el envío."""

    liberado: Liberado
    outbox_id: uuid.UUID


async def encolar_avisos_liberados(conn: Any, liberados: Sequence[Liberado]) -> tuple[AvisoLiberado, ...]:
    """Encola en el outbox el aviso de cada liberado con a quién avisar, en la transacción de `conn`."""
    encolados: list[AvisoLiberado] = []
    for lib in liberados:
        if not lib.notify or lib.channel is None or lib.channel_user_id is None:
            continue
        if lib.end_customer_id is None or lib.starts_at is None or lib.service_name is None:
            continue  # sin el cliente o el turno no se puede decir a quién ni cuál venció
        outbox_id = await encolar_aviso(
            conn,
            end_customer_id=lib.end_customer_id,
            channel=lib.channel,
            template=PLANTILLA_VENCIMIENTO,
            params={"booking_id": str(lib.booking_id), "inicio": lib.starts_at.isoformat()},
        )
        if outbox_id is not None:  # None: el cliente se anonimizó
            encolados.append(AvisoLiberado(lib, outbox_id))
    return tuple(encolados)


def pedido_de_envio(aviso: AvisoLiberado) -> EnvioPedido:
    """Pedido de envío del aviso encolado (texto de `turnos_negocio`, fila del outbox a cerrar)."""
    lib = aviso.liberado
    if lib.end_customer_id is None or lib.channel_user_id is None or lib.starts_at is None or lib.service_name is None:
        raise ValueError("el aviso encolado siempre trae cliente, destinatario y turno")
    return EnvioPedido(
        lib.end_customer_id,
        lib.channel_user_id,
        mensaje_vencimiento(lib.starts_at, lib.service_name),
        template=PLANTILLA_VENCIMIENTO,
        outbox_id=aviso.outbox_id,
    )
