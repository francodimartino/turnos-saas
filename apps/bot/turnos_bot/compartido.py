"""Lo que usan dos o más flujos del bot: texto del cliente, ids de botones, paginación y la acción de Calendar.

- `normalizar`: minúsculas, sin acentos y con los espacios colapsados, para comparar lo que escribe el cliente.
- `PALABRAS_CANCELAR` y `pide_cancelar_o_reprogramar`: la única lista de palabras que piden cancelar o reprogramar.
  La usan la reserva (descarta la reserva en curso), los cambios (descartan el movimiento en curso) y las consultas
  (llevan a "Mis turnos").
- `a_uuid`: el id de un botón, o `None` si no es un UUID.
- `HORIZONTE_DIAS` y `HORARIOS_POR_PAGINA`: cuántos días hacia adelante se ofrecen horarios y cuántos por página, en la
  reserva y al mover un turno.
- `accion_de_calendario`: el armado común de la acción posterior al commit que habla con Google Calendar.
"""

import logging
import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from platform_calendar import ErrorCalendar

from turnos_bot.despachador import AccionPosterior, Recursos

HORIZONTE_DIAS = 14
HORARIOS_POR_PAGINA = 8

PALABRAS_CANCELAR = frozenset(
    {"cancelar", "cancelo", "cancelacion", "reprogramar", "reprogramo", "posponer", "anular", "anulo"}
)


def normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.lower().split())


def pide_cancelar_o_reprogramar(texto: str | None) -> bool:
    """El texto trae alguna palabra de `PALABRAS_CANCELAR` (con o sin `/` delante)."""
    return bool(PALABRAS_CANCELAR & set(re.findall(r"\w+", normalizar(texto))))


def a_uuid(valor: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(valor)
    except ValueError:
        return None


def accion_de_calendario(
    sincronizar: Callable[[Recursos, Any], Awaitable[None]], registro: logging.Logger
) -> AccionPosterior:
    """Acción para `ctx.despues_del_commit`: abre el cliente HTTP (si el webhook no trae uno) y corre `sincronizar`.

    Un `ErrorCalendar` no se propaga: el cambio ya está confirmado. Solo se loguea el tipo de error, sin el mensaje
    ni datos del cliente.
    """

    async def accion(recursos: Recursos) -> None:
        propio = recursos.cliente_http is None
        cliente = httpx.AsyncClient(timeout=15.0) if propio else recursos.cliente_http
        try:
            await sincronizar(recursos, cliente)
        except ErrorCalendar as error:
            registro.warning("calendar_sync_fallido", extra={"error": type(error).__name__})
        finally:
            if propio:
                await cliente.aclose()

    return accion
