"""Flujo de reserva con un repositorio y una disponibilidad en memoria (sin base de datos).

La integración contra Postgres real (RLS, exclusion constraint, carrera de doble reserva) está en
`test_reserva_integracion.py`. Nombres y teléfonos inventados.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from platform_notifications import MensajeEntrante
from turnos_dominio import Ventana

from bot_app import ContextoMensaje
from bot_app.flujos import handlers_por_defecto
from bot_app.identidad import manejar_identidad
from bot_app.identidad.repositorio import EndCustomer
from bot_app.reserva import flujo, limites, repositorio
from bot_app.reserva.repositorio import BookingCreado, DatosReserva, Profesional, Servicio

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
INICIO_LIB = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)  # noqa: UP017
AHORA = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
CLIENTE = EndCustomer(uuid.uuid4(), TENANT, "telegram", "111", "+5491100000000", "Ana Prueba")
CORTE = Servicio(uuid.uuid4(), "Corte", 30, Decimal("5000"))
TINTURA = Servicio(uuid.uuid4(), "Tintura", 90, Decimal("12000"))
LUCIA = Profesional(uuid.uuid4(), "Lucía")
MARTIN = Profesional(uuid.uuid4(), "Martín")


def hueco(horas: int, minutos: int = 30) -> Ventana:
    inicio = AHORA + timedelta(hours=horas)
    return Ventana(inicio, inicio + timedelta(minutes=minutos))


class RepoFalso:
    calcular_vencimiento = staticmethod(repositorio.calcular_vencimiento)  # función pura: se usa la real

    def __init__(self) -> None:
        self.servicios = [CORTE, TINTURA]
        self.profesionales = {CORTE.id: [LUCIA, MARTIN], TINTURA.id: [LUCIA]}
        self.modo = "auto"
        self.horas = 24
        self.conflicto = False
        self.creados: list[dict] = []
        self.elegible = True
        self.limites = limites.LimitesReserva()
        self.liberados: tuple = ()  # pending vencidos que el repositorio libera al crear
        self.rechazo: str | None = None  # motivo con el que el repositorio rechaza (revalidación en la base)

    async def limites_reserva(self, conn, tenant_id):
        return self.limites

    async def listar_servicios(self, conn, tenant_id):
        return self.servicios

    async def listar_profesionales(self, conn, tenant_id, servicio_id):
        return self.profesionales.get(servicio_id, [])

    async def datos_reserva(self, conn, tenant_id, profesional_id, servicio_id):
        if not self.elegible:
            return None
        servicio = next(s for s in self.servicios if s.id == servicio_id)
        return DatosReserva(servicio.nombre, "Lucía", servicio.duracion_minutos, self.modo)

    async def horas_aprobacion(self, conn, tenant_id):
        return self.horas

    async def crear_booking(self, conn, **datos):
        if self.rechazo:
            raise limites.ReservaRechazada(self.rechazo)
        if self.conflicto:
            return None
        self.creados.append(datos)
        vence = datos["pending_expires_at"]
        self.ultimo_id = uuid.uuid4()
        return BookingCreado(self.ultimo_id, "confirmed" if vence is None else "pending", vence, self.liberados)


class Escenario:
    """Arma el contexto de cada mensaje sobre una misma sesión, como hace el webhook."""

    def __init__(self, monkeypatch) -> None:
        self.repo = RepoFalso()
        self.huecos: list[Ventana] = [hueco(2), hueco(3), hueco(26)]
        self.pedidos_huecos: list[Ventana] = []
        self.no_antes_de: list[datetime | None] = []
        self.cliente: EndCustomer | None = CLIENTE
        self.sesion: dict = {}
        monkeypatch.setattr(flujo, "repositorio", self.repo)
        monkeypatch.setattr(flujo, "calcular_huecos", self._calcular_huecos)
        monkeypatch.setattr(flujo, "obtener_o_crear_end_customer", self._cliente)
        monkeypatch.setattr(flujo, "ahora", lambda: AHORA)

    async def _calcular_huecos(self, conn, profesional_id, servicio_id, rango, no_antes_de=None, **_):
        self.pedidos_huecos.append(rango)
        self.no_antes_de.append(no_antes_de)
        piso = max(rango.inicio, no_antes_de or rango.inicio)
        return [h for h in self.huecos if h.inicio >= piso and h.fin <= rango.fin]

    async def _cliente(self, ctx):
        if self.cliente is None:
            ctx.responder("dame tu teléfono")
        return self.cliente

    async def enviar(self, texto: str | None = None, dato: str | None = None) -> ContextoMensaje:
        mensaje = MensajeEntrante("telegram", "111", "111", "u", texto=texto, callback_data=dato)
        ctx = ContextoMensaje(None, TENANT, mensaje, self.sesion)
        ctx.atendido = await flujo.manejar_reserva(ctx)
        return ctx


@pytest.fixture
def esc(monkeypatch):
    return Escenario(monkeypatch)


def botones(ctx: ContextoMensaje) -> list[str]:
    return [b.dato for r in ctx.respuestas for b in r.botones]


def textos(ctx: ContextoMensaje) -> str:
    return "\n".join(r.texto for r in ctx.respuestas)


async def hasta_horarios(esc: Escenario) -> None:
    await esc.enviar("reservar")
    await esc.enviar(dato=f"rsv:s:{CORTE.id}")
    await esc.enviar(dato=f"rsv:p:{LUCIA.id}")


async def hasta_confirmar(esc: Escenario, hueco_elegido: Ventana | None = None) -> None:
    await hasta_horarios(esc)
    elegido = hueco_elegido or esc.huecos[0]
    await esc.enviar(dato=f"rsv:h:{int(elegido.inicio.timestamp())}")


# --- registro en la cadena ----------------------------------------------------------------------------


def test_la_cadena_de_produccion_prueba_identidad_antes_que_la_reserva():
    handlers = handlers_por_defecto()
    assert handlers.index(manejar_identidad) < handlers.index(flujo.manejar_reserva)


# --- servicio -> profesional -> horario -----------------------------------------------------------------


async def test_reservar_ofrece_los_servicios_por_botones(esc):
    ctx = await esc.enviar("Quiero un turno")
    assert ctx.atendido
    assert botones(ctx)[:2] == [f"rsv:s:{CORTE.id}", f"rsv:s:{TINTURA.id}"]
    assert esc.sesion["reserva"]["paso"] == "servicio"


async def test_el_boton_reservar_del_menu_inicia_la_reserva(esc):
    ctx = await esc.enviar(dato="reservar")
    assert ctx.atendido and botones(ctx)[0] == f"rsv:s:{CORTE.id}"


async def test_sin_servicios_reservables_lo_dice_y_no_deja_sesion(esc):
    esc.repo.servicios = []
    ctx = await esc.enviar("reservar")
    assert flujo.TEXTO_SIN_SERVICIOS in textos(ctx)
    assert "reserva" not in esc.sesion


async def test_un_cliente_sin_alta_completa_corta_la_cadena_sin_ofrecer_nada(esc):
    esc.cliente = None
    ctx = await esc.enviar("reservar")
    assert ctx.atendido and textos(ctx) == "dame tu teléfono"
    assert "reserva" not in esc.sesion


async def test_elegir_servicio_lista_solo_los_profesionales_que_lo_ofrecen(esc):
    await esc.enviar("reservar")
    ctx = await esc.enviar(dato=f"rsv:s:{TINTURA.id}")
    assert botones(ctx)[:1] == [f"rsv:p:{LUCIA.id}"]
    assert f"rsv:p:{MARTIN.id}" not in botones(ctx)


async def test_servicio_que_no_se_ofrece_no_se_puede_elegir_con_un_boton_forjado(esc):
    await esc.enviar("reservar")
    ctx = await esc.enviar(dato=f"rsv:s:{uuid.uuid4()}")
    assert flujo.TEXTO_OPCION_VENCIDA in textos(ctx)
    assert esc.sesion["reserva"]["paso"] == "servicio"


async def test_profesional_que_no_ofrece_el_servicio_se_rechaza(esc):
    await esc.enviar("reservar")
    await esc.enviar(dato=f"rsv:s:{TINTURA.id}")
    ctx = await esc.enviar(dato=f"rsv:p:{MARTIN.id}")  # Martín no hace tintura
    assert flujo.TEXTO_OPCION_VENCIDA in textos(ctx)
    assert "profesional_id" not in esc.sesion["reserva"]


async def test_los_horarios_se_piden_desde_ahora_y_se_ofrecen_por_botones(esc):
    await hasta_horarios(esc)
    assert esc.pedidos_huecos[-1].inicio == AHORA
    assert esc.pedidos_huecos[-1].fin == AHORA + timedelta(days=flujo.HORIZONTE_DIAS)
    ctx = await esc.enviar(dato=f"rsv:p:{LUCIA.id}")
    assert [b for b in botones(ctx) if b.startswith("rsv:h:")] == [
        f"rsv:h:{int(h.inicio.timestamp())}" for h in esc.huecos
    ]


async def test_los_horarios_se_paginan(esc):
    esc.huecos = [hueco(h) for h in range(1, 1 + flujo.HORARIOS_POR_PAGINA + 3)]
    await hasta_horarios(esc)
    ctx = await esc.enviar(dato=f"rsv:p:{LUCIA.id}")
    assert "rsv:m" in botones(ctx)
    assert len([b for b in botones(ctx) if b.startswith("rsv:h:")]) == flujo.HORARIOS_POR_PAGINA
    ctx = await esc.enviar(dato="rsv:m")
    assert [b for b in botones(ctx) if b.startswith("rsv:h:")] == [
        f"rsv:h:{int(h.inicio.timestamp())}" for h in esc.huecos[flujo.HORARIOS_POR_PAGINA :]
    ]
    assert "rsv:m" not in botones(ctx)


async def test_sin_horarios_vuelve_a_ofrecer_profesionales(esc):
    esc.huecos = []
    await esc.enviar("reservar")
    await esc.enviar(dato=f"rsv:s:{CORTE.id}")
    ctx = await esc.enviar(dato=f"rsv:p:{LUCIA.id}")
    assert "No hay horarios disponibles" in textos(ctx)
    assert f"rsv:p:{MARTIN.id}" in botones(ctx)
    assert esc.sesion["reserva"]["paso"] == "profesional"


async def test_elegir_un_horario_pide_confirmacion_con_el_resumen(esc):
    await hasta_horarios(esc)
    ctx = await esc.enviar(dato=f"rsv:h:{int(esc.huecos[0].inicio.timestamp())}")
    assert esc.sesion["reserva"]["paso"] == "confirmar"
    resumen = textos(ctx)
    # 12:00 UTC + 2 h = 14:00 UTC = 11:00 en Buenos Aires (el 5/10/2026 es lunes)
    assert "Corte" in resumen and "Lucía" in resumen and "lunes 05/10" in resumen and "11:00" in resumen
    assert "rsv:ok" in botones(ctx) and "rsv:x" in botones(ctx)


async def test_un_horario_que_ya_no_esta_libre_ofrece_otros(esc):
    await hasta_horarios(esc)
    ocupado = esc.huecos.pop(0)  # alguien lo tomó después de ofrecerse
    ctx = await esc.enviar(dato=f"rsv:h:{int(ocupado.inicio.timestamp())}")
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(ctx)
    assert f"rsv:h:{int(ocupado.inicio.timestamp())}" not in botones(ctx)
    assert f"rsv:h:{int(esc.huecos[0].inicio.timestamp())}" in botones(ctx)
    assert esc.sesion["reserva"]["paso"] == "horario"


async def test_un_horario_pasado_no_se_acepta_aunque_la_disponibilidad_lo_devuelva(esc):
    pasado = hueco(-3)
    esc.huecos.insert(0, pasado)
    await hasta_horarios(esc)
    ctx = await esc.enviar(dato=f"rsv:h:{int(pasado.inicio.timestamp())}")
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(ctx)
    assert esc.repo.creados == []


# --- confirmar ------------------------------------------------------------------------------------------


async def test_modo_auto_crea_el_booking_confirmado_y_agenda_calendar_despues_del_commit(esc):
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="rsv:ok")
    (creado,) = esc.repo.creados
    elegido = esc.huecos[0]
    assert (creado["inicio"], creado["fin"]) == (elegido.inicio, elegido.fin)
    assert creado["end_customer_id"] == CLIENTE.id and creado["pending_expires_at"] is None
    assert creado["servicio_id"] == CORTE.id and creado["profesional_id"] == LUCIA.id
    assert flujo.TEXTO_CONFIRMADO in textos(ctx)
    assert len(ctx.posteriores) == 1
    assert ctx.respuestas[-1].end_customer_id == CLIENTE.id
    assert ctx.respuestas[-1].outbox_params == {
        "booking_id": str(esc.repo.ultimo_id),
        "inicio": elegido.inicio.isoformat(),
    }
    assert "reserva" not in esc.sesion


async def test_si_se_liberaron_pendientes_vencidos_se_agenda_su_aviso_despues_del_commit(esc):
    esc.repo.liberados = (
        repositorio.Liberado(TENANT, uuid.uuid4(), "telegram", "222", True, uuid.uuid4(), INICIO_LIB, "Corte"),
    )
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="rsv:ok")
    assert len(ctx.posteriores) == 2  # el evento de Calendar (confirmed) y el aviso al liberado


async def test_un_liberado_sin_a_quien_avisar_no_agenda_aviso(esc):
    esc.repo.liberados = (repositorio.Liberado(TENANT, uuid.uuid4()),)
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="rsv:ok")
    assert len(ctx.posteriores) == 1


async def test_el_fin_sale_de_la_duracion_del_servicio(esc):
    esc.huecos = [hueco(2, 90)]
    await esc.enviar("reservar")
    await esc.enviar(dato=f"rsv:s:{TINTURA.id}")
    await esc.enviar(dato=f"rsv:p:{LUCIA.id}")
    await esc.enviar(dato=f"rsv:h:{int(esc.huecos[0].inicio.timestamp())}")
    await esc.enviar(dato="rsv:ok")
    (creado,) = esc.repo.creados
    assert creado["fin"] - creado["inicio"] == timedelta(minutes=90)


async def test_modo_approval_nace_pending_sin_calendar_y_avisa_que_queda_pendiente(esc):
    esc.repo.modo = "approval"
    await hasta_confirmar(esc)
    assert esc.sesion["reserva"]["paso"] == "confirmar"
    ctx = await esc.enviar(dato="rsv:ok")
    (creado,) = esc.repo.creados
    assert creado["pending_expires_at"] == min(AHORA + timedelta(hours=24), esc.huecos[0].inicio)
    assert ctx.posteriores == []
    assert "pendiente de aprobación" in textos(ctx)
    assert flujo.TEXTO_CONFIRMADO not in textos(ctx)


async def test_approval_usa_el_timeout_del_tenant_y_nunca_pasa_del_inicio_del_turno(esc):
    esc.repo.modo = "approval"
    esc.repo.horas = 48
    esc.huecos = [hueco(5)]  # el turno empieza antes de las 48 h
    await hasta_confirmar(esc)
    await esc.enviar(dato="rsv:ok")
    assert esc.repo.creados[0]["pending_expires_at"] == esc.huecos[0].inicio

    esc.sesion.clear()
    esc.huecos = [hueco(24 * 6)]  # el turno es en 6 días: manda el timeout
    await hasta_confirmar(esc)
    await esc.enviar(dato="rsv:ok")
    assert esc.repo.creados[1]["pending_expires_at"] == AHORA + timedelta(hours=48)


async def test_un_servicio_que_pasa_a_deposit_o_sin_profesional_activo_no_se_confirma(esc):
    await hasta_confirmar(esc)
    esc.repo.elegible = False  # deposit o profesional dado de baja entre la selección y la confirmación
    ctx = await esc.enviar(dato="rsv:ok")
    assert esc.repo.creados == []
    assert flujo.TEXTO_SERVICIO_NO_DISPONIBLE in textos(ctx)
    assert ctx.posteriores == []


async def test_si_otro_cliente_tomo_el_horario_se_ofrecen_otros_y_no_hay_calendar(esc):
    await hasta_confirmar(esc)
    esc.repo.conflicto = True  # el exclusion constraint rechazó el insert
    ctx = await esc.enviar(dato="rsv:ok")
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(ctx)
    assert ctx.posteriores == []
    assert any(b.startswith("rsv:h:") for b in botones(ctx))
    assert esc.sesion["reserva"]["paso"] == "horario"


async def test_confirmar_sin_haber_elegido_horario_es_una_opcion_vencida(esc):
    await hasta_horarios(esc)
    ctx = await esc.enviar(dato="rsv:ok")
    assert flujo.TEXTO_OPCION_VENCIDA in textos(ctx)
    assert esc.repo.creados == []


async def test_un_boton_viejo_sin_sesion_no_ejecuta_nada(esc):
    ctx = await esc.enviar(dato="rsv:ok")
    assert ctx.atendido and flujo.TEXTO_OPCION_VENCIDA in textos(ctx)
    assert esc.repo.creados == []


async def test_el_boton_cancelar_descarta_la_reserva(esc):
    await hasta_confirmar(esc)
    ctx = await esc.enviar(dato="rsv:x")
    assert flujo.TEXTO_RESERVA_DESCARTADA in textos(ctx)
    assert "reserva" not in esc.sesion and esc.repo.creados == []


async def test_escribir_cancelar_en_medio_de_la_reserva_la_descarta(esc):
    await hasta_horarios(esc)
    ctx = await esc.enviar("Cancelar")
    assert flujo.TEXTO_RESERVA_DESCARTADA in textos(ctx)
    assert "reserva" not in esc.sesion


# --- cancelar / reprogramar un turno existente: lo hace el negocio ----------------------------------------


@pytest.mark.parametrize("texto", ["cancelar mi turno", "Quiero reprogramar", "/cancelar", "anular el turno"])
async def test_cancelar_o_reprogramar_sin_reserva_en_curso_lo_deja_a_consultas(esc, texto):
    ctx = await esc.enviar(texto)
    assert ctx.atendido is False and ctx.respuestas == []


@pytest.mark.parametrize("texto", ["cancelar", "quiero cancelar", "/cancelar", "reprogramar"])
async def test_con_reserva_en_curso_cancelar_la_descarta(esc, texto):
    await hasta_horarios(esc)
    ctx = await esc.enviar(texto)
    assert ctx.atendido and flujo.TEXTO_RESERVA_DESCARTADA in textos(ctx)
    assert "reserva" not in esc.sesion and esc.repo.creados == []


async def test_texto_ajeno_a_la_reserva_no_se_atiende_para_dejar_pasar_otros_flujos(esc):
    ctx = await esc.enviar("mis turnos")
    assert ctx.atendido is False and ctx.respuestas == []
    await esc.enviar("reservar")
    ctx = await esc.enviar("blablá")
    assert ctx.atendido is False
    assert esc.sesion["reserva"]["paso"] == "servicio"  # la reserva en curso sigue viva


# --- límites de reserva: tope de activos, límite por hora y anticipación mínima ---------------------------


async def test_la_anticipacion_minima_por_defecto_es_de_30_minutos_y_se_pasa_a_los_huecos(esc):
    await hasta_horarios(esc)
    assert esc.no_antes_de[-1] == AHORA + timedelta(minutes=30)


def test_los_defaults_viven_en_un_solo_lugar():
    por_defecto = limites.LimitesReserva()
    assert (por_defecto.max_activos_por_cliente, por_defecto.max_por_hora, por_defecto.anticipacion_minutos) == (
        5,
        3,
        30,
    )


async def test_un_horario_dentro_de_la_anticipacion_no_se_ofrece_y_uno_en_el_borde_si(esc):
    esc.repo.limites = limites.LimitesReserva(anticipacion_minutos=60)
    esc.huecos = [hueco(0, 30), Ventana(AHORA + timedelta(minutes=59), AHORA + timedelta(minutes=89)), hueco(1)]
    await esc.enviar("reservar")
    await esc.enviar(dato=f"rsv:s:{CORTE.id}")
    ctx = await esc.enviar(dato=f"rsv:p:{LUCIA.id}")
    ofrecidos = [b for b in botones(ctx) if b.startswith("rsv:h:")]
    assert ofrecidos == [f"rsv:h:{int(hueco(1).inicio.timestamp())}"]


async def test_un_horario_forjado_dentro_de_la_anticipacion_se_rechaza_aunque_la_disponibilidad_lo_devuelva(esc):
    cercano = Ventana(AHORA + timedelta(minutes=10), AHORA + timedelta(minutes=40))
    esc.huecos.insert(0, cercano)
    await hasta_horarios(esc)
    ctx = await esc.enviar(dato=f"rsv:h:{int(cercano.inicio.timestamp())}")
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(ctx)
    assert esc.repo.creados == []


async def test_la_revalidacion_al_confirmar_rechaza_por_anticipacion_y_ofrece_otros_horarios(esc):
    await hasta_confirmar(esc)
    esc.repo.rechazo = limites.MOTIVO_ANTICIPACION
    ctx = await esc.enviar(dato="rsv:ok")
    assert "30 minutos de anticipación" in textos(ctx)
    assert any(b.startswith("rsv:h:") for b in botones(ctx))
    assert esc.repo.creados == []


async def test_el_tope_de_turnos_activos_rechaza_con_mensaje_y_sin_crear_el_turno(esc):
    esc.repo.limites = limites.LimitesReserva(max_activos_por_cliente=2)
    await hasta_confirmar(esc)
    esc.repo.rechazo = limites.MOTIVO_TOPE_ACTIVOS
    ctx = await esc.enviar(dato="rsv:ok")
    assert "Ya tenés 2 turnos pendientes o confirmados" in textos(ctx)
    assert esc.repo.creados == [] and "reserva" not in esc.sesion
    assert ctx.posteriores == []


@pytest.mark.parametrize(
    ("tope", "esperado"),
    [
        (1, "Ya tenés 1 turno pendiente o confirmado,"),
        (2, "Ya tenés 2 turnos pendientes o confirmados,"),
        (5, "Ya tenés 5 turnos pendientes o confirmados,"),
    ],
)
async def test_el_texto_del_tope_concuerda_en_numero(esc, tope, esperado):
    esc.repo.limites = limites.LimitesReserva(max_activos_por_cliente=tope)
    await hasta_confirmar(esc)
    esc.repo.rechazo = limites.MOTIVO_TOPE_ACTIVOS
    ctx = await esc.enviar(dato="rsv:ok")
    assert esperado in textos(ctx)


async def test_el_limite_por_hora_rechaza_con_mensaje_y_sin_crear_el_turno(esc):
    await hasta_confirmar(esc)
    esc.repo.rechazo = limites.MOTIVO_LIMITE_POR_HORA
    ctx = await esc.enviar(dato="rsv:ok")
    assert flujo.TEXTO_LIMITE_POR_HORA in textos(ctx)
    assert esc.repo.creados == [] and "reserva" not in esc.sesion


async def test_el_bot_pasa_los_limites_y_el_ahora_al_crear_el_turno(esc):
    esc.repo.limites = limites.LimitesReserva(max_activos_por_cliente=7, max_por_hora=2, anticipacion_minutos=45)
    await hasta_confirmar(esc)
    await esc.enviar(dato="rsv:ok")
    (creado,) = esc.repo.creados
    assert creado["limites"] == esc.repo.limites and creado["ahora"] == AHORA


@pytest.mark.parametrize("valor", [None, "abc", "0", "51", "2.5", "-1", ""])
def test_un_setting_ausente_o_invalido_aplica_el_default(valor):
    assert limites.interpretar(valor, limites.MAX_ACTIVOS_POR_CLIENTE) == 5


def test_un_setting_valido_pisa_el_default_y_el_cero_es_valido_en_la_anticipacion():
    assert limites.interpretar("9", limites.MAX_ACTIVOS_POR_CLIENTE) == 9
    assert limites.interpretar("0", limites.ANTICIPACION_MINIMA_MINUTOS) == 0
    assert limites.interpretar("1440", limites.ANTICIPACION_MINIMA_MINUTOS) == 1440
    assert limites.interpretar("1441", limites.ANTICIPACION_MINIMA_MINUTOS) == 30
