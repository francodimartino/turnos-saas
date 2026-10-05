"""Limpieza de Google Calendar tras el borrado (§3.5): objetivos, fallos auditados sin PII, nunca revienta."""

import json
import uuid

import pytest
from platform_calendar import ErrorConfiguracionCalendar, EventoNoLimpiado

from bot_app.despachador import Recursos
from bot_app.identidad.calendario import (
    ACCION_AUDIT,
    MOTIVO_INESPERADO,
    MOTIVO_NO_CONFIGURADO,
    MOTIVO_SIN_INTEGRACION,
    limpiar_calendario_de,
)

TENANT = uuid.uuid4()
CLIENTE = uuid.uuid4()
INTEGRACION = uuid.uuid4()


class ConnFalsa:
    def __init__(self, base: "BaseFalsa") -> None:
        self.base = base

    async def execute(self, sql, *args):
        self.base.eventos.append(sql.strip().split("(")[0].split()[0].lower())

    async def fetch(self, sql, *args):
        self.base.eventos.append("objetivos")
        assert args == (TENANT, CLIENTE)
        return self.base.objetivos

    async def fetchval(self, sql, *args):
        self.base.auditorias.append({"action": args[0], "metadata": json.loads(args[3])})
        return uuid.uuid4()


class _Adq:
    def __init__(self, base):
        self.base = base

    async def __aenter__(self):
        return ConnFalsa(self.base)

    async def __aexit__(self, *exc):
        return False


class BaseFalsa:
    """Pool que devuelve `calendar_scrub_targets` y registra las escrituras de audit."""

    def __init__(self, objetivos) -> None:
        self.objetivos = objetivos
        self.eventos: list[str] = []
        self.auditorias: list[dict] = []

    def acquire(self):
        return _Adq(self)


def _fila(evento: str, integracion=INTEGRACION):
    return {"integration_id": integracion, "google_calendar_event_id": evento}


class CalendarioFalso:
    def __init__(self, fallidos=(), levanta: Exception | None = None) -> None:
        self.recibido: list = []
        self._fallidos, self._levanta = list(fallidos), levanta

    async def limpiar_eventos(self, pares):
        self.recibido.append(pares)
        if self._levanta:
            raise self._levanta
        return self._fallidos


async def _correr(base, fabrica):
    await limpiar_calendario_de(TENANT, CLIENTE)(Recursos(base, object(), fabrica))


async def test_sin_eventos_no_llama_a_calendar_ni_audita():
    base = BaseFalsa([])
    llamado = []
    await _correr(base, lambda conn, http: llamado.append(1))
    assert llamado == [] and base.auditorias == []


async def test_limpia_todos_los_eventos_del_cliente_y_no_audita_si_salen_bien():
    base = BaseFalsa([_fila("ev1"), _fila("ev2")])
    cal = CalendarioFalso()
    await _correr(base, lambda conn, http: cal)
    assert cal.recibido == [[(INTEGRACION, "ev1"), (INTEGRACION, "ev2")]]
    assert base.auditorias == []


async def test_cada_evento_no_limpiado_queda_en_el_audit_solo_con_event_id_y_motivo():
    base = BaseFalsa([_fila("ev1"), _fila("ev2")])
    cal = CalendarioFalso([EventoNoLimpiado(INTEGRACION, "ev2", "token_vencido")])
    await _correr(base, lambda conn, http: cal)
    assert base.auditorias == [{"action": ACCION_AUDIT, "metadata": {"event_id": "ev2", "motivo": "token_vencido"}}]


async def test_evento_sin_integracion_se_audita_sin_llamar_a_calendar():
    base = BaseFalsa([_fila("ev1", integracion=None)])
    llamado = []
    await _correr(base, lambda conn, http: llamado.append(1))
    assert llamado == []
    assert base.auditorias[0]["metadata"] == {"event_id": "ev1", "motivo": MOTIVO_SIN_INTEGRACION}


async def test_calendario_sin_configurar_audita_cada_evento():
    base = BaseFalsa([_fila("ev1"), _fila("ev2")])

    def fabrica(conn, http):
        raise ErrorConfiguracionCalendar("faltan variables de entorno")

    await _correr(base, fabrica)
    assert [a["metadata"] for a in base.auditorias] == [
        {"event_id": "ev1", "motivo": MOTIVO_NO_CONFIGURADO},
        {"event_id": "ev2", "motivo": MOTIVO_NO_CONFIGURADO},
    ]


async def test_una_excepcion_inesperada_no_se_propaga_y_deja_constancia_por_evento():
    base = BaseFalsa([_fila("ev1")])
    cal = CalendarioFalso(levanta=RuntimeError("boom"))
    await _correr(base, lambda conn, http: cal)
    assert base.auditorias[0]["metadata"] == {"event_id": "ev1", "motivo": MOTIVO_INESPERADO}


@pytest.mark.parametrize("clave", ["phone", "name", "channel_user_id"])
async def test_el_audit_nunca_lleva_claves_de_pii(clave):
    base = BaseFalsa([_fila("ev1")])
    cal = CalendarioFalso([EventoNoLimpiado(INTEGRACION, "ev1", "error_api")])
    await _correr(base, lambda conn, http: cal)
    assert clave not in base.auditorias[0]["metadata"]
