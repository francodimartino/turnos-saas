"""Avisos de turnos que se encolan en el outbox: al cliente, al negocio y el de vencimiento al liberar un horario.

Encolar es solo la intención, dentro de la transacción del cambio (un rollback no deja aviso): no resuelve
destinatarios ni envía. Los destinatarios los resuelve quien envía (`resolve_outbox_delivery`) y hoy el envío
inmediato posterior al COMMIT es solo por Telegram y cierra la fila.

- `encolar_aviso_cliente(conn, *, end_customer_id, canal, plantilla, booking_id, inicio)`: un aviso al cliente
  (confirmación, rechazo, cancelación, movimiento, reserva, vencimiento y cambio de horario aprobado, rechazado o
  vencido; las plantillas son las de `turnos_negocio.plantillas_cliente()`). Una sola fila por aviso, aunque la
  ficha tenga varios canales verificados: los resuelve quien envía. `canal` es el de origen (`telegram`, `whatsapp` o
  `email`). Devuelve el id de la fila, o `None` si la ficha está anonimizada. Demora por defecto del primer intento:
  300 segundos (ventana del envío inmediato); `0` para quien no tiene envío inmediato.
- `encolar_aviso_negocio(conn, *, plantilla, booking_id, inicio)`: un aviso al negocio (reserva, cancelación,
  movimiento o pedido de aprobación; `turnos_negocio.plantillas_negocio()`). La fila no lleva canal, destinatario
  ni ficha: el personal que lo recibe se resuelve al enviar. El tenant sale del contexto de la conexión (las dos
  funciones lo toman de `tenant_tx`; nunca se pasa). Primer intento inmediato por defecto.

Ambas guardan solo el turno (`booking_id` e `inicio`): el texto lo arma `turnos_negocio.mensaje_aviso_turno` al
enviar, con día, hora y servicio del turno y sin datos personales. Con un dato inválido lanzan `AvisoInvalido` (con
`codigo`) sin tocar la base: `plantilla_invalida`, `canal_invalido`, `demora_invalida` o `inicio_sin_zona`. Bot,
dashboard y la web llaman a estas mismas funciones.

Aviso de vencimiento al cliente cuyo turno `pending` vencido se libera para reservar su horario.

Punto único para todos los canales que liberan (`crear_turno(..., liberar_vencidos=True)`): el turno liberado
queda `cancelled`, así que el job de vencimiento ya no lo ve y el aviso lo encola quien liberó.

1. `encolar_avisos_liberados(conn, liberados)`: dentro de la MISMA transacción que la reserva (con la conexión
   del canal), un aviso por liberado en el outbox (`encolar_aviso`, plantilla `turno_vencido`, `params` solo con
   `booking_id` e `inicio`, sin texto ni datos del cliente). Si la transacción hace rollback no queda aviso.
   No encola para quien no tiene a quién avisar (sin canal, destinatario o turno) ni para un cliente anonimizado.
2. Después del COMMIT, `pedido_de_envio(aviso)` arma el `EnvioPedido` con el texto compartido
   (`turnos_negocio.mensaje_vencimiento`, el mismo que usa el job) y el id de la fila del outbox: el canal lo envía
   con `enviar_con_reintentos` y un `CompletadorOutbox`, que la cierra `sent` o `failed`. Si el proceso muere
   antes, la fila queda `pending`: el reescaneo del job de vencimiento solo retoma las filas que el envío inmediato
   no cerró, y solo de fichas cuyo único canal verificado es un Telegram; las de fichas con otro canal verificado o
   más de un Telegram quedan `pending` y hoy ningún proceso las envía. El envío inmediato es solo por Telegram y
   cierra la fila (`sent`/`failed`) aunque la ficha tenga otros canales verificados: el email no se envía.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from platform_notifications import EnvioPedido, encolar_aviso, encolar_aviso_al_negocio
from turnos_negocio import (
    PLANTILLA_VENCIMIENTO,
    mensaje_vencimiento,
    plantillas_cliente,
    plantillas_negocio,
)

from turnos_dominio.alta import Liberado

# Canales de origen de un aviso al cliente que admite el CHECK de `notification_outbox`.
CANALES_DE_AVISO = frozenset({"telegram", "whatsapp", "email"})
# Ventana en que el envío inmediato posterior al COMMIT tiene prioridad sobre el reescaneo.
PRIMER_INTENTO_CLIENTE_EN_SEGUNDOS = 300
PRIMER_INTENTO_NEGOCIO_EN_SEGUNDOS = 0
_MAXIMO_DEMORA_SEGUNDOS = 86400  # tope que acepta la base


class AvisoInvalido(ValueError):
    """Un dato del aviso no es válido: no se toca la base. `codigo` identifica el motivo."""

    codigo = "aviso_invalido"


class PlantillaDeAvisoInvalida(AvisoInvalido):
    codigo = "plantilla_invalida"


class CanalDeAvisoInvalido(AvisoInvalido):
    codigo = "canal_invalido"


class DemoraDeAvisoInvalida(AvisoInvalido):
    codigo = "demora_invalida"


class InicioSinZona(AvisoInvalido):
    codigo = "inicio_sin_zona"


def _params_del_turno(booking_id: uuid.UUID, inicio: datetime) -> dict[str, str]:
    if inicio.tzinfo is None or inicio.utcoffset() is None:
        raise InicioSinZona("el inicio del turno del aviso debe llevar zona horaria")
    return {"booking_id": str(booking_id), "inicio": inicio.isoformat()}


def _validar_demora(segundos: int) -> None:
    if not 0 <= segundos <= _MAXIMO_DEMORA_SEGUNDOS:
        raise DemoraDeAvisoInvalida(f"la demora del primer intento va de 0 a {_MAXIMO_DEMORA_SEGUNDOS} segundos")


async def encolar_aviso_negocio(
    conn: Any,
    *,
    plantilla: str,
    booking_id: uuid.UUID,
    inicio: datetime,
    primer_intento_en_segundos: int = PRIMER_INTENTO_NEGOCIO_EN_SEGUNDOS,
) -> uuid.UUID:
    """Encola en el outbox un aviso al negocio por el turno, en la transacción de `conn` (con contexto de tenant).

    La fila no lleva canal ni destinatario: el enviador resuelve el personal que lo recibe al enviar.
    """
    if plantilla not in plantillas_negocio():
        raise PlantillaDeAvisoInvalida(f"plantilla de aviso al negocio desconocida: {plantilla!r}")
    _validar_demora(primer_intento_en_segundos)
    params = _params_del_turno(booking_id, inicio)
    return await encolar_aviso_al_negocio(
        conn, template=plantilla, params=params, primer_intento_en_segundos=primer_intento_en_segundos
    )


async def encolar_aviso_cliente(
    conn: Any,
    *,
    end_customer_id: uuid.UUID,
    canal: str,
    plantilla: str,
    booking_id: uuid.UUID,
    inicio: datetime,
    primer_intento_en_segundos: int = PRIMER_INTENTO_CLIENTE_EN_SEGUNDOS,
) -> uuid.UUID | None:
    """Encola en el outbox un aviso al cliente por el turno, en la transacción de `conn` (con contexto de tenant).

    `canal` es el de origen del aviso. Los canales a los que llega se resuelven al enviar, desde la ficha.
    `None` si la ficha está anonimizada (no se retiene nada).
    """
    if plantilla not in plantillas_cliente():
        raise PlantillaDeAvisoInvalida(f"plantilla de aviso al cliente desconocida: {plantilla!r}")
    if canal not in CANALES_DE_AVISO:
        raise CanalDeAvisoInvalido(f"canal de aviso desconocido: {canal!r}")
    _validar_demora(primer_intento_en_segundos)
    params = _params_del_turno(booking_id, inicio)
    return await encolar_aviso(
        conn,
        end_customer_id=end_customer_id,
        channel=canal,
        template=plantilla,
        params=params,
        primer_intento_en_segundos=primer_intento_en_segundos,
    )


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
