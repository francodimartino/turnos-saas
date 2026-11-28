"""Cancelar y mover un turno por el bot, con el dominio y la disponibilidad en memoria (sin base de datos).

La política (plazo, aprobación, choques) se prueba en `turnos_dominio`; acá se prueba que el bot la invoque, traduzca
cada error del dominio a un texto y deje los avisos. El aislamiento por persona y por tenant, la auditoría y el
efecto real en la base están en `test_cambios_integracion.py`. Nombres y teléfonos inventados.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from platform_notifications import MensajeEntrante
from turnos_dominio import (
    AvisoLiberado,
    CambioYaPendiente,
    ErrorDeTurno,
    HorarioOcupado,
    HorarioPasado,
    HorarioSinCambios,
    Liberado,
    OperacionParaReintentar,
    PlazoVencido,
    ReservaRechazada,
    TurnoCancelado,
    TurnoMovido,
    TurnoNoEncontrado,
    TurnoNoModificable,
    Ventana,
    limites,
)
from turnos_negocio import (
    PLANTILLA_AVISO_NEGOCIO_APROBACION,
    PLANTILLA_AVISO_NEGOCIO_CANCELACION,
    PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO,
)
from turnos_negocio.avisos import cuando_bot

from turnos_bot import ContextoMensaje, Despachador
from turnos_bot.cambios import flujo, repositorio
from turnos_bot.cambios.repositorio import TurnoPropio
from turnos_bot.consultas import flujo as consultas
from turnos_bot.consultas import local
from turnos_bot.despachador import TurnoDelAviso
from turnos_bot.flujos import handlers_por_defecto
from turnos_bot.identidad.repositorio import EndCustomer
from turnos_bot.modelo_sesion import PASO_CAMBIO_CONFIRMAR, PASO_CAMBIO_HORARIO, SesionBot, SesionCambio, SesionReserva
from turnos_bot.reserva import flujo as reserva

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
AHORA = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CLIENTE = EndCustomer(uuid.uuid4(), TENANT, "+5491100000000", "Ana Prueba")
INICIO = AHORA + timedelta(days=3)  # el turno propio: jueves 8/10 a las 09:00 en Buenos Aires
FIN = INICIO + timedelta(minutes=30)
SERVICIO, PROFESIONAL = uuid.uuid4(), uuid.uuid4()


def turno(estado: str, id_: uuid.UUID | None = None) -> TurnoPropio:
    return TurnoPropio(id_ or uuid.uuid4(), estado, INICIO, FIN, SERVICIO, PROFESIONAL, "Corte", "Lucía")


TURNO, PENDIENTE, CANCELADO = turno("confirmed"), turno("pending"), turno("cancelled")
AJENO = uuid.uuid4()  # el id de un turno que no es de esta persona (ni del tenant): el repositorio no lo devuelve
MENU = [b.dato for b in consultas.botones_menu()]


def hueco(horas: int, minutos: int = 30) -> Ventana:
    inicio = AHORA + timedelta(hours=horas)
    return Ventana(inicio, inicio + timedelta(minutes=minutos))


class ConnFalsa:
    def __init__(self) -> None:
        self.ejecutados: list[tuple] = []

    async def execute(self, sql, *args):
        self.ejecutados.append((sql, args))
        return "SELECT 1"


ACTOR = ("select set_config('app.current_actor_id', $1, true)", (str(CLIENTE.id),))


class Escenario:
    """Arma el contexto de cada mensaje sobre una misma sesión, como hace el webhook."""

    def __init__(self, monkeypatch) -> None:
        self.conn = ConnFalsa()
        self.turnos = {t.id: t for t in (TURNO, PENDIENTE, CANCELADO)}  # los de CLIENTE
        self.cliente: EndCustomer | None = CLIENTE
        self.sesion = SesionBot()
        self.huecos: list[Ventana] = [hueco(80), hueco(81), hueco(100)]
        self.anticipacion = 30
        self.plazo = 24
        self.contacto: str | None = "Tel. 11 5555-0000"
        self.cancelaciones: list[dict] = []
        self.movimientos: list[dict] = []
        self.avisos_negocio: list[dict] = []
        self.pedidos_huecos: list[tuple] = []
        self.error_cancelar: Exception | None = None
        self.error_mover: Exception | None = None
        self.resultado_mover: TurnoMovido | None = None
        monkeypatch.setattr(flujo.repositorio, "turno_propio", self._turno_propio)
        monkeypatch.setattr(flujo, "cancelar_turno_cliente", self._cancelar)
        monkeypatch.setattr(flujo, "mover_turno_cliente", self._mover)
        monkeypatch.setattr(flujo, "calcular_huecos", self._calcular_huecos)
        monkeypatch.setattr(flujo, "encolar_aviso_negocio", self._aviso_negocio)
        monkeypatch.setattr(flujo, "anticipacion_minima_minutos", self._anticipacion)
        monkeypatch.setattr(flujo, "plazo_cancelacion_horas", self._plazo)
        monkeypatch.setattr(flujo, "obtener_o_crear_end_customer", self._cliente)
        monkeypatch.setattr(flujo, "ahora", lambda: AHORA)
        monkeypatch.setattr(flujo.local, "leer_datos_local", self._datos_local)

    async def _turno_propio(self, conn, tenant_id, end_customer_id, booking_id):
        assert tenant_id == TENANT and end_customer_id == CLIENTE.id
        return self.turnos.get(booking_id)

    async def _cancelar(self, conn, **datos):
        self.cancelaciones.append(datos)
        if self.error_cancelar:
            raise self.error_cancelar
        return TurnoCancelado(datos["booking_id"], INICIO, FIN, "confirmed")

    async def _mover(self, conn, **datos):
        self.movimientos.append(datos)
        if self.error_mover:
            raise self.error_mover
        if self.resultado_mover:
            return self.resultado_mover
        nuevo = datos["nuevo_inicio"]
        return TurnoMovido(datos["booking_id"], "movido", nuevo, nuevo + timedelta(minutes=30))

    async def _calcular_huecos(self, conn, profesional_id, servicio_id, rango, no_antes_de=None, **_):
        self.pedidos_huecos.append((profesional_id, servicio_id, rango, no_antes_de))
        piso = max(rango.inicio, no_antes_de or rango.inicio)
        return [h for h in self.huecos if h.inicio >= piso and h.fin <= rango.fin]

    async def _aviso_negocio(self, conn, **datos):
        self.avisos_negocio.append(datos)
        return uuid.uuid4()

    async def _plazo(self, conn, tenant_id):
        return self.plazo

    async def _anticipacion(self, conn, tenant_id):
        return self.anticipacion

    async def _datos_local(self, conn, tenant_id):
        return local.DatosLocal("Estética", None, None, self.contacto)

    async def _cliente(self, ctx):
        if self.cliente is None:
            ctx.responder("dame tu teléfono")
        return self.cliente

    async def enviar(self, texto: str | None = None, dato: str | None = None) -> ContextoMensaje:
        mensaje = MensajeEntrante("telegram", "111", "111", "u", texto=texto, callback_data=dato)
        ctx = ContextoMensaje(self.conn, TENANT, mensaje, self.sesion)
        ctx.atendido = await flujo.manejar_cambios(ctx)
        return ctx


@pytest.fixture
def esc(monkeypatch):
    return Escenario(monkeypatch)


def botones(ctx: ContextoMensaje) -> list[str]:
    return [b.dato for r in ctx.respuestas for b in r.botones]


def textos(ctx: ContextoMensaje) -> str:
    return "\n".join(r.texto for r in ctx.respuestas)


def sin_efectos(esc: Escenario, ctx: ContextoMensaje) -> bool:
    """No llegó al dominio, no encoló avisos ni agendó nada para después del commit."""
    return not (esc.cancelaciones or esc.movimientos or esc.avisos_negocio or ctx.posteriores)


def horarios_de(ctx: ContextoMensaje) -> list[str]:
    return [b for b in botones(ctx) if b.startswith("cam:h:")]


def dato_horario(h: Ventana) -> str:
    return f"cam:h:{int(h.inicio.timestamp())}"


async def hasta_horarios(esc: Escenario) -> ContextoMensaje:
    return await esc.enviar(dato=f"cam:m:{TURNO.id}")


async def hasta_confirmar(esc: Escenario, elegido: Ventana | None = None) -> ContextoMensaje:
    await hasta_horarios(esc)
    return await esc.enviar(dato=dato_horario(elegido or esc.huecos[0]))


# --- ver el turno y cancelar -------------------------------------------------------------------------------


async def test_el_boton_de_un_turno_ofrece_cancelar_y_mover(esc):
    ctx = await esc.enviar(dato=f"cam:t:{TURNO.id}")
    assert ctx.atendido
    assert botones(ctx) == [f"cam:c:{TURNO.id}", f"cam:m:{TURNO.id}", "con:turnos"]
    assert "Corte con Lucía, jueves 08/10 a las 09:00" in textos(ctx)
    assert sin_efectos(esc, ctx)


async def test_un_turno_pendiente_se_muestra_como_pendiente(esc):
    ctx = await esc.enviar(dato=f"cam:t:{PENDIENTE.id}")
    assert "(pendiente de confirmación)" in textos(ctx)


@pytest.mark.parametrize("dato", ["cam:t:", "cam:t:no-es-uuid", f"cam:t:{AJENO}", f"cam:t:{CANCELADO.id}"])
async def test_un_turno_ajeno_inexistente_o_cancelado_no_se_ofrece(esc, dato):
    ctx = await esc.enviar(dato=dato)
    assert ctx.atendido and botones(ctx) == MENU
    assert textos(ctx) in (flujo.TEXTO_TURNO_NO_DISPONIBLE, flujo.TEXTO_NO_MODIFICABLE)
    assert sin_efectos(esc, ctx)


async def test_un_turno_ajeno_y_uno_inexistente_dan_el_mismo_texto(esc):
    ajeno = textos(await esc.enviar(dato=f"cam:t:{AJENO}"))
    inexistente = textos(await esc.enviar(dato=f"cam:t:{uuid.uuid4()}"))
    assert ajeno == inexistente == flujo.TEXTO_TURNO_NO_DISPONIBLE


async def test_cancelar_pide_confirmacion_y_no_escribe(esc):
    ctx = await esc.enviar(dato=f"cam:c:{TURNO.id}")
    assert botones(ctx) == [f"cam:cc:{TURNO.id}", f"cam:t:{TURNO.id}"]
    assert "¿Confirmás que querés cancelar" in textos(ctx) and sin_efectos(esc, ctx)


async def test_cancelar_dentro_del_plazo_cancela_avisa_al_negocio_y_al_cliente_y_sincroniza_calendar(esc):
    ctx = await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    (llamada,) = esc.cancelaciones
    assert llamada == {
        "tenant_id": TENANT,
        "end_customer_id": CLIENTE.id,
        "booking_id": TURNO.id,
        "canal": "bot",
        "ahora": AHORA,
    }
    assert esc.conn.ejecutados == [ACTOR]  # la propia persona es el actor del audit_log
    assert esc.avisos_negocio == [
        {"plantilla": PLANTILLA_AVISO_NEGOCIO_CANCELACION, "booking_id": TURNO.id, "inicio": INICIO}
    ]
    (respuesta,) = ctx.respuestas
    assert respuesta.texto == "Listo, cancelamos tu turno de Corte con Lucía del jueves 08/10 a las 09:00."
    assert (respuesta.end_customer_id, respuesta.template) == (CLIENTE.id, "turno_cancelado")
    assert respuesta.outbox_turno == TurnoDelAviso(TURNO.id, INICIO)
    assert len(ctx.posteriores) == 1  # Calendar, después del commit
    assert esc.sesion.cambio is None


@pytest.mark.parametrize(
    ("horas", "motivo"),
    [
        (24, "hace falta hacerlo con al menos 24 horas de anticipación"),
        (1, "hace falta hacerlo con al menos 1 hora de anticipación"),
        (0, "el turno ya empezó o pasó"),
    ],
)
async def test_cancelar_pasado_el_plazo_en_el_dominio_dice_las_horas_y_el_contacto_del_negocio(esc, horas, motivo):
    esc.error_cancelar = PlazoVencido(horas)
    ctx = await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    assert (
        textos(ctx)
        == f"Este turno ya no se puede cancelar por acá: {motivo}. Comunicate con el negocio: Tel. 11 5555-0000"
    )
    assert botones(ctx) == MENU
    assert esc.avisos_negocio == [] and ctx.posteriores == []


async def test_cancelar_pasado_el_plazo_sin_contacto_cargado_igual_orienta(esc):
    esc.error_cancelar = PlazoVencido(24)
    esc.contacto = None
    ctx = await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    assert flujo.TEXTO_SIN_CONTACTO in textos(ctx)


@pytest.mark.parametrize(
    ("error", "texto"),
    [
        (TurnoNoEncontrado("x"), flujo.TEXTO_TURNO_NO_DISPONIBLE),
        (TurnoNoModificable("cancelled"), flujo.TEXTO_NO_MODIFICABLE),
        (OperacionParaReintentar("40P01"), flujo.TEXTO_REINTENTAR),
        (ErrorDeTurno("otro"), flujo.TEXTO_NO_SE_PUDO),
    ],
)
async def test_cada_error_del_dominio_al_cancelar_tiene_su_texto_y_no_deja_avisos(esc, error, texto):
    esc.error_cancelar = error
    ctx = await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    assert textos(ctx) == texto
    assert esc.avisos_negocio == [] and ctx.posteriores == []
    assert all(r.template is None and r.outbox_turno is None for r in ctx.respuestas)


@pytest.mark.parametrize("dato", [f"cam:cc:{AJENO}", f"cam:cc:{CANCELADO.id}", "cam:cc:basura"])
async def test_forjar_la_cancelacion_de_un_turno_ajeno_o_ya_cancelado_no_llega_al_dominio(esc, dato):
    ctx = await esc.enviar(dato=dato)
    assert sin_efectos(esc, ctx) and esc.conn.ejecutados == []


async def test_el_doble_toque_de_cancelar_es_inofensivo(esc):
    await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    esc.turnos[TURNO.id] = turno("cancelled", TURNO.id)  # lo que ve la base tras la primera cancelación
    ctx = await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    assert textos(ctx) == flujo.TEXTO_NO_MODIFICABLE
    assert len(esc.cancelaciones) == 1 and len(esc.avisos_negocio) == 1 and ctx.posteriores == []


async def test_con_el_alta_en_curso_no_se_cancela_nada(esc):
    esc.cliente = None
    ctx = await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    assert ctx.atendido and textos(ctx) == "dame tu teléfono" and sin_efectos(esc, ctx)


# --- mover ---------------------------------------------------------------------------------------------------


async def test_mover_ofrece_los_huecos_del_profesional_y_el_servicio_del_turno(esc):
    ctx = await hasta_horarios(esc)
    assert horarios_de(ctx) == [dato_horario(h) for h in esc.huecos]
    assert botones(ctx)[-1] == "cam:x"
    assert esc.sesion.cambio == SesionCambio(PASO_CAMBIO_HORARIO, TURNO.id)
    profesional, servicio, rango, no_antes_de = esc.pedidos_huecos[0]
    assert (profesional, servicio) == (PROFESIONAL, SERVICIO)
    assert rango.fin - rango.inicio == timedelta(days=flujo.HORIZONTE_DIAS)
    assert no_antes_de == AHORA + timedelta(minutes=30)  # la anticipación mínima del negocio
    assert esc.movimientos == []


async def test_mover_sin_horarios_disponibles_lo_dice_y_no_deja_sesion(esc):
    esc.huecos = []
    ctx = await hasta_horarios(esc)
    assert "No hay horarios disponibles en los próximos 14 días" in textos(ctx)
    assert esc.sesion.cambio is None and botones(ctx) == MENU


async def test_los_horarios_se_paginan(esc):
    esc.huecos = [hueco(80 + i) for i in range(10)]
    ctx = await hasta_horarios(esc)
    assert len(horarios_de(ctx)) == flujo.HORARIOS_POR_PAGINA and "cam:mas" in botones(ctx)
    ctx = await esc.enviar(dato="cam:mas")
    assert horarios_de(ctx) == [dato_horario(h) for h in esc.huecos[flujo.HORARIOS_POR_PAGINA :]]
    assert "cam:mas" not in botones(ctx) and esc.sesion.cambio.pagina == 1


async def test_elegir_un_horario_pide_confirmar_y_muestra_el_cambio(esc):
    ctx = await hasta_confirmar(esc)
    assert botones(ctx) == ["cam:ok", "cam:x"]
    nuevo = cuando_bot(esc.huecos[0].inicio)
    assert textos(ctx) == (
        f"Vas a mover tu turno de Corte con Lucía del jueves 08/10 a las 09:00 al {nuevo}. ¿Confirmás?"
    )
    assert esc.sesion.cambio == SesionCambio(PASO_CAMBIO_CONFIRMAR, TURNO.id, int(esc.huecos[0].inicio.timestamp()))
    assert esc.movimientos == []


async def test_un_horario_que_ya_no_esta_libre_vuelve_a_ofrecer_otros(esc):
    await hasta_horarios(esc)
    tomado = esc.huecos.pop(0)
    ctx = await esc.enviar(dato=dato_horario(tomado))
    assert textos(ctx).startswith(flujo.TEXTO_HORARIO_NO_DISPONIBLE)
    assert dato_horario(tomado) not in botones(ctx) and horarios_de(ctx)
    assert esc.sesion.cambio.paso == PASO_CAMBIO_HORARIO and esc.movimientos == []


@pytest.mark.parametrize("valor", ["abc", "99999999999999999999", ""])
async def test_un_horario_que_no_es_un_epoch_vence(esc, valor):
    await hasta_horarios(esc)
    ctx = await esc.enviar(dato=f"cam:h:{valor}")
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA and esc.movimientos == []


async def test_confirmar_un_movimiento_directo_mueve_avisa_y_sincroniza(esc):
    await hasta_confirmar(esc)
    elegido = esc.huecos[0].inicio
    ctx = await esc.enviar(dato="cam:ok")
    (llamada,) = esc.movimientos
    assert llamada == {
        "tenant_id": TENANT,
        "end_customer_id": CLIENTE.id,
        "booking_id": TURNO.id,
        "nuevo_inicio": elegido,
        "canal": "bot",
        "ahora": AHORA,
    }
    assert esc.conn.ejecutados == [ACTOR]
    assert esc.avisos_negocio == [
        {"plantilla": PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO, "booking_id": TURNO.id, "inicio": elegido}
    ]
    (respuesta,) = ctx.respuestas
    assert respuesta.texto.startswith("Listo, moví tu turno de Corte con Lucía: ahora es el ")
    assert (respuesta.end_customer_id, respuesta.template) == (CLIENTE.id, "turno_movido")
    assert respuesta.outbox_turno == TurnoDelAviso(TURNO.id, elegido)
    assert len(ctx.posteriores) == 1 and esc.sesion.cambio is None


async def test_mover_un_servicio_con_aprobacion_dice_que_el_horario_original_se_mantiene(esc):
    elegido = esc.huecos[0].inicio
    vence = AHORA + timedelta(hours=24)
    esc.resultado_mover = TurnoMovido(
        TURNO.id, "pendiente_de_aprobacion", INICIO, FIN, elegido, elegido + timedelta(minutes=30), vence
    )
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="cam:ok")
    (respuesta,) = ctx.respuestas
    assert "sigue siendo el jueves 08/10 a las 09:00 hasta que responda" in respuesta.texto
    assert "el horario original se mantiene" in respuesta.texto and "el pedido vence" in respuesta.texto
    # el negocio recibe el pedido de aprobación; el cliente no recibe otro aviso y Calendar no cambia
    assert esc.avisos_negocio == [
        {"plantilla": PLANTILLA_AVISO_NEGOCIO_APROBACION, "booking_id": TURNO.id, "inicio": elegido}
    ]
    assert respuesta.template is None and respuesta.outbox_turno is None and ctx.posteriores == []
    assert esc.sesion.cambio is None


async def test_mover_libera_pendientes_vencidos_y_les_envia_su_aviso_despues_del_commit(esc):
    liberado = Liberado(TENANT, uuid.uuid4(), "telegram", "222", True, uuid.uuid4(), INICIO, "Corte")
    aviso = AvisoLiberado(liberado, uuid.uuid4(), liberado.end_customer_id, "222")
    elegido = esc.huecos[0].inicio
    esc.resultado_mover = TurnoMovido(TURNO.id, "movido", elegido, elegido + timedelta(minutes=30), liberados=(aviso,))
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="cam:ok")
    assert len(ctx.posteriores) == 2  # el aviso a quien se liberó y Calendar


@pytest.mark.parametrize(
    ("error", "texto"),
    [
        (HorarioOcupado("23P01"), flujo.TEXTO_HORARIO_NO_DISPONIBLE),
        (HorarioPasado("x"), flujo.TEXTO_HORARIO_NO_DISPONIBLE),
        (HorarioSinCambios("x"), flujo.TEXTO_MISMO_HORARIO),
        (ReservaRechazada(limites.MOTIVO_ANTICIPACION), "al menos 30 minutos de anticipación"),
    ],
)
async def test_un_horario_rechazado_por_el_dominio_vuelve_a_ofrecer_horarios(esc, error, texto):
    await hasta_confirmar(esc)
    esc.error_mover = error
    ctx = await esc.enviar(dato="cam:ok")
    assert texto in textos(ctx) and flujo.TEXTO_ELEGIR_HORARIO in textos(ctx)
    assert esc.sesion.cambio.paso == PASO_CAMBIO_HORARIO
    assert esc.avisos_negocio == [] and ctx.posteriores == []
    assert all(r.template is None for r in ctx.respuestas)


async def test_horario_ocupado_al_confirmar_sin_otros_horarios_termina_sin_sesion(esc):
    await hasta_confirmar(esc)
    esc.error_mover = HorarioOcupado("23P01")
    esc.huecos = []
    ctx = await esc.enviar(dato="cam:ok")
    assert "No hay horarios disponibles" in textos(ctx) and esc.sesion.cambio is None


@pytest.mark.parametrize("horas", [24, 1])
async def test_mover_pasado_el_plazo_dice_las_horas_y_el_contacto(esc, horas):
    await hasta_confirmar(esc)
    esc.error_mover = PlazoVencido(horas)
    ctx = await esc.enviar(dato="cam:ok")
    plural = "hora" if horas == 1 else "horas"
    assert textos(ctx) == (
        f"Este turno ya no se puede mover por acá: hace falta hacerlo con al menos {horas} {plural} de anticipación. "
        "Comunicate con el negocio: Tel. 11 5555-0000"
    )
    assert esc.sesion.cambio is None and esc.avisos_negocio == [] and ctx.posteriores == []


@pytest.mark.parametrize(
    ("error", "texto"),
    [
        (CambioYaPendiente("x"), flujo.TEXTO_CAMBIO_YA_PEDIDO),
        (TurnoNoEncontrado("x"), flujo.TEXTO_TURNO_NO_DISPONIBLE),
        (TurnoNoModificable("cancelled"), flujo.TEXTO_NO_MODIFICABLE),
        (ErrorDeTurno("otro"), flujo.TEXTO_NO_SE_PUDO),
        (ReservaRechazada("otro_motivo"), flujo.TEXTO_NO_SE_PUDO),
    ],
)
async def test_cada_error_del_dominio_al_mover_cierra_el_flujo_con_su_texto(esc, error, texto):
    await hasta_confirmar(esc)
    esc.error_mover = error
    ctx = await esc.enviar(dato="cam:ok")
    assert textos(ctx) == texto and esc.sesion.cambio is None
    assert esc.avisos_negocio == [] and ctx.posteriores == []


async def test_un_deadlock_persistente_deja_reintentar_sin_perder_la_eleccion(esc):
    await hasta_confirmar(esc)
    esc.error_mover = OperacionParaReintentar("40P01")
    ctx = await esc.enviar(dato="cam:ok")
    assert textos(ctx) == flujo.TEXTO_REINTENTAR and botones(ctx)[0] == "cam:ok"
    assert esc.sesion.cambio.paso == PASO_CAMBIO_CONFIRMAR
    esc.error_mover = None
    ctx = await esc.enviar(dato="cam:ok")
    assert ctx.respuestas[0].template == "turno_movido" and esc.sesion.cambio is None


async def test_el_turno_cancelado_a_mitad_del_movimiento_termina_el_flujo(esc):
    await hasta_horarios(esc)
    esc.turnos[TURNO.id] = turno("cancelled", TURNO.id)  # alguien lo canceló mientras la persona elegía
    ctx = await esc.enviar(dato=dato_horario(esc.huecos[0]))
    assert textos(ctx) == flujo.TEXTO_NO_MODIFICABLE and esc.sesion.cambio is None
    assert sin_efectos(esc, ctx)


async def test_el_boton_no_mover_descarta_el_movimiento(esc):
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="cam:x")
    assert textos(ctx) == flujo.TEXTO_SIN_CAMBIOS and esc.sesion.cambio is None and sin_efectos(esc, ctx)
    ctx = await esc.enviar(dato="cam:ok")  # el botón de confirmar que quedó en el chat ya no sirve
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA and esc.movimientos == []


@pytest.mark.parametrize("texto", ["cancelar", "quiero cancelar", "/cancelar", "Reprogramar", "anular"])
async def test_escribir_cancelar_en_medio_del_movimiento_lo_descarta(esc, texto):
    await hasta_confirmar(esc)
    ctx = await esc.enviar(texto)
    assert ctx.atendido and textos(ctx) == flujo.TEXTO_SIN_CAMBIOS
    assert esc.sesion.cambio is None and esc.movimientos == []


@pytest.mark.parametrize("texto", ["mis turnos", "hola", "", None])
async def test_otros_textos_no_son_de_este_flujo_ni_tocan_la_sesion(esc, texto):
    await hasta_confirmar(esc)
    antes = esc.sesion.copia()
    ctx = await esc.enviar(texto)
    assert ctx.atendido is False and ctx.respuestas == [] and esc.sesion == antes


async def test_escribir_cancelar_sin_movimiento_en_curso_lo_deja_a_consultas(esc):
    ctx = await esc.enviar("cancelar")
    assert ctx.atendido is False and ctx.respuestas == []


async def test_el_doble_toque_de_confirmar_mueve_una_sola_vez(esc):
    await hasta_confirmar(esc)
    await esc.enviar(dato="cam:ok")
    ctx = await esc.enviar(dato="cam:ok")
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA
    assert len(esc.movimientos) == 1 and len(esc.avisos_negocio) == 1 and ctx.posteriores == []


@pytest.mark.parametrize("dato", ["cam:h:1800000000", "cam:mas", "cam:ok"])
async def test_los_pasos_de_mover_sin_sesion_vencen(esc, dato):
    ctx = await esc.enviar(dato=dato)
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA and sin_efectos(esc, ctx)


async def test_confirmar_sin_haber_elegido_horario_vence(esc):
    await hasta_horarios(esc)
    ctx = await esc.enviar(dato="cam:ok")
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA and esc.movimientos == []


async def test_ver_mas_horarios_ya_en_la_confirmacion_vence(esc):
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="cam:mas")
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA


@pytest.mark.parametrize("dato", [f"cam:m:{AJENO}", "cam:m:basura", f"cam:m:{CANCELADO.id}"])
async def test_forjar_el_movimiento_de_un_turno_ajeno_no_abre_sesion(esc, dato):
    ctx = await esc.enviar(dato=dato)
    assert esc.sesion.cambio is None and esc.pedidos_huecos == [] and sin_efectos(esc, ctx)


async def test_el_turno_que_se_mueve_es_el_de_la_sesion_y_ningun_boton_lo_cambia(esc):
    await hasta_confirmar(esc)
    await esc.enviar(dato=f"cam:cc:{AJENO}")  # un botón forjado a mitad de camino
    assert esc.cancelaciones == []
    await esc.enviar(dato="cam:ok")  # `cam:ok` no lleva id
    assert [m["booking_id"] for m in esc.movimientos] == [TURNO.id]


async def test_un_boton_cam_desconocido_vence_y_no_toca_la_sesion(esc):
    await hasta_horarios(esc)
    antes = esc.sesion.copia()
    ctx = await esc.enviar(dato="cam:zzz")
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA and esc.sesion == antes


@pytest.mark.parametrize("dato", [f"cam:m:{TURNO.id}", f"cam:t:{TURNO.id}", f"cam:cc:{TURNO.id}", "cam:ok"])
async def test_con_el_alta_en_curso_ningun_boton_de_cambios_escribe(esc, dato):
    esc.cliente = None
    ctx = await esc.enviar(dato=dato)
    assert textos(ctx) == "dame tu teléfono" and sin_efectos(esc, ctx) and esc.conn.ejecutados == []


async def test_los_botones_de_otros_flujos_no_son_de_este(esc):
    for dato in ("reservar", "rsv:ok", "con:turnos", "idn:borrar_si"):
        assert (await esc.enviar(dato=dato)).atendido is False


async def test_ningun_texto_expone_ids_ni_datos_del_cliente(esc):
    esc.error_cancelar = PlazoVencido(24)
    ctx_a = await esc.enviar(dato=f"cam:t:{TURNO.id}")
    ctx_b = await esc.enviar(dato=f"cam:cc:{TURNO.id}")
    esc.error_cancelar = None
    ctx_c = await hasta_confirmar(esc)
    for ctx in (ctx_a, ctx_b, ctx_c):
        for r in ctx.respuestas:
            assert str(TURNO.id) not in r.texto and str(CLIENTE.id) not in r.texto and str(TENANT) not in r.texto
            assert CLIENTE.phone not in r.texto and CLIENTE.name not in r.texto


# --- el plazo se comprueba al empezar ------------------------------------------------------------------------

FUERA_DE_PLAZO = (
    "Este turno ya no se puede {accion} por acá: hace falta hacerlo con al menos 100 horas de anticipación. "
)
CONTACTO = "Comunicate con el negocio: Tel. 11 5555-0000"


@pytest.mark.parametrize(("dato", "accion"), [(f"cam:c:{TURNO.id}", "cancelar"), (f"cam:m:{TURNO.id}", "mover")])
async def test_fuera_de_plazo_al_empezar_dice_las_horas_y_el_contacto_sin_ofrecer_nada(esc, dato, accion):
    esc.plazo = 100  # faltan 72 horas para el turno
    ctx = await esc.enviar(dato=dato)
    assert textos(ctx) == FUERA_DE_PLAZO.format(accion=accion) + CONTACTO
    assert botones(ctx) == MENU  # ni confirmar la cancelación ni horarios
    assert esc.sesion.cambio is None and esc.pedidos_huecos == [] and sin_efectos(esc, ctx)


async def test_fuera_de_plazo_y_sin_huecos_dice_el_plazo_y_no_que_no_hay_horarios(esc):
    esc.plazo = 100
    esc.huecos = []
    ctx = await esc.enviar(dato=f"cam:m:{TURNO.id}")
    assert "al menos 100 horas" in textos(ctx) and "No hay horarios" not in textos(ctx)


async def test_dentro_de_plazo_y_sin_huecos_si_dice_que_no_hay_horarios(esc):
    esc.huecos = []
    ctx = await esc.enviar(dato=f"cam:m:{TURNO.id}")
    assert "No hay horarios disponibles" in textos(ctx) and "anticipación" not in textos(ctx)


@pytest.mark.parametrize("dato", [f"cam:c:{TURNO.id}", f"cam:m:{TURNO.id}"])
async def test_justo_en_el_plazo_todavia_se_puede_empezar(esc, dato):
    esc.plazo = 72  # el turno está a exactamente 72 horas
    ctx = await esc.enviar(dato=dato)
    assert "Comunicate" not in textos(ctx) and ("cam:cc:" in "".join(botones(ctx)) or horarios_de(ctx))


async def test_fuera_de_plazo_sin_contacto_cargado_igual_orienta(esc):
    esc.plazo = 100
    esc.contacto = None
    ctx = await esc.enviar(dato=f"cam:c:{TURNO.id}")
    assert textos(ctx).endswith(flujo.TEXTO_SIN_CONTACTO)


async def test_si_el_plazo_vence_entre_el_inicio_y_la_confirmacion_el_dominio_lo_frena(esc):
    await hasta_confirmar(esc)  # al empezar todavía estaba dentro del plazo
    esc.plazo = 100
    esc.error_mover = PlazoVencido(100)  # lo que levanta el dominio al confirmar
    ctx = await esc.enviar(dato="cam:ok")
    assert textos(ctx) == FUERA_DE_PLAZO.format(accion="mover") + CONTACTO
    assert esc.sesion.cambio is None and esc.avisos_negocio == [] and ctx.posteriores == []


async def test_el_horario_actual_del_turno_no_se_ofrece_como_destino(esc):
    esc.huecos = [Ventana(INICIO, FIN), hueco(80)]
    ctx = await hasta_horarios(esc)
    assert horarios_de(ctx) == [dato_horario(hueco(80))]
    ctx = await esc.enviar(dato=dato_horario(Ventana(INICIO, FIN)))  # un botón viejo con el horario actual
    assert textos(ctx).startswith(flujo.TEXTO_HORARIO_NO_DISPONIBLE) and esc.movimientos == []


async def test_un_horario_que_se_solapa_con_el_actual_se_ofrece_y_se_confirma(esc):
    corrido = Ventana(INICIO + timedelta(minutes=15), FIN + timedelta(minutes=15))
    esc.huecos = [corrido]
    ctx = await hasta_horarios(esc)
    assert horarios_de(ctx) == [dato_horario(corrido)]
    await esc.enviar(dato=dato_horario(corrido))
    await esc.enviar(dato="cam:ok")
    assert [m["nuevo_inicio"] for m in esc.movimientos] == [corrido.inicio]


async def test_el_propio_turno_se_excluye_de_los_ocupados_al_pedir_los_huecos(esc, monkeypatch):
    vistos = []

    async def huecos(conn, profesional, servicio, rango, no_antes_de=None, excluir_turno=None):
        vistos.append(excluir_turno)
        return [hueco(80)]

    monkeypatch.setattr(flujo, "calcular_huecos", huecos)
    await hasta_confirmar(esc)
    assert vistos == [TURNO.id, TURNO.id]  # al ofrecer y al validar el horario elegido


# --- un solo flujo activo ------------------------------------------------------------------------------------


@pytest.mark.parametrize("dato", [f"cam:t:{TURNO.id}", f"cam:c:{TURNO.id}", f"cam:cc:{TURNO.id}", f"cam:m:{TURNO.id}"])
async def test_tocar_un_turno_descarta_la_reserva_a_medias(esc, dato):
    esc.sesion.reserva = SesionReserva("servicio")
    await esc.enviar(dato=dato)
    assert esc.sesion.reserva is None


async def test_un_turno_que_no_es_de_la_persona_no_descarta_su_reserva(esc):
    esc.sesion.reserva = SesionReserva("servicio")
    await esc.enviar(dato=f"cam:m:{AJENO}")
    assert esc.sesion.reserva == SesionReserva("servicio")


async def test_con_una_reserva_a_medias_mover_y_escribir_cancelar_corta_todo_y_cam_ok_no_mueve(esc):
    esc.sesion.reserva = SesionReserva("servicio")
    cadena = Despachador([reserva.manejar_reserva, flujo.manejar_cambios])

    async def decir(texto=None, dato=None):
        mensaje = MensajeEntrante("telegram", "111", "111", "u", texto=texto, callback_data=dato)
        ctx = ContextoMensaje(esc.conn, TENANT, mensaje, esc.sesion)
        await cadena.despachar(ctx)
        return ctx

    await decir(dato=f"cam:m:{TURNO.id}")
    assert esc.sesion.reserva is None and esc.sesion.cambio is not None
    ctx = await decir("cancelar")
    assert textos(ctx) == flujo.TEXTO_SIN_CAMBIOS and not esc.sesion
    await decir(dato=dato_horario(esc.huecos[0]))
    ctx = await decir(dato="cam:ok")
    assert textos(ctx) == flujo.TEXTO_OPCION_VENCIDA and esc.movimientos == []


# --- sincronización de listas -------------------------------------------------------------------------------


def test_los_botones_de_cambios_caben_en_el_callback_de_telegram_y_comparten_prefijo():
    con_id = (flujo.CB_TURNO, flujo.CB_CANCELAR, flujo.CB_CANCELAR_SI, flujo.CB_MOVER)
    todos = [f"{prefijo}{uuid.uuid4()}" for prefijo in con_id]
    todos.append(f"{flujo.CB_HORARIO}{int(datetime(2099, 12, 31, tzinfo=UTC).timestamp())}")
    todos += [flujo.CB_MAS_HORARIOS, flujo.CB_CONFIRMAR, flujo.CB_DESCARTAR]
    assert all(d.startswith(flujo.PREFIJO_CB) and len(d.encode()) <= 64 for d in todos)


def test_el_boton_de_mis_turnos_es_el_que_atiende_el_flujo_de_cambios():
    assert consultas.PREFIJO_CAMBIAR_TURNO == flujo.CB_TURNO
    assert consultas.PREFIJO_CAMBIAR_TURNO.startswith(flujo.PREFIJO_CB)


def test_los_prefijos_de_cada_flujo_no_se_pisan():
    prefijos = [flujo.PREFIJO_CB, consultas.PREFIJO_CB, "rsv:", "idn:"]
    assert len(set(prefijos)) == 4
    assert not any(a != b and a.startswith(b) for a in prefijos for b in prefijos)


def test_cambios_va_despues_de_identidad_y_antes_de_consultas_en_la_cadena_de_produccion():
    nombres = [h.__name__ for h in handlers_por_defecto()]
    assert nombres.index("manejar_identidad") < nombres.index("manejar_cambios") < nombres.index("manejar_consultas")


def test_el_repositorio_solo_considera_modificables_los_pendientes_y_confirmados():
    estados = {"pending": True, "confirmed": True, "cancelled": False, "completed": False}
    for estado, esperado in estados.items():
        assert turno(estado).modificable is esperado
    assert set(repositorio.ESTADOS_MODIFICABLES) == {"pending", "confirmed"}
