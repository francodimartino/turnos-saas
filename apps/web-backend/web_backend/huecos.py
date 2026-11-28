"""Dominio de los horarios libres de la web pública (ARCHITECTURE.md §2.12, §7.1): sin FastAPI.

El cálculo es el de `turnos_dominio.calcular_huecos`, el mismo que usa el bot. Acá solo se arma el rango a partir de
fechas del calendario del negocio, se acota (nadie pide un año de huecos de una sola vez) y se aplica la anticipación
mínima de reserva del negocio, que sale de sus settings con el mismo default que el bot.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from turnos_dominio import Ventana, calcular_huecos
from turnos_dominio.limites import ANTICIPACION_MINIMA_MINUTOS, interpretar
from turnos_negocio import ZONA_HORARIA

from web_backend import consultas
from web_backend.negocio import MODOS_RESERVABLES

# Días que abarca un pedido (contando el primero y el último) y hasta cuántos días desde hoy se puede mirar.
MAX_DIAS_RANGO = 31
MAX_DIAS_ADELANTE = 90


class ErrorHuecos(Exception):
    """Pedido de horarios que no se puede atender. `codigo` y `mensaje` son los que ve el cliente."""

    codigo = "pedido_invalido"
    mensaje = "El pedido de horarios no es válido."


class FechaInvalida(ErrorHuecos):
    codigo = "fecha_invalida"
    mensaje = "La fecha no es válida: usá el formato AAAA-MM-DD."


class RangoInvalido(ErrorHuecos):
    codigo = "rango_invalido"
    mensaje = "La fecha final es anterior a la inicial."


class RangoDemasiadoGrande(ErrorHuecos):
    codigo = "rango_demasiado_grande"
    mensaje = f"El rango no puede abarcar más de {MAX_DIAS_RANGO} días."


class FechaFueraDeRango(ErrorHuecos):
    codigo = "fecha_fuera_de_rango"
    mensaje = f"Solo se pueden consultar horarios desde hoy y hasta {MAX_DIAS_ADELANTE} días adelante."


class ServicioOProfesionalInexistente(ErrorHuecos):
    codigo = "no_encontrado"
    mensaje = "El servicio o el profesional no existe."


@dataclass(frozen=True)
class ConsultaHuecos:
    servicio_id: uuid.UUID
    profesional_id: uuid.UUID
    rango: Ventana


def parsear_fecha(texto: str) -> date:
    """`AAAA-MM-DD` del calendario del negocio; un día que no existe (30 de febrero) es `FechaInvalida`."""
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise FechaInvalida from None


def armar_rango(desde: date, hasta: date, ahora: datetime) -> Ventana:
    """Rango [medianoche local de `desde`, medianoche local del día siguiente a `hasta`), ya acotado.

    `hasta` es inclusive. Las fechas son del calendario del negocio, no UTC: a las 22:30 del 2 de noviembre en
    Buenos Aires el 2 todavía es hoy aunque en UTC ya sea el 3.
    """
    if hasta < desde:
        raise RangoInvalido
    if (hasta - desde).days + 1 > MAX_DIAS_RANGO:
        raise RangoDemasiadoGrande
    hoy = ahora.astimezone(ZONA_HORARIA).date()
    if desde < hoy or hasta > hoy + timedelta(days=MAX_DIAS_ADELANTE):
        raise FechaFueraDeRango
    return Ventana(
        datetime.combine(desde, time.min, tzinfo=ZONA_HORARIA),
        datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=ZONA_HORARIA),
    )


def armar_consulta(servicio_id: str, profesional_id: str, desde: str, hasta: str, ahora: datetime) -> ConsultaHuecos:
    """Valida el pedido ya con la forma correcta (ids y fechas con su largo y formato) y arma el rango."""
    rango = armar_rango(parsear_fecha(desde), parsear_fecha(hasta), ahora)
    return ConsultaHuecos(uuid.UUID(servicio_id), uuid.UUID(profesional_id), rango)


async def buscar_huecos(conn: Any, tenant_id: uuid.UUID, consulta: ConsultaHuecos, ahora: datetime) -> list[Ventana]:
    """Huecos libres del profesional para el servicio. `conn` ya tiene el contexto de `tenant_id`."""
    par = await conn.fetchrow(
        consultas.SQL_PAR_RESERVABLE, tenant_id, consulta.servicio_id, consulta.profesional_id, list(MODOS_RESERVABLES)
    )
    if par is None:
        raise ServicioOProfesionalInexistente
    fila = await conn.fetchrow(consultas.SQL_AVISO_MINIMO, tenant_id)
    minutos = interpretar(None if fila is None else fila["aviso_minimo"], ANTICIPACION_MINIMA_MINUTOS)
    return await calcular_huecos(
        conn,
        consulta.profesional_id,
        consulta.servicio_id,
        consulta.rango,
        no_antes_de=ahora + timedelta(minutes=minutos),
    )
