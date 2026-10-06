"""Flujo de reserva determinístico por botones (ARCHITECTURE.md §2.6, §7.1).

Estado en `ctx.sesion["reserva"]` (`bot_sessions`, 1 hora): `{paso, servicio_id, profesional_id, inicio, pagina}`.
Los botones llevan los ids (`rsv:s:<uuid>`, `rsv:p:<uuid>`, `rsv:h:<epoch>`), pero nada de lo que llega se da por
bueno: cada paso revalida contra la base (servicio y profesional elegibles del tenant, horario libre y futuro), y
`rsv:ok` vuelve a validar todo antes de insertar. La verdad sobre el doble booking es el exclusion constraint de
`bookings`: si otro cliente tomó el horario entre que se ofreció y se confirmó, el insert se rechaza y el bot
ofrece otros horarios en lugar de fallar.

Modos de confirmación: `auto` -> el turno nace `confirmed` y se agenda el evento de Calendar después del commit;
`approval` -> nace `pending` con `pending_expires_at` y nunca va al calendario (se sincroniza al aprobarse, desde
el dashboard); `deposit` no se ofrece en el piloto.

Cancelar o reprogramar un turno ya reservado NO lo hace el bot en el piloto (§7.1): eso lo atiende el flujo de
consultas (contacto del negocio); acá solo se descarta una reserva en curso.

Un texto que no es de la reserva devuelve `False` (aunque haya una reserva en curso) para no tapar a otros flujos;
la reserva en curso sigue viva hasta que venza la sesión o el cliente la cancele.
"""

import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

from disponibilidad import ZONA_HORARIA, Ventana, calcular_huecos
from platform_notifications import Boton

from bot_app.despachador import ContextoMensaje
from bot_app.identidad import obtener_o_crear_end_customer
from bot_app.reserva import repositorio
from bot_app.reserva.calendario import sincronizar_reserva

UTC = timezone.utc  # noqa: UP017

CLAVE = "reserva"

PASO_SERVICIO = "servicio"
PASO_PROFESIONAL = "profesional"
PASO_HORARIO = "horario"
PASO_CONFIRMAR = "confirmar"

PREFIJO_CB = "rsv:"
# Botón "Reservar turno" del menú principal (lo define el flujo de consultas, que es dueño del menú).
CB_INICIAR_DESDE_MENU = "reservar"
CB_SERVICIO = "rsv:s:"
CB_PROFESIONAL = "rsv:p:"
CB_HORARIO = "rsv:h:"
CB_MAS_HORARIOS = "rsv:m"
CB_CONFIRMAR = "rsv:ok"
CB_CANCELAR = "rsv:x"

HORIZONTE_DIAS = 14
HORARIOS_POR_PAGINA = 8
MAXIMO_SERVICIOS = 20

TEMPLATE_CONFIRMADA = "reserva_confirmada"
TEMPLATE_PENDIENTE = "reserva_pendiente"

TEXTO_ELEGIR_SERVICIO = "¿Qué servicio querés reservar?"
TEXTO_SIN_SERVICIOS = "Por ahora no hay servicios disponibles para reservar por acá."
TEXTO_ELEGIR_PROFESIONAL = "¿Con quién querés atenderte?"
TEXTO_ELEGIR_HORARIO = "Estos son los próximos horarios disponibles. Elegí uno:"
TEXTO_SIN_HORARIOS = (
    "No hay horarios disponibles en los próximos {dias} días con esa opción. Probá con otro profesional."
)
TEXTO_HORARIO_NO_DISPONIBLE = "Ese horario ya no está disponible."
TEXTO_SERVICIO_NO_DISPONIBLE = "Ese servicio ya no está disponible. Empecemos de nuevo."
TEXTO_OPCION_VENCIDA = 'Esa opción ya no está disponible. Escribí "reservar" para empezar de nuevo.'
TEXTO_RESERVA_DESCARTADA = "Listo, no reservé nada."
TEXTO_CONFIRMADO = "Listo, tu turno quedó confirmado."
TEXTO_PENDIENTE = (
    "Recibimos tu solicitud: tu turno queda pendiente de aprobación del negocio. "
    "Si no se aprueba antes del {limite}, se cancela solo."
)

_INICIAR = {
    "reservar",
    "/reservar",
    "reservar turno",
    "/turno",
    "turno",
    "nuevo turno",
    "sacar turno",
    "pedir turno",
    "quiero un turno",
    "quiero reservar",
    "quiero reservar un turno",
}
_PALABRAS_CANCELAR = {"cancelar", "cancelo", "reprogramar", "reprogramo", "anular", "anulo"}

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def ahora() -> datetime:
    return datetime.now(UTC)


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.lower().split())


def _pide_cancelar_o_reprogramar(texto: str) -> bool:
    return bool(set(texto.replace("/", " ").split()) & _PALABRAS_CANCELAR)


def _uuid(valor: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(valor)
    except ValueError:
        return None


def _formatear_fecha(momento: datetime) -> str:
    local = momento.astimezone(ZONA_HORARIA)
    return f"{_DIAS[local.weekday()]} {local:%d/%m} a las {local:%H:%M}"


def _formatear_precio(precio) -> str:
    return "$" + f"{precio:,.0f}".replace(",", ".")


def _estado(ctx: ContextoMensaje) -> dict | None:
    estado = ctx.sesion.get(CLAVE)
    return estado if isinstance(estado, dict) else None


def _boton_cancelar() -> Boton:
    return Boton("Cancelar", CB_CANCELAR)


# --- pasos: cada uno deja la sesión en el paso que pregunta y pide la respuesta --------------------------


async def _mostrar_servicios(ctx: ContextoMensaje, prefijo: str = "") -> None:
    servicios = (await repositorio.listar_servicios(ctx.conn, ctx.tenant_id))[:MAXIMO_SERVICIOS]
    if not servicios:
        ctx.sesion.pop(CLAVE, None)
        ctx.responder(prefijo + TEXTO_SIN_SERVICIOS)
        return
    ctx.sesion[CLAVE] = {"paso": PASO_SERVICIO}
    botones = [
        Boton(f"{s.nombre} ({s.duracion_minutos} min) - {_formatear_precio(s.precio)}", f"{CB_SERVICIO}{s.id}")
        for s in servicios
    ]
    ctx.responder(prefijo + TEXTO_ELEGIR_SERVICIO, [*botones, _boton_cancelar()])


async def _mostrar_profesionales(ctx: ContextoMensaje, estado: dict, prefijo: str = "") -> None:
    """Lista los profesionales del servicio elegido; si no queda ninguno, avisa y vuelve a los servicios."""
    profesionales = await repositorio.listar_profesionales(ctx.conn, ctx.tenant_id, uuid.UUID(estado["servicio_id"]))
    if not profesionales:
        await _mostrar_servicios(ctx, TEXTO_SERVICIO_NO_DISPONIBLE + "\n")
        return
    estado["paso"] = PASO_PROFESIONAL
    estado.pop("profesional_id", None)
    estado.pop("inicio", None)
    botones = [Boton(p.nombre, f"{CB_PROFESIONAL}{p.id}") for p in profesionales]
    ctx.responder(prefijo + TEXTO_ELEGIR_PROFESIONAL, [*botones, _boton_cancelar()])


async def _huecos(ctx: ContextoMensaje, estado: dict, rango: Ventana) -> list[Ventana]:
    return await calcular_huecos(ctx.conn, uuid.UUID(estado["profesional_id"]), uuid.UUID(estado["servicio_id"]), rango)


async def _mostrar_horarios(ctx: ContextoMensaje, estado: dict, prefijo: str = "") -> None:
    desde = ahora()
    huecos = await _huecos(ctx, estado, Ventana(desde, desde + timedelta(days=HORIZONTE_DIAS)))
    if not huecos:
        await _mostrar_profesionales(ctx, estado, prefijo + TEXTO_SIN_HORARIOS.format(dias=HORIZONTE_DIAS) + "\n")
        return
    pagina = estado.get("pagina", 0)
    if pagina * HORARIOS_POR_PAGINA >= len(huecos):
        pagina = 0
    estado.update(paso=PASO_HORARIO, pagina=pagina)
    estado.pop("inicio", None)
    visibles = huecos[pagina * HORARIOS_POR_PAGINA : (pagina + 1) * HORARIOS_POR_PAGINA]
    botones = [Boton(_formatear_fecha(h.inicio), f"{CB_HORARIO}{int(h.inicio.timestamp())}") for h in visibles]
    if (pagina + 1) * HORARIOS_POR_PAGINA < len(huecos):
        botones.append(Boton("Ver más horarios", CB_MAS_HORARIOS))
    ctx.responder(prefijo + TEXTO_ELEGIR_HORARIO, [*botones, _boton_cancelar()])


async def _horario_vigente(ctx: ContextoMensaje, estado: dict, inicio: datetime, duracion: int) -> bool:
    """El horario es futuro y sigue en los huecos del profesional (disponibilidad y turnos tomados)."""
    if inicio <= ahora():
        return False
    huecos = await _huecos(ctx, estado, Ventana(inicio, inicio + timedelta(minutes=duracion)))
    return any(h.inicio == inicio for h in huecos)


async def _datos(ctx: ContextoMensaje, estado: dict) -> repositorio.DatosReserva | None:
    return await repositorio.datos_reserva(
        ctx.conn, ctx.tenant_id, uuid.UUID(estado["profesional_id"]), uuid.UUID(estado["servicio_id"])
    )


def _vencida(ctx: ContextoMensaje) -> None:
    ctx.responder(TEXTO_OPCION_VENCIDA)


# --- botones ---------------------------------------------------------------------------------------------


async def _elegir_servicio(ctx: ContextoMensaje, valor: str) -> None:
    servicio_id = _uuid(valor)
    servicios = await repositorio.listar_servicios(ctx.conn, ctx.tenant_id)
    if servicio_id is None or servicio_id not in {s.id for s in servicios}:
        _vencida(ctx)
        return
    nuevo = {"paso": PASO_SERVICIO, "servicio_id": str(servicio_id)}
    ctx.sesion[CLAVE] = nuevo
    await _mostrar_profesionales(ctx, nuevo)


async def _elegir_profesional(ctx: ContextoMensaje, estado: dict, valor: str) -> None:
    profesional_id = _uuid(valor)
    profesionales = await repositorio.listar_profesionales(ctx.conn, ctx.tenant_id, uuid.UUID(estado["servicio_id"]))
    if profesional_id is None or profesional_id not in {p.id for p in profesionales}:
        _vencida(ctx)
        return
    estado.update(profesional_id=str(profesional_id), pagina=0)
    await _mostrar_horarios(ctx, estado)


async def _elegir_horario(ctx: ContextoMensaje, estado: dict, valor: str) -> None:
    datos = await _datos(ctx, estado)
    if datos is None:
        await _mostrar_servicios(ctx, TEXTO_SERVICIO_NO_DISPONIBLE + "\n")
        return
    try:
        inicio = datetime.fromtimestamp(int(valor), UTC)
    except (ValueError, OverflowError, OSError):
        _vencida(ctx)
        return
    if not await _horario_vigente(ctx, estado, inicio, datos.duracion_minutos):
        estado["pagina"] = 0
        await _mostrar_horarios(ctx, estado, TEXTO_HORARIO_NO_DISPONIBLE + "\n")
        return
    estado.update(paso=PASO_CONFIRMAR, inicio=int(inicio.timestamp()))
    texto = f"Vas a reservar {datos.servicio} con {datos.profesional} el {_formatear_fecha(inicio)}."
    if datos.modo == "approval":
        texto += " El negocio tiene que aprobar el turno antes de que quede confirmado."
    ctx.responder(texto + " ¿Confirmás?", [Boton("Confirmar", CB_CONFIRMAR), _boton_cancelar()])


async def _confirmar(ctx: ContextoMensaje, estado: dict) -> None:
    cliente = await obtener_o_crear_end_customer(ctx)
    if cliente is None:
        return
    datos = await _datos(ctx, estado)
    if datos is None:
        await _mostrar_servicios(ctx, TEXTO_SERVICIO_NO_DISPONIBLE + "\n")
        return
    inicio = datetime.fromtimestamp(estado["inicio"], UTC)
    fin = inicio + timedelta(minutes=datos.duracion_minutos)
    if not await _horario_vigente(ctx, estado, inicio, datos.duracion_minutos):
        estado["pagina"] = 0
        await _mostrar_horarios(ctx, estado, TEXTO_HORARIO_NO_DISPONIBLE + "\n")
        return
    vence = None
    if datos.modo == "approval":
        horas = await repositorio.horas_aprobacion(ctx.conn, ctx.tenant_id)
        vence = repositorio.calcular_vencimiento(ahora(), inicio, horas)
    creado = await repositorio.crear_booking(
        ctx.conn,
        tenant_id=ctx.tenant_id,
        end_customer_id=cliente.id,
        servicio_id=uuid.UUID(estado["servicio_id"]),
        profesional_id=uuid.UUID(estado["profesional_id"]),
        inicio=inicio,
        fin=fin,
        pending_expires_at=vence,
    )
    if creado is None:
        # Otro cliente lo tomó entre que se ofreció y se confirmó: el exclusion constraint decide.
        estado["pagina"] = 0
        await _mostrar_horarios(ctx, estado, TEXTO_HORARIO_NO_DISPONIBLE + "\n")
        return
    ctx.sesion.pop(CLAVE, None)
    detalle = f"{datos.servicio} con {datos.profesional}, {_formatear_fecha(inicio)}."
    if creado.status == "confirmed":
        # El evento de Calendar es lo único que sale de la base: después del commit, y nunca para un pending.
        ctx.despues_del_commit(sincronizar_reserva(ctx.tenant_id, creado.id))
        ctx.responder(f"{TEXTO_CONFIRMADO}\n{detalle}", end_customer_id=cliente.id, template=TEMPLATE_CONFIRMADA)
    else:
        limite = _formatear_fecha(creado.pending_expires_at or inicio)
        ctx.responder(
            f"{TEXTO_PENDIENTE.format(limite=limite)}\n{detalle}",
            end_customer_id=cliente.id,
            template=TEMPLATE_PENDIENTE,
        )


async def _boton(ctx: ContextoMensaje, dato: str) -> None:
    cliente = await obtener_o_crear_end_customer(ctx)
    if cliente is None:
        return
    estado = _estado(ctx)
    if dato == CB_CANCELAR:
        ctx.sesion.pop(CLAVE, None)
        ctx.responder(TEXTO_RESERVA_DESCARTADA)
        return
    if estado is None:
        _vencida(ctx)
        return
    paso = estado.get("paso")
    if dato.startswith(CB_SERVICIO):
        await _elegir_servicio(ctx, dato[len(CB_SERVICIO) :])
    elif dato.startswith(CB_PROFESIONAL) and estado.get("servicio_id"):
        await _elegir_profesional(ctx, estado, dato[len(CB_PROFESIONAL) :])
    elif dato == CB_MAS_HORARIOS and paso == PASO_HORARIO and estado.get("profesional_id"):
        estado["pagina"] = estado.get("pagina", 0) + 1
        await _mostrar_horarios(ctx, estado)
    elif dato.startswith(CB_HORARIO) and paso in (PASO_HORARIO, PASO_CONFIRMAR) and estado.get("profesional_id"):
        await _elegir_horario(ctx, estado, dato[len(CB_HORARIO) :])
    elif dato == CB_CONFIRMAR and paso == PASO_CONFIRMAR and estado.get("inicio") is not None:
        await _confirmar(ctx, estado)
    else:
        _vencida(ctx)


async def manejar_reserva(ctx: ContextoMensaje) -> bool:
    """Handler de la reserva (ver el docstring del módulo). `True` si atendió el mensaje."""
    m = ctx.mensaje
    if m.callback_data is not None and m.callback_data.startswith(PREFIJO_CB):
        await _boton(ctx, m.callback_data)
        return True
    texto = _normalizar(m.texto)
    if texto in _INICIAR or m.callback_data == CB_INICIAR_DESDE_MENU:
        if await obtener_o_crear_end_customer(ctx) is None:
            return True
        await _mostrar_servicios(ctx)
        return True
    # Con una reserva en curso, "cancelar"/"reprogramar"/"anular" la descarta. Sin reserva en curso no se atiende:
    # pedir cancelar un turno ya reservado lo resuelve el flujo de consultas (muestra el contacto del negocio).
    if _estado(ctx) is not None and _pide_cancelar_o_reprogramar(texto):
        ctx.sesion.pop(CLAVE, None)
        ctx.responder(TEXTO_RESERVA_DESCARTADA)
        return True
    return False
