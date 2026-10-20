"""Reintento de limpiezas de Calendar sin base: Google, pool y Sentry simulados."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from platform_calendar import EventoNoLimpiado
from platform_tenancy import ConexionBajoDemanda, tenant_tx
from turnos_negocio.calendario import TEXTO_EVENTO_ANONIMIZADO

from jobs_purga.reintento_calendar import (
    ConfigReintento,
    reintentar_limpiezas,
)

AHORA = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)  # noqa: UP017


class ErrorSql(Exception):
    def __init__(self, sqlstate):
        super().__init__("sql")
        self.sqlstate = sqlstate


class Conn:
    def __init__(self, base):
        self._base = base

    async def execute(self, sql, *args):
        self._base.sentencias.append(sql.split()[0])

    async def fetch(self, sql, *args):
        if self._base.falla_listado:
            raise RuntimeError("caída del listado")
        self._base.limites.append(args[0])
        return self._base.filas

    async def fetchval(self, sql, *args):
        if "record_calendar_scrub_result" in sql:
            tenant, evento, resuelta, motivo = args
            if evento in self._base.registro_falla:
                raise self._base.registro_falla[evento]
            self._base.registros.append((tenant, evento, resuelta, motivo))
        return


class Base:
    """Pool falso: guarda lo que se registró y cuántas conexiones hay tomadas en cada momento."""

    def __init__(self, filas):
        self.filas = filas
        self.registros = []
        self.registro_falla = {}
        self.limites = []
        self.sentencias = []
        self.falla_listado = False
        self.tomadas = 0

    @asynccontextmanager
    async def acquire(self):
        self.tomadas += 1
        try:
            yield Conn(self)
        finally:
            self.tomadas -= 1


class Reportador:
    def __init__(self):
        self.mensajes = []
        self.excepciones = []

    def capturar_mensaje(self, mensaje, **contexto):
        self.mensajes.append((mensaje, contexto))

    def capturar_excepcion(self, exc, **contexto):
        self.excepciones.append((exc, contexto))


class Calendario:
    """Servicio simulado: `resultados[event_id]` es un motivo (falla), una excepción o None (limpio)."""

    def __init__(self, base, resultados, llamadas, espera=0.0):
        self._base, self._resultados, self._llamadas, self._espera = base, resultados, llamadas, espera

    async def anonimizar_eventos(self, pares, texto):
        assert self._base.tomadas == 0, "no puede haber una conexión tomada mientras se llama a Google"
        if self._espera:
            await asyncio.sleep(self._espera)
        fallidos = []
        for integ, evento in pares:
            self._llamadas.append((integ, evento))
            r = self._resultados.get(evento)
            if isinstance(r, Exception):
                raise r
            if r is not None:
                fallidos.append(EventoNoLimpiado(integ, evento, r))
        return fallidos


def _fila(evento, *, intentos=1, dias=0, integracion="auto", motivo="http_500", tenant=None):
    return {
        "tenant_id": tenant or uuid.uuid4(),
        "integration_id": uuid.uuid4() if integracion == "auto" else integracion,
        "event_id": evento,
        "attempts": intentos,
        "first_failed_at": AHORA - timedelta(days=dias),
        "last_failed_at": AHORA - timedelta(hours=1),
        "last_motivo": motivo,
    }


def _armar(filas, resultados=None, espera=0.0):
    base = Base(filas)
    llamadas = []
    fabrica = lambda conn: Calendario(base, resultados or {}, llamadas, espera)  # noqa: E731
    return base, fabrica, llamadas, Reportador()


async def _correr(base, fabrica, reportador, config=None, **kw):
    return await reintentar_limpiezas(
        base, fabrica, reportador, config, texto_neutro=TEXTO_EVENTO_ANONIMIZADO, ahora=lambda: AHORA, **kw
    )


async def test_limpia_y_registra_resuelta_por_cada_pendiente():
    filas = [_fila("e1"), _fila("e2")]
    base, fabrica, llamadas, rep = _armar(filas)

    r = await _correr(base, fabrica, rep)

    assert r.pendientes == 2 and r.resueltas == 2 and r.ok
    assert [(x[1], x[2], x[3]) for x in base.registros] == [("e1", True, None), ("e2", True, None)]
    assert [e for _, e in llamadas] == ["e1", "e2"]
    assert rep.mensajes == [] and rep.excepciones == []


async def test_sin_pendientes_no_llama_a_google_ni_registra():
    base, fabrica, llamadas, rep = _armar([])
    r = await _correr(base, fabrica, rep)
    assert r.pendientes == 0 and r.ok and llamadas == [] and base.registros == []


async def test_el_limite_por_corrida_se_pasa_a_la_funcion_de_base():
    base, fabrica, _, rep = _armar([])
    await _correr(base, fabrica, rep, ConfigReintento(limite=37))
    assert base.limites == [37]


async def test_un_fallo_se_registra_con_su_motivo_y_no_corta_el_resto():
    base, fabrica, llamadas, rep = _armar(
        [_fila("e1"), _fila("e2"), _fila("e3")],
        {"e1": "http_500", "e2": RuntimeError("boom")},
    )

    r = await _correr(base, fabrica, rep)

    assert r.resueltas == 1 and r.reintentos_fallidos == 2 and r.ok
    assert [(x[1], x[2], x[3]) for x in base.registros] == [
        ("e1", False, "http_500"),
        ("e2", False, "error_inesperado"),
        ("e3", True, None),
    ]


async def test_el_timeout_por_evento_cuenta_como_fallo_y_sigue():
    base, fabrica, _, rep = _armar([_fila("e1")], espera=0.5)
    r = await _correr(base, fabrica, rep, ConfigReintento(timeout_evento=0.01))
    assert r.reintentos_fallidos == 1
    assert [(x[1], x[2], x[3]) for x in base.registros] == [("e1", False, "timeout")]


async def test_el_plazo_total_corta_la_pasada_y_las_demas_quedan_omitidas():
    base, fabrica, llamadas, rep = _armar([_fila("e1"), _fila("e2")])
    tiempos = iter([0.0, 0.0, 100.0, 100.0, 100.0])  # arranque, antes de e1, antes de e2
    r = await _correr(base, fabrica, rep, ConfigReintento(plazo_total=50), reloj=lambda: next(tiempos))
    assert r.resueltas == 1 and r.omitidas == 1
    assert [e for _, e in llamadas] == ["e1"]


async def test_integracion_nula_no_llama_a_google_pero_registra_el_intento_fallido_sin_integracion():
    base, fabrica, llamadas, rep = _armar([_fila("e1", integracion=None), _fila("e2")])
    r = await _correr(base, fabrica, rep)
    assert r.sin_integracion == 1 and r.resueltas == 1
    assert [e for _, e in llamadas] == ["e2"]
    assert [(x[1], x[2], x[3]) for x in base.registros] == [
        ("e1", False, "sin_integracion"),
        ("e2", True, None),
    ]


async def test_sin_integracion_bajo_el_umbral_no_alerta_y_el_job_sale_con_cero():
    base, fabrica, _, rep = _armar([_fila("e1", integracion=None, intentos=2, dias=1)])
    r = await _correr(base, fabrica, rep, ConfigReintento(umbral_intentos=5, umbral_dias=7))
    assert r.ok and r.persistentes == 0 and r.sin_integracion_persistentes == 0 and rep.mensajes == []


async def test_sin_integracion_en_el_umbral_alerta_que_se_descarten_a_mano_pero_no_falla_el_job():
    base, fabrica, _, rep = _armar([_fila("e1", integracion=None, intentos=4, dias=1)])  # este es el quinto
    r = await _correr(base, fabrica, rep, ConfigReintento(umbral_intentos=5, umbral_dias=7))
    assert r.ok and r.persistentes == 0 and r.sin_integracion_persistentes == 1
    ((mensaje, contexto),) = rep.mensajes
    assert "descartar" in mensaje and "manual" in mensaje
    assert contexto["paso"] == "calendar_scrub_sin_integracion" and contexto["sin_integracion"] == 1
    assert "e1" not in str(contexto) and str(base.filas[0]["tenant_id"]) not in str(contexto)


async def test_sin_integracion_vieja_por_dias_alerta_sin_fallar_el_job():
    base, fabrica, llamadas, rep = _armar([_fila("e1", integracion=None, dias=8)])
    r = await _correr(base, fabrica, rep, ConfigReintento(umbral_dias=7))
    assert r.ok and r.sin_integracion_persistentes == 1 and llamadas == [] and len(rep.mensajes) == 1


async def test_sin_integracion_con_error_al_registrar_cuenta_como_error_del_paso():
    base, fabrica, _, rep = _armar([_fila("e1", integracion=None)])
    base.registro_falla["e1"] = ErrorSql("08006")
    r = await _correr(base, fabrica, rep)
    assert r.errores == 1 and not r.ok


async def test_sin_integracion_ya_resuelta_por_otro_job_no_suma_intento():
    base, fabrica, _, rep = _armar([_fila("e1", integracion=None, intentos=4)])
    base.registro_falla["e1"] = ErrorSql("P0002")
    r = await _correr(base, fabrica, rep, ConfigReintento(umbral_intentos=5))
    assert r.ok and r.sin_integracion_persistentes == 0


async def test_calendar_sin_configurar_no_registra_un_intento_ni_cae():
    base, fabrica, _, rep = _armar([_fila("e1")], {"e1": "calendar_no_configurado"})
    r = await _correr(base, fabrica, rep)
    assert r.sin_configurar == 1 and r.ok and base.registros == []


async def test_ya_resuelta_por_otro_job_se_ignora():
    base, fabrica, _, rep = _armar([_fila("e1"), _fila("e2")])
    base.registro_falla["e1"] = ErrorSql("P0002")
    r = await _correr(base, fabrica, rep)
    assert r.resueltas == 1 and r.errores == 0 and r.ok


async def test_error_al_registrar_se_reporta_y_no_corta_el_resto():
    base, fabrica, _, rep = _armar([_fila("e1"), _fila("e2")])
    base.registro_falla["e1"] = ErrorSql("08006")
    r = await _correr(base, fabrica, rep)
    assert r.errores == 1 and r.resueltas == 1 and not r.ok
    assert len(rep.excepciones) == 1


async def test_error_al_listar_no_lanza_y_marca_el_job_como_fallido():
    base, fabrica, _, rep = _armar([_fila("e1")])
    base.falla_listado = True
    r = await _correr(base, fabrica, rep)
    assert r.errores == 1 and not r.ok and len(rep.excepciones) == 1


# --- umbral ---------------------------------------------------------------------------------------------------


async def test_bajo_el_umbral_no_alerta():
    base, fabrica, _, rep = _armar([_fila("e1", intentos=3, dias=2)], {"e1": "http_500"})
    r = await _correr(base, fabrica, rep, ConfigReintento(umbral_intentos=5, umbral_dias=7))
    assert r.persistentes == 0 and r.ok and rep.mensajes == []


async def test_alerta_al_llegar_al_umbral_de_intentos_contando_el_que_acaba_de_fallar():
    base, fabrica, _, rep = _armar([_fila("e1", intentos=4, dias=1)], {"e1": "http_500"})  # este es el quinto
    r = await _correr(base, fabrica, rep, ConfigReintento(umbral_intentos=5, umbral_dias=7))
    assert r.persistentes == 1 and not r.ok and len(rep.mensajes) == 1


async def test_alerta_por_dias_aunque_los_intentos_sean_pocos():
    base, fabrica, _, rep = _armar([_fila("e1", intentos=1, dias=7)], {"e1": "http_500"})
    r = await _correr(base, fabrica, rep, ConfigReintento(umbral_intentos=5, umbral_dias=7))
    assert r.persistentes == 1 and not r.ok


async def test_una_resuelta_vieja_no_alerta():
    base, fabrica, _, rep = _armar([_fila("e1", intentos=9, dias=30)])
    r = await _correr(base, fabrica, rep)
    assert r.resueltas == 1 and r.persistentes == 0 and r.ok and rep.mensajes == []


async def test_un_solo_reporte_agregado_sin_pii_ni_ids():
    filas = [
        _fila("evt-secreto-1", intentos=6),
        _fila("evt-secreto-2", intentos=8),
        _fila("e3", integracion=None, dias=9),
    ]
    base, fabrica, _, rep = _armar(filas, {"evt-secreto-1": "http_500", "evt-secreto-2": "token_vencido"})

    r = await _correr(base, fabrica, rep)

    assert r.persistentes == 2 and r.sin_integracion_persistentes == 1
    (mensaje, contexto), _otro = rep.mensajes
    assert contexto["persistentes"] == 2 and contexto["max_intentos"] == 9 and contexto["max_dias"] == 0
    assert contexto["motivos"] == "http_500:1,token_vencido:1"
    mensaje += str(_otro)
    texto = mensaje + str(contexto)
    for filtrada in ("evt-secreto", str(filas[0]["tenant_id"]), str(filas[0]["integration_id"])):
        assert filtrada not in texto


def test_config_desde_entorno_usa_defaults_y_tolera_basura():
    assert ConfigReintento.desde_entorno({}) == ConfigReintento()
    c = ConfigReintento.desde_entorno(
        {
            "CALENDAR_SCRUB_UMBRAL_INTENTOS": "3",
            "CALENDAR_SCRUB_UMBRAL_DIAS": "abc",
            "CALENDAR_SCRUB_LIMITE": "5000",
            "CALENDAR_SCRUB_TIMEOUT_SEG": "-1",
        }
    )
    assert c.umbral_intentos == 3 and c.umbral_dias == 7 and c.limite == 1000 and c.timeout_evento == 30.0


async def test_conexion_bajo_demanda_abre_una_transaccion_del_tenant_por_consulta():
    base = Base([])
    tenant = uuid.uuid4()
    conexion = ConexionBajoDemanda(lambda: tenant_tx(base, tenant))

    await conexion.fetchval("select 1")
    await conexion.fetchval("select 2")

    assert base.sentencias.count("BEGIN") == 2 and base.sentencias.count("COMMIT") == 2
    assert base.tomadas == 0
