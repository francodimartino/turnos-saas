"""Cancelar y mover un turno por botones (ARCHITECTURE.md §2.6, §7.1).

Entrada: el botón de cada turno de "Mis turnos" (`cam:t:<id>`) muestra el turno y ofrece cancelarlo o moverlo.
Cancelar pide confirmación (`cam:c:<id>` y luego `cam:cc:<id>`). Mover guarda en `ctx.sesion.cambio` (`SesionCambio`,
1 hora) el turno elegido y recorre horario y confirmación (`cam:m:<id>`, `cam:h:<epoch>`, `cam:ok`).

La política es del dominio (`turnos_dominio.cambios.cancelar_turno_cliente` y `mover_turno_cliente`): el plazo del
negocio, si mover es directo o pide aprobación, el choque de horario y el cambio ya pendiente. Este módulo no la
repite: llama y traduce cada error a un texto. Los horarios que ofrece salen de `turnos_dominio.calcular_huecos`.

Nada de lo que llega en un botón se da por bueno: el turno se vuelve a leer por tenant y por la persona que escribe
(`repositorio.turno_propio`) y el dominio lo verifica de nuevo al cambiar. Un id ajeno, inexistente o de otro negocio
recibe el mismo texto. Los pasos de mover toman el turno de la sesión, no del botón. Repetir un botón ya resuelto es
inofensivo: una cancelación repetida dice que el turno ya no se puede modificar y una confirmación repetida, que la
opción ya no está disponible, sin escribir nada.

Avisos, en la misma transacción que el cambio: al negocio (`encolar_aviso_negocio`: cancelación, movimiento o pedido
de aprobación) y al cliente (la respuesta lleva la plantilla y el turno y el pipeline del webhook la encola con
`encolar_aviso_segun_canales`). Un pedido de mover que queda esperando al negocio no lleva aviso al cliente: la
respuesta de la conversación ya le dice que el horario original se mantiene. Después del commit se sincroniza Calendar
(`calendario.sincronizar_cambio`) y se envían los avisos de los pendientes vencidos que el movimiento liberó. El bot no
envía nada al negocio por su cuenta.

Auditoría: el dominio deja una fila de `audit_log` por cada cambio; acá se fija como actor a la propia persona
(`app.current_actor_id`). Un cambio rechazado no escribe nada.
"""

import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from platform_notifications import Boton
from turnos_dominio import (
    CambioYaPendiente,
    ErrorDeTurno,
    HorarioOcupado,
    HorarioPasado,
    HorarioSinCambios,
    OperacionParaReintentar,
    PlazoVencido,
    ReservaRechazada,
    TurnoMovido,
    TurnoNoEncontrado,
    TurnoNoModificable,
    Ventana,
    calcular_huecos,
    cancelar_turno_cliente,
    encolar_aviso_negocio,
    mover_turno_cliente,
)
from turnos_dominio import limites as lim
from turnos_dominio.cambios import anticipacion_minima_minutos
from turnos_negocio import (
    PLANTILLA_AVISO_NEGOCIO_APROBACION,
    PLANTILLA_AVISO_NEGOCIO_CANCELACION,
    PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO,
)
from turnos_negocio.avisos import cuando_bot

from turnos_bot.cambios import repositorio
from turnos_bot.cambios.calendario import sincronizar_cambio
from turnos_bot.consultas import local
from turnos_bot.consultas.flujo import CB_TURNOS, PREFIJO_CAMBIAR_TURNO, botones_menu
from turnos_bot.despachador import ContextoMensaje, TurnoDelAviso
from turnos_bot.identidad import obtener_o_crear_end_customer
from turnos_bot.identidad.repositorio import EndCustomer
from turnos_bot.modelo_sesion import PASO_CAMBIO_CONFIRMAR, PASO_CAMBIO_HORARIO, SesionCambio
from turnos_bot.reserva.liberados import enviar_avisos_liberados

UTC = timezone.utc  # noqa: UP017

PREFIJO_CB = "cam:"
CB_TURNO = PREFIJO_CAMBIAR_TURNO  # cam:t:<id>
CB_CANCELAR = "cam:c:"
CB_CANCELAR_SI = "cam:cc:"
CB_MOVER = "cam:m:"
CB_HORARIO = "cam:h:"
CB_MAS_HORARIOS = "cam:mas"
CB_CONFIRMAR = "cam:ok"
CB_DESCARTAR = "cam:x"

PLANTILLA_CANCELADO = "turno_cancelado"
PLANTILLA_MOVIDO = "turno_movido"

HORIZONTE_DIAS = 14
HORARIOS_POR_PAGINA = 8

TEXTO_TURNO_NO_DISPONIBLE = "Ese turno ya no está disponible. Mirá tus próximos turnos para ver cuáles tenés."
TEXTO_NO_MODIFICABLE = "Ese turno ya estaba cancelado o ya no se puede modificar."
TEXTO_REINTENTAR = "No pude hacerlo en este momento. Probá de nuevo en unos segundos."
TEXTO_CAMBIO_YA_PEDIDO = (
    "Ya pediste mover este turno y el negocio todavía no respondió. Tu horario original se mantiene hasta que conteste."
)
TEXTO_OPCION_VENCIDA = 'Esa opción ya no está disponible. Entrá a "Mis turnos" para empezar de nuevo.'
TEXTO_SIN_CAMBIOS = "Listo, no cambié nada."
TEXTO_HORARIO_NO_DISPONIBLE = "Ese horario ya no está disponible."
TEXTO_MISMO_HORARIO = "Ese ya es el horario de tu turno."
TEXTO_ANTICIPACION = (
    "Ese horario es demasiado pronto: hace falta pedirlo con al menos {minutos} minutos de anticipación."
)
TEXTO_SIN_HORARIOS = (
    "No hay horarios disponibles en los próximos {dias} días para mover tu turno. "
    "Probá más adelante o comunicate con el negocio."
)
TEXTO_ELEGIR_HORARIO = "Estos son los próximos horarios disponibles para tu turno. Elegí uno:"
TEXTO_NO_SE_PUDO = "No pude hacer ese cambio. Comunicate con el negocio."
TEXTO_SIN_CONTACTO = "Comunicate directamente con el negocio."

_PALABRAS_CANCELAR = {"cancelar", "cancelo", "cancelacion", "reprogramar", "reprogramo", "anular", "anulo"}


def ahora() -> datetime:
    return datetime.now(UTC)


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.lower().split())


def _uuid(valor: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(valor)
    except ValueError:
        return None


def _responder(ctx: ContextoMensaje, texto: str, antes: list[Boton] | None = None, **aviso: Any) -> None:
    """Toda respuesta deja el menú a mano; `antes` pone primero los botones propios de la respuesta."""
    ctx.responder(texto, [*(antes or []), *botones_menu()], **aviso)


def _horas(cantidad: int) -> str:
    return "1 hora" if cantidad == 1 else f"{cantidad} horas"


async def _texto_plazo(ctx: ContextoMensaje, accion: str, horas_plazo: int) -> str:
    """Pasado el plazo del negocio: cuántas horas hacen falta y cómo contactarlo."""
    contacto = (await local.leer_datos_local(ctx.conn, ctx.tenant_id)).contacto
    if horas_plazo > 0:
        motivo = f"hace falta hacerlo con al menos {_horas(horas_plazo)} de anticipación"
    else:
        motivo = "el turno ya empezó o pasó"
    pedido = f"Comunicate con el negocio: {contacto}" if contacto else TEXTO_SIN_CONTACTO
    return f"Este turno ya no se puede {accion} por acá: {motivo}. {pedido}"


async def _actuar_como(ctx: ContextoMensaje, cliente: EndCustomer) -> None:
    """El actor del `audit_log` es la propia persona (el dominio audita con el contexto de la transacción)."""
    await ctx.conn.execute("select set_config('app.current_actor_id', $1, true)", str(cliente.id))


def _descripcion(turno: repositorio.TurnoPropio) -> str:
    return f"{turno.servicio} con {turno.profesional}, {cuando_bot(turno.inicio)}"


async def _turno_modificable(
    ctx: ContextoMensaje, cliente: EndCustomer, booking_id: uuid.UUID | None
) -> repositorio.TurnoPropio | None:
    """El turno propio y modificable, o `None` tras responder por qué no (ajeno e inexistente dan el mismo texto)."""
    turno = None
    if booking_id is not None:
        turno = await repositorio.turno_propio(ctx.conn, ctx.tenant_id, cliente.id, booking_id)
    if turno is None or not turno.modificable:
        _responder(ctx, TEXTO_TURNO_NO_DISPONIBLE if turno is None else TEXTO_NO_MODIFICABLE)
        return None
    return turno


# --- cancelar ---------------------------------------------------------------------------------------------


async def _mostrar_turno(ctx: ContextoMensaje, cliente: EndCustomer, valor: str) -> None:
    turno = await _turno_modificable(ctx, cliente, _uuid(valor))
    if turno is None:
        return
    ctx.sesion.cambio = None
    pendiente = " (pendiente de confirmación)" if turno.estado == "pending" else ""
    botones = [
        Boton("Cancelar turno", f"{CB_CANCELAR}{turno.id}"),
        Boton("Mover turno", f"{CB_MOVER}{turno.id}"),
        Boton("Volver a mis turnos", CB_TURNOS),
    ]
    ctx.responder(f"Tu turno: {_descripcion(turno)}{pendiente}. ¿Qué querés hacer?", botones)


async def _pedir_confirmacion_de_cancelar(ctx: ContextoMensaje, cliente: EndCustomer, valor: str) -> None:
    turno = await _turno_modificable(ctx, cliente, _uuid(valor))
    if turno is None:
        return
    ctx.sesion.cambio = None
    botones = [Boton("Sí, cancelar", f"{CB_CANCELAR_SI}{turno.id}"), Boton("No, dejarlo", f"{CB_TURNO}{turno.id}")]
    ctx.responder(f"¿Confirmás que querés cancelar tu turno de {_descripcion(turno)}?", botones)


async def _cancelar(ctx: ContextoMensaje, cliente: EndCustomer, valor: str) -> None:
    turno = await _turno_modificable(ctx, cliente, _uuid(valor))
    if turno is None:
        return
    ctx.sesion.cambio = None
    await _actuar_como(ctx, cliente)
    try:
        cancelado = await cancelar_turno_cliente(
            ctx.conn,
            tenant_id=ctx.tenant_id,
            end_customer_id=cliente.id,
            booking_id=turno.id,
            canal="bot",
            ahora=ahora(),
        )
    except PlazoVencido as error:
        _responder(ctx, await _texto_plazo(ctx, "cancelar", error.horas_plazo))
        return
    except TurnoNoEncontrado:
        _responder(ctx, TEXTO_TURNO_NO_DISPONIBLE)
        return
    except TurnoNoModificable:
        _responder(ctx, TEXTO_NO_MODIFICABLE)
        return
    except OperacionParaReintentar:
        _responder(ctx, TEXTO_REINTENTAR)
        return
    except ErrorDeTurno:
        _responder(ctx, TEXTO_NO_SE_PUDO)
        return
    await encolar_aviso_negocio(
        ctx.conn, plantilla=PLANTILLA_AVISO_NEGOCIO_CANCELACION, booking_id=turno.id, inicio=cancelado.inicio
    )
    ctx.despues_del_commit(sincronizar_cambio(ctx.tenant_id, turno.id))
    _responder(
        ctx,
        f"Listo, cancelamos tu turno de {turno.servicio} con {turno.profesional} del {cuando_bot(cancelado.inicio)}.",
        end_customer_id=cliente.id,
        template=PLANTILLA_CANCELADO,
        outbox_turno=TurnoDelAviso(turno.id, cancelado.inicio),
    )


# --- mover ------------------------------------------------------------------------------------------------


def _boton_descartar() -> Boton:
    return Boton("No mover", CB_DESCARTAR)


async def _huecos(
    ctx: ContextoMensaje, turno: repositorio.TurnoPropio, rango: Ventana, anticipacion_minutos: int
) -> list[Ventana]:
    return await calcular_huecos(
        ctx.conn,
        turno.profesional_id,
        turno.servicio_id,
        rango,
        no_antes_de=ahora() + timedelta(minutes=anticipacion_minutos),
    )


async def _mostrar_horarios(
    ctx: ContextoMensaje, estado: SesionCambio, turno: repositorio.TurnoPropio, prefijo: str = ""
) -> None:
    desde = ahora()
    anticipacion = await anticipacion_minima_minutos(ctx.conn, ctx.tenant_id)
    huecos = await _huecos(ctx, turno, Ventana(desde, desde + timedelta(days=HORIZONTE_DIAS)), anticipacion)
    if not huecos:
        ctx.sesion.cambio = None
        _responder(ctx, prefijo + TEXTO_SIN_HORARIOS.format(dias=HORIZONTE_DIAS))
        return
    pagina = estado.pagina
    if pagina * HORARIOS_POR_PAGINA >= len(huecos):
        pagina = 0
    estado.paso = PASO_CAMBIO_HORARIO
    estado.pagina = pagina
    estado.inicio = None
    visibles = huecos[pagina * HORARIOS_POR_PAGINA : (pagina + 1) * HORARIOS_POR_PAGINA]
    botones = [Boton(cuando_bot(h.inicio), f"{CB_HORARIO}{int(h.inicio.timestamp())}") for h in visibles]
    if (pagina + 1) * HORARIOS_POR_PAGINA < len(huecos):
        botones.append(Boton("Ver más horarios", CB_MAS_HORARIOS))
    ctx.responder(prefijo + TEXTO_ELEGIR_HORARIO, [*botones, _boton_descartar()])


async def _empezar_mover(ctx: ContextoMensaje, cliente: EndCustomer, valor: str) -> None:
    turno = await _turno_modificable(ctx, cliente, _uuid(valor))
    if turno is None:
        return
    estado = SesionCambio(PASO_CAMBIO_HORARIO, turno.id)
    ctx.sesion.cambio = estado
    await _mostrar_horarios(ctx, estado, turno)


async def _turno_de_la_sesion(
    ctx: ContextoMensaje, cliente: EndCustomer
) -> tuple[SesionCambio, repositorio.TurnoPropio] | None:
    """El turno que se está moviendo (el de la sesión, no el de un botón), o `None` tras decir que no sigue vigente."""
    estado = ctx.sesion.cambio
    if estado is None:
        _responder(ctx, TEXTO_OPCION_VENCIDA)
        return None
    turno = await _turno_modificable(ctx, cliente, estado.turno)
    if turno is None:
        ctx.sesion.cambio = None  # el turno de la sesión ya no se puede mover: no queda nada por seguir
        return None
    return estado, turno


async def _mas_horarios(ctx: ContextoMensaje, cliente: EndCustomer) -> None:
    actual = await _turno_de_la_sesion(ctx, cliente)
    if actual is None:
        return
    estado, turno = actual
    if estado.paso != PASO_CAMBIO_HORARIO:
        _responder(ctx, TEXTO_OPCION_VENCIDA)
        return
    estado.pagina += 1
    await _mostrar_horarios(ctx, estado, turno)


async def _elegir_horario(ctx: ContextoMensaje, cliente: EndCustomer, valor: str) -> None:
    actual = await _turno_de_la_sesion(ctx, cliente)
    if actual is None:
        return
    estado, turno = actual
    try:
        inicio = datetime.fromtimestamp(int(valor), UTC)
    except (ValueError, OverflowError, OSError):
        _responder(ctx, TEXTO_OPCION_VENCIDA)
        return
    anticipacion = await anticipacion_minima_minutos(ctx.conn, ctx.tenant_id)
    huecos = await _huecos(ctx, turno, Ventana(inicio, inicio + (turno.fin - turno.inicio)), anticipacion)
    if not any(h.inicio == inicio for h in huecos):
        estado.pagina = 0
        await _mostrar_horarios(ctx, estado, turno, TEXTO_HORARIO_NO_DISPONIBLE + "\n")
        return
    estado.paso = PASO_CAMBIO_CONFIRMAR
    estado.inicio = int(inicio.timestamp())
    ctx.responder(
        f"Vas a mover tu turno de {turno.servicio} con {turno.profesional} del {cuando_bot(turno.inicio)} "
        f"al {cuando_bot(inicio)}. ¿Confirmás?",
        [Boton("Confirmar", CB_CONFIRMAR), _boton_descartar()],
    )


async def _volver_a_horarios(
    ctx: ContextoMensaje, estado: SesionCambio, turno: repositorio.TurnoPropio, aviso: str
) -> None:
    estado.pagina = 0
    await _mostrar_horarios(ctx, estado, turno, aviso + "\n")


async def _confirmar_mover(ctx: ContextoMensaje, cliente: EndCustomer) -> None:
    actual = await _turno_de_la_sesion(ctx, cliente)
    if actual is None:
        return
    estado, turno = actual
    if estado.paso != PASO_CAMBIO_CONFIRMAR or estado.inicio is None:
        _responder(ctx, TEXTO_OPCION_VENCIDA)
        return
    nuevo_inicio = datetime.fromtimestamp(estado.inicio, UTC)
    await _actuar_como(ctx, cliente)
    try:
        movido = await mover_turno_cliente(
            ctx.conn,
            tenant_id=ctx.tenant_id,
            end_customer_id=cliente.id,
            booking_id=turno.id,
            nuevo_inicio=nuevo_inicio,
            canal="bot",
            ahora=ahora(),
        )
    except (HorarioOcupado, HorarioPasado):
        await _volver_a_horarios(ctx, estado, turno, TEXTO_HORARIO_NO_DISPONIBLE)
        return
    except HorarioSinCambios:
        await _volver_a_horarios(ctx, estado, turno, TEXTO_MISMO_HORARIO)
        return
    except ReservaRechazada as rechazo:
        if rechazo.motivo != lim.MOTIVO_ANTICIPACION:
            ctx.sesion.cambio = None
            _responder(ctx, TEXTO_NO_SE_PUDO)
            return
        minutos = await anticipacion_minima_minutos(ctx.conn, ctx.tenant_id)
        await _volver_a_horarios(ctx, estado, turno, TEXTO_ANTICIPACION.format(minutos=minutos))
        return
    except OperacionParaReintentar:
        _responder(ctx, TEXTO_REINTENTAR, [Boton("Reintentar", CB_CONFIRMAR), _boton_descartar()])
        return
    except (PlazoVencido, CambioYaPendiente, TurnoNoEncontrado, TurnoNoModificable, ErrorDeTurno) as error:
        ctx.sesion.cambio = None
        if isinstance(error, PlazoVencido):
            _responder(ctx, await _texto_plazo(ctx, "mover", error.horas_plazo))
        else:
            _responder(ctx, _texto_de_error(error))
        return
    ctx.sesion.cambio = None
    if movido.liberados:
        ctx.despues_del_commit(enviar_avisos_liberados(movido.liberados))
    if movido.resultado == "pendiente_de_aprobacion":
        await _responder_pendiente(ctx, turno, movido, nuevo_inicio)
        return
    await encolar_aviso_negocio(
        ctx.conn, plantilla=PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO, booking_id=turno.id, inicio=movido.inicio
    )
    ctx.despues_del_commit(sincronizar_cambio(ctx.tenant_id, turno.id))
    _responder(
        ctx,
        f"Listo, moví tu turno de {turno.servicio} con {turno.profesional}: ahora es el {cuando_bot(movido.inicio)}.",
        end_customer_id=cliente.id,
        template=PLANTILLA_MOVIDO,
        outbox_turno=TurnoDelAviso(turno.id, movido.inicio),
    )


def _texto_de_error(error: ErrorDeTurno) -> str:
    if isinstance(error, CambioYaPendiente):
        return TEXTO_CAMBIO_YA_PEDIDO
    if isinstance(error, TurnoNoEncontrado):
        return TEXTO_TURNO_NO_DISPONIBLE
    if isinstance(error, TurnoNoModificable):
        return TEXTO_NO_MODIFICABLE
    return TEXTO_NO_SE_PUDO


async def _responder_pendiente(
    ctx: ContextoMensaje, turno: repositorio.TurnoPropio, movido: TurnoMovido, nuevo_inicio: datetime
) -> None:
    """Cambio a la espera del negocio: el horario original sigue reservado y Calendar no cambia."""
    propuesto = movido.inicio_propuesto or nuevo_inicio
    await encolar_aviso_negocio(
        ctx.conn, plantilla=PLANTILLA_AVISO_NEGOCIO_APROBACION, booking_id=turno.id, inicio=propuesto
    )
    texto = (
        f"Le pedimos el cambio al negocio. Tu turno de {turno.servicio} con {turno.profesional} sigue siendo el "
        f"{cuando_bot(movido.inicio)} hasta que responda; si lo aprueba, pasa al {cuando_bot(propuesto)}."
    )
    if movido.cambio_pendiente_expira_at is not None:
        texto += f" Si no contesta antes del {cuando_bot(movido.cambio_pendiente_expira_at)}, el pedido vence."
    _responder(ctx, texto + " Mientras tanto, el horario original se mantiene.")


# --- botones ----------------------------------------------------------------------------------------------


async def _boton(ctx: ContextoMensaje, dato: str) -> None:
    cliente = await obtener_o_crear_end_customer(ctx)
    if cliente is None:
        return  # el alta sigue en curso: ya se pidió el dato que falta
    if dato == CB_DESCARTAR:
        ctx.sesion.cambio = None
        _responder(ctx, TEXTO_SIN_CAMBIOS)
    elif dato.startswith(CB_TURNO):
        await _mostrar_turno(ctx, cliente, dato[len(CB_TURNO) :])
    elif dato.startswith(CB_CANCELAR):
        await _pedir_confirmacion_de_cancelar(ctx, cliente, dato[len(CB_CANCELAR) :])
    elif dato.startswith(CB_CANCELAR_SI):
        await _cancelar(ctx, cliente, dato[len(CB_CANCELAR_SI) :])
    elif dato.startswith(CB_MOVER):
        await _empezar_mover(ctx, cliente, dato[len(CB_MOVER) :])
    elif dato == CB_MAS_HORARIOS:
        await _mas_horarios(ctx, cliente)
    elif dato.startswith(CB_HORARIO):
        await _elegir_horario(ctx, cliente, dato[len(CB_HORARIO) :])
    elif dato == CB_CONFIRMAR:
        await _confirmar_mover(ctx, cliente)
    else:  # un botón `cam:` que ya no existe (mensaje viejo)
        _responder(ctx, TEXTO_OPCION_VENCIDA)


async def manejar_cambios(ctx: ContextoMensaje) -> bool:
    """Handler de cancelar y mover (ver el docstring del módulo). `True` si atendió el mensaje."""
    m = ctx.mensaje
    if m.callback_data is not None and m.callback_data.startswith(PREFIJO_CB):
        await _boton(ctx, m.callback_data)
        return True
    # Con un movimiento en curso, "cancelar" lo deja sin efecto. Fuera de eso el texto no es de este flujo.
    if ctx.sesion.cambio is not None and set(_normalizar(m.texto).replace("/", " ").split()) & _PALABRAS_CANCELAR:
        ctx.sesion.cambio = None
        _responder(ctx, TEXTO_SIN_CAMBIOS)
        return True
    return False
