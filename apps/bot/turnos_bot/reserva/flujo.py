"""Flujo de reserva determinístico por botones (ARCHITECTURE.md §2.6, §7.1).

Estado en `ctx.sesion.reserva` (`SesionReserva`, `bot_sessions`, 1 hora): paso, servicio, profesional, inicio y página.
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

from platform_notifications import Boton
from turnos_dominio import Ventana, calcular_huecos, encolar_avisos_liberados
from turnos_dominio import limites as lim
from turnos_negocio import (
    PLANTILLA_RESERVA_CONFIRMADA,
    PLANTILLA_RESERVA_PENDIENTE,
    TEXTO_CONFIRMADO,
    TEXTO_PENDIENTE,
    ZONA_HORARIA,
)

from turnos_bot.despachador import ContextoMensaje
from turnos_bot.identidad import obtener_o_crear_end_customer
from turnos_bot.modelo_sesion import PASO_CONFIRMAR, PASO_HORARIO, PASO_PROFESIONAL, PASO_SERVICIO, SesionReserva
from turnos_bot.reserva import repositorio
from turnos_bot.reserva.calendario import sincronizar_reserva
from turnos_bot.reserva.liberados import enviar_avisos_liberados

UTC = timezone.utc  # noqa: UP017


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

TEMPLATE_CONFIRMADA = PLANTILLA_RESERVA_CONFIRMADA
TEMPLATE_PENDIENTE = PLANTILLA_RESERVA_PENDIENTE

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
TEXTO_TOPE_ACTIVOS = (
    "Ya tenés {tope} {turnos} {estado}, que es el máximo que se puede tener a la vez. "
    "Cuando pase o se cancele alguno, vas a poder reservar otro."
)
TEXTO_LIMITE_POR_HORA = (
    "Hiciste varias reservas en poco tiempo y llegaste al límite por hora. Probá de nuevo en un rato."
)
TEXTO_ANTICIPACION = (
    "Ese horario es demasiado pronto: hace falta reservar con al menos {minutos} minutos de anticipación."
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


def _estado(ctx: ContextoMensaje) -> SesionReserva | None:
    return ctx.sesion.reserva


def _boton_cancelar() -> Boton:
    return Boton("Cancelar", CB_CANCELAR)


# --- pasos: cada uno deja la sesión en el paso que pregunta y pide la respuesta --------------------------


async def _mostrar_servicios(ctx: ContextoMensaje, prefijo: str = "") -> None:
    servicios = (await repositorio.listar_servicios(ctx.conn, ctx.tenant_id))[:MAXIMO_SERVICIOS]
    if not servicios:
        ctx.sesion.reserva = None
        ctx.responder(prefijo + TEXTO_SIN_SERVICIOS)
        return
    ctx.sesion.reserva = SesionReserva(PASO_SERVICIO)
    botones = [
        Boton(f"{s.nombre} ({s.duracion_minutos} min) - {_formatear_precio(s.precio)}", f"{CB_SERVICIO}{s.id}")
        for s in servicios
    ]
    ctx.responder(prefijo + TEXTO_ELEGIR_SERVICIO, [*botones, _boton_cancelar()])


async def _mostrar_profesionales(ctx: ContextoMensaje, estado: SesionReserva, prefijo: str = "") -> None:
    """Lista los profesionales del servicio elegido; si no queda ninguno, avisa y vuelve a los servicios."""
    profesionales = await repositorio.listar_profesionales(ctx.conn, ctx.tenant_id, estado.servicio)
    if not profesionales:
        await _mostrar_servicios(ctx, TEXTO_SERVICIO_NO_DISPONIBLE + "\n")
        return
    estado.paso = PASO_PROFESIONAL
    estado.profesional_id = None
    estado.inicio = None
    botones = [Boton(p.nombre, f"{CB_PROFESIONAL}{p.id}") for p in profesionales]
    ctx.responder(prefijo + TEXTO_ELEGIR_PROFESIONAL, [*botones, _boton_cancelar()])


async def _huecos(
    ctx: ContextoMensaje, estado: SesionReserva, rango: Ventana, limites: lim.LimitesReserva
) -> list[Ventana]:
    """Huecos del profesional sin los que empiezan antes de `ahora + anticipación mínima` del tenant."""
    return await calcular_huecos(
        ctx.conn,
        estado.profesional,
        estado.servicio,
        rango,
        no_antes_de=ahora() + timedelta(minutes=limites.anticipacion_minutos),
    )


async def _mostrar_horarios(ctx: ContextoMensaje, estado: SesionReserva, prefijo: str = "") -> None:
    desde = ahora()
    limites = await repositorio.limites_reserva(ctx.conn, ctx.tenant_id)
    huecos = await _huecos(ctx, estado, Ventana(desde, desde + timedelta(days=HORIZONTE_DIAS)), limites)
    if not huecos:
        await _mostrar_profesionales(ctx, estado, prefijo + TEXTO_SIN_HORARIOS.format(dias=HORIZONTE_DIAS) + "\n")
        return
    pagina = estado.pagina
    if pagina * HORARIOS_POR_PAGINA >= len(huecos):
        pagina = 0
    estado.paso = PASO_HORARIO
    estado.pagina = pagina
    estado.inicio = None
    visibles = huecos[pagina * HORARIOS_POR_PAGINA : (pagina + 1) * HORARIOS_POR_PAGINA]
    botones = [Boton(_formatear_fecha(h.inicio), f"{CB_HORARIO}{int(h.inicio.timestamp())}") for h in visibles]
    if (pagina + 1) * HORARIOS_POR_PAGINA < len(huecos):
        botones.append(Boton("Ver más horarios", CB_MAS_HORARIOS))
    ctx.responder(prefijo + TEXTO_ELEGIR_HORARIO, [*botones, _boton_cancelar()])


async def _horario_vigente(ctx: ContextoMensaje, estado: SesionReserva, inicio: datetime, duracion: int) -> bool:
    """El horario es futuro y sigue en los huecos del profesional (disponibilidad y turnos tomados)."""
    limites = await repositorio.limites_reserva(ctx.conn, ctx.tenant_id)
    if inicio < ahora() + timedelta(minutes=limites.anticipacion_minutos) or inicio <= ahora():
        return False
    huecos = await _huecos(ctx, estado, Ventana(inicio, inicio + timedelta(minutes=duracion)), limites)
    return any(h.inicio == inicio for h in huecos)


async def _datos(ctx: ContextoMensaje, estado: SesionReserva) -> repositorio.DatosReserva | None:
    return await repositorio.datos_reserva(ctx.conn, ctx.tenant_id, estado.profesional, estado.servicio)


def _vencida(ctx: ContextoMensaje) -> None:
    ctx.responder(TEXTO_OPCION_VENCIDA)


# --- botones ---------------------------------------------------------------------------------------------


async def _elegir_servicio(ctx: ContextoMensaje, valor: str) -> None:
    servicio_id = _uuid(valor)
    servicios = await repositorio.listar_servicios(ctx.conn, ctx.tenant_id)
    if servicio_id is None or servicio_id not in {s.id for s in servicios}:
        _vencida(ctx)
        return
    nuevo = SesionReserva(PASO_SERVICIO, servicio_id=servicio_id)
    ctx.sesion.reserva = nuevo
    await _mostrar_profesionales(ctx, nuevo)


async def _elegir_profesional(ctx: ContextoMensaje, estado: SesionReserva, valor: str) -> None:
    profesional_id = _uuid(valor)
    profesionales = await repositorio.listar_profesionales(ctx.conn, ctx.tenant_id, estado.servicio)
    if profesional_id is None or profesional_id not in {p.id for p in profesionales}:
        _vencida(ctx)
        return
    estado.profesional_id = profesional_id
    estado.pagina = 0
    await _mostrar_horarios(ctx, estado)


async def _elegir_horario(ctx: ContextoMensaje, estado: SesionReserva, valor: str) -> None:
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
        estado.pagina = 0
        await _mostrar_horarios(ctx, estado, TEXTO_HORARIO_NO_DISPONIBLE + "\n")
        return
    estado.paso = PASO_CONFIRMAR
    estado.inicio = int(inicio.timestamp())
    texto = f"Vas a reservar {datos.servicio} con {datos.profesional} el {_formatear_fecha(inicio)}."
    if datos.modo == "approval":
        texto += " El negocio tiene que aprobar el turno antes de que quede confirmado."
    ctx.responder(texto + " ¿Confirmás?", [Boton("Confirmar", CB_CONFIRMAR), _boton_cancelar()])


async def _rechazar(ctx: ContextoMensaje, estado: SesionReserva, motivo: str, limites: lim.LimitesReserva) -> None:
    """Un límite impidió reservar: se explica en español (sin datos de otros clientes) y no se crea nada."""
    if motivo == lim.MOTIVO_ANTICIPACION:
        estado.pagina = 0
        aviso = TEXTO_ANTICIPACION.format(minutos=limites.anticipacion_minutos)
        await _mostrar_horarios(ctx, estado, f"{aviso}\n")
        return
    ctx.sesion.reserva = None
    if motivo == lim.MOTIVO_TOPE_ACTIVOS:
        tope = limites.max_activos_por_cliente
        ctx.responder(
            TEXTO_TOPE_ACTIVOS.format(
                tope=tope,
                turnos="turno" if tope == 1 else "turnos",
                estado="pendiente o confirmado" if tope == 1 else "pendientes o confirmados",
            )
        )
    else:
        ctx.responder(TEXTO_LIMITE_POR_HORA)


async def _confirmar(ctx: ContextoMensaje, estado: SesionReserva) -> None:
    cliente = await obtener_o_crear_end_customer(ctx)
    if cliente is None:
        return
    datos = await _datos(ctx, estado)
    if datos is None:
        await _mostrar_servicios(ctx, TEXTO_SERVICIO_NO_DISPONIBLE + "\n")
        return
    inicio = datetime.fromtimestamp(estado.horario, UTC)
    fin = inicio + timedelta(minutes=datos.duracion_minutos)
    if not await _horario_vigente(ctx, estado, inicio, datos.duracion_minutos):
        estado.pagina = 0
        await _mostrar_horarios(ctx, estado, TEXTO_HORARIO_NO_DISPONIBLE + "\n")
        return
    vence = None
    if datos.modo == "approval":
        horas = await repositorio.horas_aprobacion(ctx.conn, ctx.tenant_id)
        vence = repositorio.calcular_vencimiento(ahora(), inicio, horas)
    limites = await repositorio.limites_reserva(ctx.conn, ctx.tenant_id)
    try:
        creado = await repositorio.crear_booking(
            ctx.conn,
            tenant_id=ctx.tenant_id,
            end_customer_id=cliente.id,
            servicio_id=estado.servicio,
            profesional_id=estado.profesional,
            inicio=inicio,
            fin=fin,
            pending_expires_at=vence,
            limites=limites,
            ahora=ahora(),
        )
    except lim.ReservaRechazada as rechazo:
        await _rechazar(ctx, estado, rechazo.motivo, limites)
        return
    if creado is None:
        # Otro cliente lo tomó entre que se ofreció y se confirmó: el exclusion constraint decide.
        estado.pagina = 0
        await _mostrar_horarios(ctx, estado, TEXTO_HORARIO_NO_DISPONIBLE + "\n")
        return
    ctx.sesion.reserva = None
    avisos_liberados = await encolar_avisos_liberados(ctx.conn, creado.liberados)
    if avisos_liberados:
        # Pending vencidos liberados para este turno: el job ya no los ve. El aviso queda en el outbox, en esta
        # misma transacción; el envío sale tras el commit y, si el proceso muere antes, lo reenvía el job.
        ctx.despues_del_commit(enviar_avisos_liberados(avisos_liberados))
    detalle = f"{datos.servicio} con {datos.profesional}, {_formatear_fecha(inicio)}."
    # Al outbox va solo el turno (id e inicio): el texto y el destinatario se resuelven al enviar.
    params_outbox = {"booking_id": str(creado.id), "inicio": inicio.isoformat()}
    if creado.status == "confirmed":
        # El evento de Calendar es lo único que sale de la base: después del commit, y nunca para un pending.
        ctx.despues_del_commit(sincronizar_reserva(ctx.tenant_id, creado.id))
        ctx.responder(
            f"{TEXTO_CONFIRMADO}\n{detalle}",
            end_customer_id=cliente.id,
            template=TEMPLATE_CONFIRMADA,
            outbox_params=params_outbox,
        )
    else:
        limite = _formatear_fecha(creado.pending_expires_at or inicio)
        ctx.responder(
            f"{TEXTO_PENDIENTE.format(limite=limite)}\n{detalle}",
            end_customer_id=cliente.id,
            template=TEMPLATE_PENDIENTE,
            outbox_params=params_outbox,
        )


async def _boton(ctx: ContextoMensaje, dato: str) -> None:
    cliente = await obtener_o_crear_end_customer(ctx)
    if cliente is None:
        return
    estado = _estado(ctx)
    if dato == CB_CANCELAR:
        ctx.sesion.reserva = None
        ctx.responder(TEXTO_RESERVA_DESCARTADA)
        return
    if estado is None:
        _vencida(ctx)
        return
    paso = estado.paso
    if dato.startswith(CB_SERVICIO):
        await _elegir_servicio(ctx, dato[len(CB_SERVICIO) :])
    elif dato.startswith(CB_PROFESIONAL) and estado.servicio_id is not None:
        await _elegir_profesional(ctx, estado, dato[len(CB_PROFESIONAL) :])
    elif dato == CB_MAS_HORARIOS and paso == PASO_HORARIO and estado.profesional_id is not None:
        estado.pagina += 1
        await _mostrar_horarios(ctx, estado)
    elif dato.startswith(CB_HORARIO) and paso in (PASO_HORARIO, PASO_CONFIRMAR) and estado.profesional_id is not None:
        await _elegir_horario(ctx, estado, dato[len(CB_HORARIO) :])
    elif dato == CB_CONFIRMAR and paso == PASO_CONFIRMAR and estado.inicio is not None:
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
        ctx.sesion.reserva = None
        ctx.responder(TEXTO_RESERVA_DESCARTADA)
        return True
    return False
