"""Avisos de turnos que se encolan en el outbox: al cliente, al negocio y el de vencimiento al liberar un horario.

Encolar es solo la intención, dentro de la transacción del cambio (un rollback no deja aviso): no resuelve
destinatarios ni envía. Los destinatarios los resuelve quien envía (`resolve_outbox_delivery`).

Qué se envía al instante y qué queda pendiente. Tras el COMMIT, el bot y el dashboard envían el aviso en el
momento SOLO si la ficha tiene como único canal verificado un Telegram (`FichaParaAviso.solo_telegram`): envían
por Telegram y cierran la fila (`sent`/`failed`), con el primer intento del reescaneo a los 300 segundos. Con
cualquier otro canal verificado (email, WhatsApp o un segundo Telegram) no se envía al instante ni se cierra la
fila: queda `pending` con primer intento inmediato (0 segundos), para un proceso que envíe por todos los canales,
que hoy no existe (el reescaneo del job de vencimiento tampoco la retoma: solo toma fichas de un único Telegram).
Una ficha sin ningún canal verificado no recibe fila (`sin_canal`) y una anonimizada tampoco.
`solo_telegram` es la misma condición que `reclamar_avisos_telegram` aplica en SQL: ficha vigente con exactamente un
canal verificado en `canales_cliente` y que ese canal sea un Telegram. Se lee en la transacción del cambio
(`leer_ficha_para_aviso`); un canal que otra transacción verifique después de esa lectura y antes del COMMIT no se
ve, y en ese caso el envío inmediato cierra la fila como antes.

Funciones (todas reciben la conexión de la transacción, con contexto de tenant):

- `encolar_aviso_cliente(conn, *, end_customer_id, canal, plantilla, booking_id, inicio)`: inserta un aviso al
  cliente (plantillas de `turnos_negocio.plantillas_cliente()`). Una sola fila por aviso, aunque la ficha tenga
  varios canales verificados. `canal` es el de origen (`telegram`, `whatsapp` o `email`). Devuelve el id de la fila,
  o `None` si la ficha está anonimizada. Demora por defecto del primer intento: 300 segundos.
- `encolar_aviso_segun_canales(conn, *, end_customer_id, plantilla, booking_id, inicio, canal_origen=None)`: lee la
  ficha, decide con la regla de arriba, encola con la demora que corresponde y devuelve un `AvisoCliente`
  (`estado`, `outbox_id`, `destinatario_telegram` y `envio_inmediato`). Es lo que llaman hoy el bot (confirmación y
  reserva pendiente) y el dashboard (aprobar, rechazar, cancelar, mover y resolver un cambio de horario).
- `encolar_aviso_negocio(conn, *, plantilla, booking_id, inicio)`: un aviso al negocio (reserva, cancelación,
  movimiento o pedido de aprobación; `turnos_negocio.plantillas_negocio()`), sin canal, destinatario ni ficha. El
  tenant sale del contexto de la conexión. Primer intento inmediato. Hoy no la llama ningún flujo; el servicio
  enviador envía esas filas.
- `encolar_avisos_liberados(conn, liberados)`: el aviso de vencimiento de cada pending vencido liberado al reservar
  (bot y dashboard), por `encolar_aviso_segun_canales`; con `pedido_de_envio` se arma el envío inmediato posterior
  al COMMIT de los que cumplen la regla.

Lo encolado guarda solo el turno (`booking_id` e `inicio`), sin texto ni datos personales. Los textos los arma
`turnos_negocio.mensaje_aviso_turno` en el reescaneo del job de vencimiento (solo fichas de un único Telegram); el
envío inmediato del bot y del dashboard usa sus propios textos. Los avisos `cambio_horario_aprobado` y
`cambio_horario_rechazado` los encola el dashboard al resolver el cambio.
Con un dato inválido las funciones lanzan `AvisoInvalido` (con `codigo`) sin tocar la base: `plantilla_invalida`,
`canal_invalido`, `demora_invalida` o `inicio_sin_zona`.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
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


SQL_FICHA_PARA_AVISO = """
select (ec.deleted_at is not null) as anonimizada,
       count(cc.id) filter (where cc.verificado_at is not null) as verificados,
       count(cc.id) filter (where cc.verificado_at is not null and cc.canal = 'telegram') as telegrams,
       (array_agg(cc.canal order by case cc.canal when 'telegram' then 0 when 'whatsapp' then 1 else 2 end)
          filter (where cc.verificado_at is not null))[1] as canal_origen,
       max(cc.identificador) filter (where cc.verificado_at is not null and cc.canal = 'telegram')
         as identificador_telegram
  from public.end_customers ec
  left join public.canales_cliente cc on cc.tenant_id = ec.tenant_id and cc.end_customer_id = ec.id
 where ec.id = $1
 group by ec.id
"""
SQL_CLIENTE_DEL_TURNO = "select end_customer_id from public.bookings where id = $1"


@dataclass(frozen=True)
class FichaParaAviso:
    """Lo que hace falta saber de una ficha para encolar su aviso (leído en la transacción del cambio)."""

    anonimizada: bool
    verificados: int  # canales verificados de la ficha en `canales_cliente`
    telegrams: int  # cuántos de ellos son Telegram
    canal_origen: str | None  # `telegram` si hay, si no `whatsapp`, si no `email`; `None` sin canal verificado
    identificador_telegram: str | None = field(default=None, repr=False)  # chat del Telegram, dato del cliente

    @property
    def solo_telegram(self) -> bool:
        """Único canal verificado = un Telegram: la condición del envío inmediato y la de `reclamar_avisos_telegram`."""
        return (
            not self.anonimizada
            and self.verificados == 1
            and self.telegrams == 1
            and self.identificador_telegram is not None
        )


async def leer_ficha_para_aviso(conn: Any, end_customer_id: uuid.UUID) -> FichaParaAviso | None:
    """Canales de la ficha, o `None` si no existe (o es de otro tenant: la RLS no la deja ver)."""
    fila = await conn.fetchrow(SQL_FICHA_PARA_AVISO, end_customer_id)
    if fila is None:
        return None
    return FichaParaAviso(
        anonimizada=fila["anonimizada"],
        verificados=fila["verificados"],
        telegrams=fila["telegrams"],
        canal_origen=fila["canal_origen"],
        identificador_telegram=fila["identificador_telegram"],
    )


@dataclass(frozen=True)
class AvisoCliente:
    """Resultado de encolar un aviso al cliente según sus canales.

    `estado`: `encolado` (hay fila), `cliente_anonimizado` o `sin_canal` (ficha inexistente o sin canal verificado:
    no hay fila). `destinatario_telegram` solo viene si corresponde el envío inmediato.
    """

    estado: str
    outbox_id: uuid.UUID | None = None
    destinatario_telegram: str | None = field(default=None, repr=False)

    @property
    def envio_inmediato(self) -> bool:
        return self.outbox_id is not None and self.destinatario_telegram is not None


async def encolar_aviso_segun_canales(
    conn: Any,
    *,
    end_customer_id: uuid.UUID,
    plantilla: str,
    booking_id: uuid.UUID,
    inicio: datetime,
    canal_origen: str | None = None,
) -> AvisoCliente:
    """Encola el aviso al cliente y dice si corresponde enviarlo al instante (solo con un único Telegram verificado).

    Sin envío inmediato la fila queda `pending` con primer intento inmediato. `canal_origen` es el del mensaje que
    originó el aviso (el bot); por defecto, el mejor canal verificado de la ficha.
    """
    ficha = await leer_ficha_para_aviso(conn, end_customer_id)
    if ficha is None:
        return AvisoCliente("sin_canal")
    if ficha.anonimizada:
        return AvisoCliente("cliente_anonimizado")
    canal = canal_origen or ficha.canal_origen
    if ficha.verificados == 0 or canal is None:
        return AvisoCliente("sin_canal")
    inmediato = ficha.solo_telegram
    outbox_id = await encolar_aviso_cliente(
        conn,
        end_customer_id=end_customer_id,
        canal=canal,
        plantilla=plantilla,
        booking_id=booking_id,
        inicio=inicio,
        primer_intento_en_segundos=PRIMER_INTENTO_CLIENTE_EN_SEGUNDOS if inmediato else 0,
    )
    if outbox_id is None:  # se anonimizó entre la lectura y el encolado
        return AvisoCliente("cliente_anonimizado")
    return AvisoCliente("encolado", outbox_id, ficha.identificador_telegram if inmediato else None)


@dataclass(frozen=True)
class AvisoLiberado:
    """Un aviso de vencimiento ya encolado: el liberado y la fila del outbox.

    `destinatario_telegram` solo está si corresponde el envío inmediato (único canal verificado = un Telegram);
    sin él la fila queda `pending` y no se envía ni se cierra.
    """

    liberado: Liberado
    outbox_id: uuid.UUID
    end_customer_id: uuid.UUID | None = None
    destinatario_telegram: str | None = field(default=None, repr=False)

    @property
    def envio_inmediato(self) -> bool:
        return self.destinatario_telegram is not None


async def encolar_avisos_liberados(conn: Any, liberados: Sequence[Liberado]) -> tuple[AvisoLiberado, ...]:
    """Encola en el outbox el aviso de vencimiento de cada liberado, en la transacción de `conn`.

    Encola para toda ficha vigente con algún canal verificado, tenga o no el canal heredado de `end_customers`
    (que `release_expired_pending_overlaps` usa para `notify`): sin ese canal la función no devuelve la ficha y se
    la busca en el turno. No encola sin ficha, sin canal verificado ni para una ficha anonimizada.
    """
    encolados: list[AvisoLiberado] = []
    for lib in liberados:
        if lib.starts_at is None:
            continue  # sin el inicio no se puede decir cuál turno venció
        cliente = lib.end_customer_id or await conn.fetchval(SQL_CLIENTE_DEL_TURNO, lib.booking_id)
        if cliente is None:
            continue
        aviso = await encolar_aviso_segun_canales(
            conn,
            end_customer_id=cliente,
            plantilla=PLANTILLA_VENCIMIENTO,
            booking_id=lib.booking_id,
            inicio=lib.starts_at,
        )
        if aviso.outbox_id is not None:
            encolados.append(AvisoLiberado(lib, aviso.outbox_id, cliente, aviso.destinatario_telegram))
    return tuple(encolados)


def pedido_de_envio(aviso: AvisoLiberado) -> EnvioPedido:
    """Pedido de envío inmediato del aviso encolado (texto de `turnos_negocio`, fila del outbox a cerrar).

    Solo para los avisos con `envio_inmediato`: los demás quedan `pending` y no se envían ni se cierran.
    """
    lib = aviso.liberado
    if (
        aviso.end_customer_id is None
        or aviso.destinatario_telegram is None
        or lib.starts_at is None
        or lib.service_name is None
    ):
        raise ValueError("solo se envía al instante un aviso con cliente, Telegram único y turno")
    return EnvioPedido(
        aviso.end_customer_id,
        aviso.destinatario_telegram,
        mensaje_vencimiento(lib.starts_at, lib.service_name),
        template=PLANTILLA_VENCIMIENTO,
        outbox_id=aviso.outbox_id,
    )
