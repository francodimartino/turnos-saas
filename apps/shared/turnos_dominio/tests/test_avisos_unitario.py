"""Avisos al cliente según sus canales verificados y aviso de vencimiento al liberar un horario."""

import json
import uuid
from datetime import datetime, timezone

import pytest
from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_vencimiento

from turnos_dominio import alta, avisos
from turnos_dominio.alta import Liberado
from turnos_dominio.avisos import FichaParaAviso

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
CHAT = "222"


def fila_ficha(canales: list[tuple[str, str]] | None = None, anonimizada: bool = False) -> dict:
    """La fila que da `SQL_FICHA_PARA_AVISO` para una ficha con esos canales verificados `(canal, identificador)`."""
    canales = canales or []
    telegrams = [ident for canal, ident in canales if canal == "telegram"]
    orden = {"telegram": 0, "whatsapp": 1, "email": 2}
    return {
        "anonimizada": anonimizada,
        "verificados": len(canales),
        "telegrams": len(telegrams),
        "canal_origen": min((c for c, _ in canales), key=orden.__getitem__) if canales else None,
        "identificador_telegram": max(telegrams) if telegrams else None,
    }


SOLO_TELEGRAM = fila_ficha([("telegram", CHAT)])
TELEGRAM_Y_EMAIL = fila_ficha([("telegram", CHAT), ("email", "a@b.ar")])
DOS_TELEGRAM = fila_ficha([("telegram", CHAT), ("telegram", "333")])
SOLO_EMAIL = fila_ficha([("email", "a@b.ar")])
SOLO_WHATSAPP = fila_ficha([("whatsapp", "549")])
WHATSAPP_Y_EMAIL = fila_ficha([("whatsapp", "549"), ("email", "a@b.ar")])
ANONIMIZADA = fila_ficha([], anonimizada=True)
SIN_CANALES = fila_ficha([])


def liberado(**cambios) -> Liberado:
    datos = {
        "tenant_id": TENANT,
        "booking_id": uuid.uuid4(),
        "channel": "telegram",
        "channel_user_id": CHAT,
        "notify": True,
        "end_customer_id": uuid.uuid4(),
        "starts_at": INICIO,
        "service_name": "Corte",
    }
    return Liberado(**{**datos, **cambios})


class ConnOutbox:
    """Conexión simulada: `fichas` por id de cliente (con las filas que da la base), el turno y el outbox."""

    def __init__(self, fichas: dict | None = None, ficha_unica: dict | None = None, cliente_del_turno=None) -> None:
        self.fichas, self.ficha_unica = fichas or {}, ficha_unica
        self.cliente_del_turno = cliente_del_turno
        self.llamadas: list[tuple] = []
        self.lecturas: list[uuid.UUID] = []

    async def fetchrow(self, sql, *args):
        assert sql == avisos.SQL_FICHA_PARA_AVISO
        self.lecturas.append(args[0])
        return self.ficha_unica if self.ficha_unica is not None else self.fichas.get(args[0])

    async def fetchval(self, sql, *args):
        if sql == avisos.SQL_CLIENTE_DEL_TURNO:
            return self.cliente_del_turno
        assert "enqueue_notification" in sql
        self.llamadas.append(args)
        return None if (self.fichas.get(args[0]) or self.ficha_unica or {}).get("anonimizada") else uuid.uuid4()


# --- la regla del envío inmediato ----------------------------


@pytest.mark.parametrize(
    ("fila", "solo_telegram"),
    [
        (SOLO_TELEGRAM, True),
        (TELEGRAM_Y_EMAIL, False),
        (DOS_TELEGRAM, False),
        (SOLO_EMAIL, False),
        (SOLO_WHATSAPP, False),
        (WHATSAPP_Y_EMAIL, False),
        (ANONIMIZADA, False),
        (SIN_CANALES, False),
    ],
)
async def test_solo_telegram_es_un_unico_canal_verificado_y_es_un_telegram(fila, solo_telegram):
    ficha = await avisos.leer_ficha_para_aviso(ConnOutbox(ficha_unica=fila), uuid.uuid4())
    assert isinstance(ficha, FichaParaAviso) and ficha.solo_telegram is solo_telegram


async def test_una_ficha_inexistente_o_de_otro_tenant_no_se_lee():
    assert await avisos.leer_ficha_para_aviso(ConnOutbox(), uuid.uuid4()) is None


# --- encolar según los canales ----------------------------


async def _encolar(conn, cliente, **extra):
    return await avisos.encolar_aviso_segun_canales(
        conn, end_customer_id=cliente, plantilla="turno_confirmado", booking_id=uuid.uuid4(), inicio=INICIO, **extra
    )


async def test_ficha_solo_telegram_se_encola_con_envio_inmediato_y_demora_de_300_segundos():
    cliente = uuid.uuid4()
    conn = ConnOutbox({cliente: SOLO_TELEGRAM})
    aviso = await _encolar(conn, cliente)
    ((_, canal, plantilla, params, demora),) = conn.llamadas
    assert (aviso.estado, aviso.envio_inmediato, aviso.destinatario_telegram) == ("encolado", True, CHAT)
    assert (canal, plantilla, demora) == ("telegram", "turno_confirmado", 300)
    assert set(json.loads(params)) == {"booking_id", "inicio"}


@pytest.mark.parametrize(
    ("fila", "canal_esperado"),
    [
        (TELEGRAM_Y_EMAIL, "telegram"),
        (DOS_TELEGRAM, "telegram"),
        (SOLO_EMAIL, "email"),
        (SOLO_WHATSAPP, "whatsapp"),
        (WHATSAPP_Y_EMAIL, "whatsapp"),
    ],
)
async def test_con_cualquier_otro_canal_se_encola_sin_envio_inmediato_y_con_demora_cero(fila, canal_esperado):
    cliente = uuid.uuid4()
    conn = ConnOutbox({cliente: fila})
    aviso = await _encolar(conn, cliente)
    ((_, canal, _, _, demora),) = conn.llamadas
    assert aviso.estado == "encolado" and aviso.outbox_id is not None
    assert aviso.envio_inmediato is False and aviso.destinatario_telegram is None
    assert (canal, demora) == (canal_esperado, 0)


async def test_el_canal_de_origen_del_mensaje_manda_sobre_el_mejor_canal_de_la_ficha():
    cliente = uuid.uuid4()
    conn = ConnOutbox({cliente: WHATSAPP_Y_EMAIL})
    await _encolar(conn, cliente, canal_origen="email")
    assert conn.llamadas[0][1] == "email"


async def test_ficha_anonimizada_no_encola():
    cliente = uuid.uuid4()
    conn = ConnOutbox({cliente: ANONIMIZADA})
    aviso = await _encolar(conn, cliente)
    assert (aviso.estado, aviso.outbox_id, aviso.envio_inmediato) == ("cliente_anonimizado", None, False)
    assert conn.llamadas == []


async def test_ficha_sin_ningun_canal_verificado_no_encola_y_dice_sin_canal():
    cliente = uuid.uuid4()
    conn = ConnOutbox({cliente: SIN_CANALES})
    aviso = await _encolar(conn, cliente)
    assert (aviso.estado, aviso.outbox_id, aviso.envio_inmediato) == ("sin_canal", None, False)
    assert conn.llamadas == []


async def test_ficha_sin_canal_verificado_no_encola_aunque_se_pase_un_canal_de_origen():
    cliente = uuid.uuid4()
    conn = ConnOutbox({cliente: SIN_CANALES})
    aviso = await _encolar(conn, cliente, canal_origen="telegram")
    assert aviso.estado == "sin_canal" and conn.llamadas == []


async def test_ficha_inexistente_no_encola_y_dice_sin_canal():
    conn = ConnOutbox()
    aviso = await _encolar(conn, uuid.uuid4())
    assert aviso.estado == "sin_canal" and conn.llamadas == []


async def test_si_se_anonimiza_entre_la_lectura_y_el_encolado_no_queda_aviso():
    cliente = uuid.uuid4()
    conn = ConnOutbox(ficha_unica=SOLO_TELEGRAM)
    conn.fichas = {cliente: {"anonimizada": True}}  # la base devuelve NULL al encolar
    aviso = await _encolar(conn, cliente)
    assert (aviso.estado, aviso.outbox_id) == ("cliente_anonimizado", None)


# --- vencimiento al liberar un horario ----------------------------


async def test_encola_un_aviso_por_liberado_con_plantilla_y_params_sin_pii():
    lib = liberado()
    conn = ConnOutbox({lib.end_customer_id: SOLO_TELEGRAM})
    (aviso,) = await avisos.encolar_avisos_liberados(conn, [lib])
    ((cliente, canal, plantilla, params, demora),) = conn.llamadas
    assert (cliente, canal, plantilla, demora) == (lib.end_customer_id, "telegram", PLANTILLA_VENCIMIENTO, 300)
    assert json.loads(params) == {"booking_id": str(lib.booking_id), "inicio": INICIO.isoformat()}
    assert aviso.liberado is lib and aviso.outbox_id is not None
    assert aviso.envio_inmediato and aviso.destinatario_telegram == CHAT


@pytest.mark.parametrize("fila", [SOLO_EMAIL, TELEGRAM_Y_EMAIL, WHATSAPP_Y_EMAIL, DOS_TELEGRAM])
async def test_liberado_con_otro_canal_se_encola_sin_envio_inmediato_aunque_notify_sea_falso(fila):
    lib = liberado(channel=None, channel_user_id=None, notify=False, end_customer_id=None)
    cliente = uuid.uuid4()  # la base no devuelve la ficha cuando notify es falso: se busca en el turno
    conn = ConnOutbox({cliente: fila}, cliente_del_turno=cliente)
    (aviso,) = await avisos.encolar_avisos_liberados(conn, [lib])
    assert aviso.envio_inmediato is False and aviso.destinatario_telegram is None
    assert aviso.end_customer_id == cliente and conn.llamadas[0][4] == 0


async def test_liberado_de_ficha_solo_telegram_sin_canal_heredado_igual_se_envia_al_instante():
    lib = liberado(channel=None, channel_user_id=None, notify=False, end_customer_id=None)
    cliente = uuid.uuid4()
    conn = ConnOutbox({cliente: SOLO_TELEGRAM}, cliente_del_turno=cliente)
    (aviso,) = await avisos.encolar_avisos_liberados(conn, [lib])
    assert aviso.envio_inmediato and aviso.destinatario_telegram == CHAT


@pytest.mark.parametrize(
    "caso",
    [
        {"starts_at": None},
        {"end_customer_id": None},  # el turno no tiene cliente
    ],
)
async def test_liberado_sin_inicio_o_sin_cliente_no_encola(caso):
    conn = ConnOutbox(cliente_del_turno=None)
    assert await avisos.encolar_avisos_liberados(conn, [liberado(**caso)]) == ()
    assert conn.llamadas == []


@pytest.mark.parametrize("fila", [ANONIMIZADA, SIN_CANALES])
async def test_liberado_de_ficha_anonimizada_o_sin_canales_no_encola(fila):
    lib = liberado()
    conn = ConnOutbox({lib.end_customer_id: fila})
    assert await avisos.encolar_avisos_liberados(conn, [lib]) == ()
    assert conn.llamadas == []


async def test_el_aviso_de_uno_no_depende_de_los_otros():
    uno, otro = liberado(), liberado()
    conn = ConnOutbox({uno.end_customer_id: SOLO_TELEGRAM, otro.end_customer_id: ANONIMIZADA})
    avisados = await avisos.encolar_avisos_liberados(conn, [otro, uno])
    assert [a.liberado for a in avisados] == [uno]


def test_el_pedido_de_envio_usa_el_texto_compartido_y_cierra_la_fila_del_outbox():
    lib = liberado()
    aviso = avisos.AvisoLiberado(lib, uuid.uuid4(), lib.end_customer_id, CHAT)
    pedido = avisos.pedido_de_envio(aviso)
    assert pedido.texto == mensaje_vencimiento(INICIO, "Corte")
    assert pedido.template == PLANTILLA_VENCIMIENTO
    assert pedido.destinatario == CHAT and pedido.end_customer_id == lib.end_customer_id
    assert pedido.outbox_id == aviso.outbox_id


def test_no_se_arma_un_pedido_de_envio_para_un_aviso_sin_envio_inmediato():
    lib = liberado()
    with pytest.raises(ValueError, match="solo se envía al instante"):
        avisos.pedido_de_envio(avisos.AvisoLiberado(lib, uuid.uuid4(), lib.end_customer_id, None))


class ConnLiberar:
    def __init__(self, filas) -> None:
        self.filas, self.args = filas, None

    async def fetch(self, sql, *args):
        self.args = (sql, args)
        return self.filas


async def test_liberar_vencidos_solapados_devuelve_los_liberados():
    fila = {
        "tenant_id": TENANT,
        "booking_id": uuid.uuid4(),
        "channel": "telegram",
        "channel_user_id": CHAT,
        "notify": True,
        "end_customer_id": uuid.uuid4(),
        "starts_at": INICIO,
        "service_name": "Corte",
    }
    conn = ConnLiberar([fila])
    profesional = uuid.uuid4()
    fin = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)
    (lib,) = await alta.liberar_vencidos_solapados(conn, profesional_id=profesional, inicio=INICIO, fin=fin)
    assert lib.booking_id == fila["booking_id"] and lib.notify
    assert conn.args == (alta.SQL_LIBERAR_VENCIDOS, (profesional, INICIO, fin))
