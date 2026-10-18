"""Consultas del cliente y menú principal del bot (ARCHITECTURE.md §2.6).

Flujo determinístico con botones. Los datos viajan por `callback_data` con el prefijo `con:`; el botón de reservar
usa `reservar` (`CB_RESERVAR`), que atiende el flujo de reserva. El bot no cancela ni reprograma en el piloto
(§7.1): ante esos pedidos muestra el contacto del local.

Los textos del cliente solo se interpretan como atajo cuando no hay otro flujo en curso (`ctx.sesion` vacía): una
frase tipeada en medio de la reserva o del alta de identidad no se puede confundir con una consulta. Ningún texto
expone ids internos.
"""

import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

from disponibilidad import ZONA_HORARIA
from platform_notifications import Boton

from bot_app.consultas import local, repositorio
from bot_app.despachador import ContextoMensaje
from bot_app.identidad import obtener_o_crear_end_customer

CB_RESERVAR = "reservar"
CB_TURNOS = "con:turnos"
CB_SERVICIOS = "con:servicios"
CB_LOCAL = "con:local"
CB_NEGOCIO = "con:negocio"
CB_MENU = "con:menu"
PREFIJO_CB = "con:"

TEXTO_MENU = "¿Qué querés hacer?"
TEXTO_SIN_TURNOS = "No tenés turnos próximos."
TEXTO_SIN_SERVICIOS = "El local todavía no cargó sus servicios."
TEXTO_SIN_DIRECCION = "Dirección: el local todavía no la cargó."
TEXTO_SIN_HORARIOS = "Horarios: el local todavía no los cargó."
TEXTO_SIN_CONTACTO = "Contacto: el local todavía no lo cargó."
TEXTO_NEGOCIO_SIN_CONTACTO = (
    "El local todavía no cargó un dato de contacto. Probá comunicarte directamente con el local."
)

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

_ATAJOS_TURNOS = {"mis turnos", "turnos", "mis proximos turnos", "proximos turnos"}
_ATAJOS_SERVICIOS = {"servicios", "precios", "servicios y precios", "lista de precios"}
_ATAJOS_LOCAL = {"datos del local", "local", "direccion", "horarios", "ubicacion", "donde estan"}
_ATAJOS_NEGOCIO = {"hablar con el negocio", "contacto", "hablar con el local", "negocio"}
_PALABRAS_CANCELAR = {"cancelar", "cancelo", "cancelacion", "reprogramar", "reprogramo", "posponer", "anular"}
_ATAJOS_MENU = {"menu", "/menu", "/start", "inicio", "ayuda", "/ayuda"}


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.lower().split())


def botones_menu() -> list[Boton]:
    return [
        Boton("Reservar turno", CB_RESERVAR),
        Boton("Mis turnos", CB_TURNOS),
        Boton("Servicios y precios", CB_SERVICIOS),
        Boton("Datos del local", CB_LOCAL),
        Boton("Hablar con el negocio", CB_NEGOCIO),
    ]


def _responder(ctx: ContextoMensaje, texto: str) -> None:
    """Toda respuesta deja el menú a mano para seguir navegando."""
    ctx.responder(texto, botones_menu())


async def mostrar_menu_principal(ctx: ContextoMensaje) -> None:
    """Menú principal. Es la respuesta por defecto del `Despachador`: no toca la sesión."""
    ctx.responder(TEXTO_MENU, botones_menu())


def _formatear_precio(precio: Decimal) -> str:
    entero, _, decimales = f"{precio:.2f}".partition(".")
    miles = f"{int(entero):,}".replace(",", ".")
    return f"${miles}" if decimales == "00" else f"${miles},{decimales}"


def _formatear_inicio(inicio: datetime) -> str:
    local_ = inicio.astimezone(ZONA_HORARIA)
    return f"{_DIAS[local_.weekday()]} {local_.day}/{local_.month} a las {local_:%H:%M}"


async def _mis_turnos(ctx: ContextoMensaje, ahora: datetime) -> None:
    cliente = await obtener_o_crear_end_customer(ctx)
    if cliente is None:
        return  # el alta sigue en curso: ya se pidió el dato que falta
    turnos = await repositorio.proximos_turnos(ctx.conn, ctx.tenant_id, cliente.id, ahora)
    if not turnos:
        _responder(ctx, TEXTO_SIN_TURNOS)
        return
    lineas = ["Tus próximos turnos:"]
    for t in turnos:
        pendiente = " (pendiente de confirmación)" if t.status == "pending" else ""
        lineas.append(f"- {_formatear_inicio(t.inicio)}: {t.servicio} con {t.profesional}{pendiente}")
    _responder(ctx, "\n".join(lineas))


async def _servicios(ctx: ContextoMensaje) -> None:
    servicios = await repositorio.servicios_activos(ctx.conn, ctx.tenant_id)
    if not servicios:
        _responder(ctx, TEXTO_SIN_SERVICIOS)
        return
    lineas = ["Servicios y precios:"]
    lineas += [f"- {s.nombre}: {_formatear_precio(s.precio)} ({s.duracion_minutos} min)" for s in servicios]
    _responder(ctx, "\n".join(lineas))


async def _datos_local(ctx: ContextoMensaje) -> None:
    datos = await local.leer_datos_local(ctx.conn, ctx.tenant_id)
    lineas = [f"Datos de {datos.nombre}:" if datos.nombre else "Datos del local:"]
    lineas.append(f"Dirección: {datos.direccion}" if datos.direccion else TEXTO_SIN_DIRECCION)
    if datos.horarios:
        lineas.append(f"Horarios:\n{datos.horarios}" if "\n" in datos.horarios else f"Horarios: {datos.horarios}")
    else:
        lineas.append(TEXTO_SIN_HORARIOS)
    lineas.append(f"Contacto: {datos.contacto}" if datos.contacto else TEXTO_SIN_CONTACTO)
    _responder(ctx, "\n".join(lineas))


async def _hablar_con_el_negocio(ctx: ContextoMensaje) -> None:
    datos = await local.leer_datos_local(ctx.conn, ctx.tenant_id)
    if not datos.contacto:
        _responder(ctx, TEXTO_NEGOCIO_SIN_CONTACTO)
        return
    _responder(
        ctx,
        "Para hablar con el local, o para cancelar o reprogramar un turno, comunicate con ellos directamente:\n"
        f"{datos.contacto}",
    )


def _atajo_de_texto(texto: str | None) -> str | None:
    """Callback equivalente a lo que escribió el cliente, o `None` si no es una consulta."""
    t = _normalizar(texto)
    if not t:
        return None
    if t in _ATAJOS_MENU:
        return CB_MENU
    if t in _ATAJOS_TURNOS:
        return CB_TURNOS
    if t in _ATAJOS_SERVICIOS:
        return CB_SERVICIOS
    if t in _ATAJOS_LOCAL:
        return CB_LOCAL
    if t in _ATAJOS_NEGOCIO or _PALABRAS_CANCELAR & set(re.findall(r"\w+", t)):
        return CB_NEGOCIO
    return None


async def manejar_consultas(ctx: ContextoMensaje, *, ahora: datetime | None = None) -> bool:
    """Handler de consultas. `True` si atendió el mensaje; `False` si le toca a otro flujo (o al menú por defecto)."""
    m = ctx.mensaje
    if m.callback_data is not None:
        accion = m.callback_data if m.callback_data.startswith(PREFIJO_CB) else None
    elif not ctx.sesion:
        accion = _atajo_de_texto(m.texto)
    else:
        accion = None
    if accion is None:
        return False

    if accion == CB_TURNOS:
        await _mis_turnos(ctx, ahora or datetime.now(timezone.utc))  # noqa: UP017
    elif accion == CB_SERVICIOS:
        await _servicios(ctx)
    elif accion == CB_LOCAL:
        await _datos_local(ctx)
    elif accion == CB_NEGOCIO:
        await _hablar_con_el_negocio(ctx)
    else:  # CB_MENU o un botón `con:` desconocido (mensaje viejo): se vuelve al menú
        await mostrar_menu_principal(ctx)
    return True
