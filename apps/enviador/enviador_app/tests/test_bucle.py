"""Bucle del enviador: cadencia, concurrencia, plazos, apagado y errores que no lo matan. El procesador es un doble."""

import asyncio
import json
import logging
import uuid

from enviador_app.bucle import Contadores, Enviador
from enviador_app.procesador import ResultadoCanal, SalidaFila
from enviador_app.tests.soporte import EMAIL_CLIENTE, Mundo, Reportes, fila

TENANT = uuid.uuid4()


class ProcesadorFalso:
    """Reemplaza a `ProcesadorOutbox`: devuelve lo que se le indique por fila y recuerda lo que le llegó."""

    def __init__(self, salidas=None, bloqueo=None):
        self.salidas = salidas or {}
        self.bloqueo = bloqueo  # asyncio.Event que hay que abrir para que `procesar` termine
        self.vistas = []
        self.liberadas = []
        self.agotadas = []
        self.en_curso = 0
        self.maximo_en_curso = 0

    async def procesar(self, fila_, credenciales):
        self.vistas.append(fila_["id"])
        self.en_curso += 1
        self.maximo_en_curso = max(self.maximo_en_curso, self.en_curso)
        try:
            if self.bloqueo is not None:
                await self.bloqueo.wait()
            salida = self.salidas.get(fila_["id"], SalidaFila("enviada"))
            if isinstance(salida, Exception):
                raise salida
            return salida
        finally:
            self.en_curso -= 1

    async def liberar(self, fila_):
        self.liberadas.append(fila_["id"])
        return True

    async def cerrar_agotada(self, fila_):
        self.agotadas.append(fila_["id"])
        return True


def _enviador(mundo, procesador, reportes=None, *, dormir=None, **cambios):
    opciones = {
        "lote": 4,
        "concurrencia": 2,
        "arriendo_seg": 300,
        "max_intentos": 5,
        "plazo_fila_seg": 5.0,
        "plazo_apagado_seg": 1.0,
        "intervalo_seg": 2.0,
        "espera_maxima_seg": 10.0,
        "margen_cierre_seg": 0.0,
    }
    opciones.update(cambios)
    if dormir is not None:
        opciones["dormir"] = dormir
    return Enviador(mundo, procesador, reportar=reportes if reportes is not None else Reportes(), **opciones)


def _filas(n):
    return [fila(TENANT) for _ in range(n)]


class DormirRegistrado:
    """`dormir` del bucle: recuerda cada espera y pide detener después de `veces` vueltas."""

    def __init__(self, detener, veces):
        self.detener, self.veces, self.segundos = detener, veces, []

    async def __call__(self, detener, segundos):
        self.segundos.append(segundos)
        if len(self.segundos) >= self.veces:
            self.detener.set()


async def test_el_reclamo_pide_el_lote_el_arriendo_y_el_tope_de_intentos_configurados():
    m = Mundo()
    m.cola = _filas(2)
    env = _enviador(m, ProcesadorFalso(), lote=4, arriendo_seg=450, max_intentos=3)
    assert await env.ciclo(asyncio.Event()) == 2
    assert m.reclamos == [(4, 450, 3)]


async def test_procesa_cada_fila_reclamada_y_cuenta_el_resultado():
    m = Mundo()
    m.cola = filas = _filas(3)
    canales = [
        ResultadoCanal("telegram", "cc:tg", "sent"),
        ResultadoCanal("email", "cc:mail", "failed", "x", "http_500"),
    ]
    proc = ProcesadorFalso({filas[1]["id"]: SalidaFila("reprogramada", "email: x", canales)})
    env = _enviador(m, proc)
    await env.ciclo(asyncio.Event())
    assert sorted(proc.vistas) == sorted(f["id"] for f in filas)
    c = env.contadores
    assert (c.reclamadas, c.enviadas, c.reprogramadas, c.fallos) == (3, 2, 1, 0)
    assert c.por_canal == {"telegram": {"sent": 1, "failed": 0}, "email": {"sent": 0, "failed": 1}}


async def test_cada_tipo_de_salida_tiene_su_contador_y_su_log(caplog):
    m = Mundo()
    m.cola = filas = _filas(6)
    tipos = ["enviada", "reprogramada", "descartada", "cerrada", "omitida", "fallo"]
    proc = ProcesadorFalso({f["id"]: SalidaFila(t, f"motivo_{t}") for f, t in zip(filas, tipos, strict=True)})
    reportes = Reportes()
    env = _enviador(m, proc, reportes, lote=6)
    with caplog.at_level(logging.INFO):
        await env.ciclo(asyncio.Event())
    c = env.contadores
    assert (c.enviadas, c.reprogramadas, c.descartadas, c.cerradas, c.omitidas, c.fallos) == (1, 1, 1, 1, 1, 1)
    eventos = {r.getMessage(): r for r in caplog.records}
    for evento in ("outbox_enviada", "outbox_reprogramada", "outbox_descartada", "outbox_cerrada", "outbox_omitida"):
        assert str(TENANT) == eventos[evento].tenant_id
    assert eventos["outbox_fallo"].motivo == "motivo_fallo"
    # solo el fallo se reporta, con el tenant y el motivo
    assert reportes.items == [("falló un aviso del outbox", {"motivo": "motivo_fallo", "tenant_id": str(TENANT)})]
    resumen = eventos["lote_procesado"]
    assert (resumen.reclamadas, resumen.enviadas, resumen.fallos, resumen.omitidas) == (6, 1, 1, 1)


async def test_una_excepcion_en_una_fila_no_corta_a_las_demas_y_se_reporta_sin_datos(caplog):
    m = Mundo()
    m.cola = filas = _filas(3)
    proc = ProcesadorFalso({filas[0]["id"]: RuntimeError(f"falla con {EMAIL_CLIENTE}")})
    reportes = Reportes()
    env = _enviador(m, proc, reportes)
    with caplog.at_level(logging.INFO):
        await env.ciclo(asyncio.Event())
    assert (env.contadores.enviadas, env.contadores.fallos) == (2, 1)
    assert reportes.items[0][1]["motivo"] == "error_inesperado: RuntimeError"
    assert EMAIL_CLIENTE not in json.dumps(reportes.items, default=str)
    assert EMAIL_CLIENTE not in " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)


async def test_el_plazo_de_la_fila_la_corta_y_las_demas_siguen():
    m = Mundo()
    m.cola = filas = _filas(2)

    class Lento(ProcesadorFalso):
        async def procesar(self, fila_, credenciales):
            if fila_["id"] == filas[0]["id"]:
                await asyncio.sleep(10)
            return await super().procesar(fila_, credenciales)

    reportes = Reportes()
    proc = Lento()
    env = _enviador(m, proc, reportes, plazo_fila_seg=0.05)
    await env.ciclo(asyncio.Event())
    assert (env.contadores.enviadas, env.contadores.fallos) == (1, 1)
    assert proc.agotadas == [filas[0]["id"]]  # se le pide cerrar según lo entregado (en el último reclamo)
    assert reportes.items[0][1]["motivo"] == "plazo_agotado"


async def test_la_concurrencia_esta_acotada():
    m = Mundo()
    m.cola = _filas(6)

    class Medido(ProcesadorFalso):
        async def procesar(self, fila_, credenciales):
            await asyncio.sleep(0.02)
            return await super().procesar(fila_, credenciales)

    proc = Medido()
    env = _enviador(m, proc, lote=6, concurrencia=2)
    await env.ciclo(asyncio.Event())
    assert env.contadores.enviadas == 6 and proc.maximo_en_curso <= 2


async def test_con_el_lote_lleno_no_espera_y_sin_trabajo_la_espera_crece_hasta_el_tope():
    m = Mundo()
    m.cola = _filas(8)  # dos lotes llenos y después nada
    detener = asyncio.Event()
    dormir = DormirRegistrado(detener, veces=6)
    env = _enviador(m, ProcesadorFalso(), dormir=dormir, lote=4, intervalo_seg=2.0, espera_maxima_seg=10.0)
    await env.correr(detener)
    # lote lleno, lote lleno (sin esperar), vacío: 2, 4, 8, 10, 10, 10
    assert dormir.segundos == [2.0, 4.0, 8.0, 10.0, 10.0, 10.0]
    assert len(m.reclamos) == 2 + 6  # dos lotes llenos seguidos y un reclamo por cada espera
    assert env.contadores.enviadas == 8


async def test_con_trabajo_a_medias_espera_el_intervalo_y_despues_de_trabajar_vuelve_al_intervalo():
    m = Mundo()
    m.cola = _filas(2)  # menos que el lote
    detener = asyncio.Event()
    dormir = DormirRegistrado(detener, veces=3)
    env = _enviador(m, ProcesadorFalso(), dormir=dormir, lote=4, intervalo_seg=2.0, espera_maxima_seg=10.0)
    await env.correr(detener)
    assert dormir.segundos == [2.0, 2.0, 4.0]  # con trabajo (2 s), primer vacío (2 s), segundo vacío (4 s)


async def test_un_error_al_reclamar_no_mata_el_bucle_se_reporta_una_vez_y_se_recupera(caplog):
    m = Mundo()
    m.falla_reclamo = [RuntimeError("base caída"), RuntimeError("base caída")]
    m.cola = _filas(1)
    detener = asyncio.Event()
    dormir = DormirRegistrado(detener, veces=3)
    reportes = Reportes()
    env = _enviador(m, ProcesadorFalso(), reportes, dormir=dormir, lote=4)
    with caplog.at_level(logging.INFO):
        await env.correr(detener)
    assert env.contadores.enviadas == 1  # el tercer reclamo funcionó
    assert reportes.items == [("no se pudo reclamar el outbox de avisos", {"motivo": "RuntimeError"})]
    mensajes = [r.getMessage() for r in caplog.records]
    assert mensajes.count("ciclo_fallido") == 2 and "reclamo_recuperado" in mensajes


async def test_una_nueva_racha_de_errores_se_vuelve_a_reportar():
    m = Mundo()
    detener = asyncio.Event()
    reportes = Reportes()
    m.falla_reclamo = [RuntimeError("x")]

    class Dormir(DormirRegistrado):
        async def __call__(self, detener_, segundos):
            await super().__call__(detener_, segundos)
            if len(self.segundos) == 2:
                m.falla_reclamo.append(RuntimeError("otra vez"))

    env = _enviador(m, ProcesadorFalso(), reportes, dormir=Dormir(detener, 4))
    await env.correr(detener)
    assert len(reportes.items) == 2


async def test_pedir_detener_durante_la_espera_corta_la_espera_y_termina():
    m = Mundo()
    detener = asyncio.Event()
    env = _enviador(m, ProcesadorFalso(), intervalo_seg=30.0, espera_maxima_seg=60.0)
    tarea = asyncio.ensure_future(env.correr(detener))
    await asyncio.sleep(0.05)
    detener.set()
    await asyncio.wait_for(tarea, 2)  # no espera los 30 s


async def test_sigterm_a_mitad_de_lote_termina_la_fila_en_curso_y_devuelve_las_que_no_empezaron():
    m = Mundo()
    m.cola = filas = _filas(3)
    bloqueo = asyncio.Event()
    proc = ProcesadorFalso(bloqueo=bloqueo)
    detener = asyncio.Event()
    env = _enviador(m, proc, lote=3, concurrencia=1, plazo_apagado_seg=2.0)
    ciclo = asyncio.ensure_future(env.ciclo(detener))
    await asyncio.sleep(0.05)
    assert len(proc.vistas) == 1  # la primera está en curso; las otras esperan su turno
    detener.set()
    await asyncio.sleep(0.05)
    bloqueo.set()  # la fila en curso termina dentro del plazo de apagado
    assert await asyncio.wait_for(ciclo, 3) == 3
    assert proc.vistas == [filas[0]["id"]]  # las otras dos nunca empezaron
    assert sorted(proc.liberadas) == sorted(f["id"] for f in filas[1:])
    assert (env.contadores.enviadas, env.contadores.liberadas) == (1, 2)


async def test_sigterm_con_una_fila_que_no_termina_la_cancela_vencido_el_plazo_y_la_devuelve():
    m = Mundo()
    m.cola = filas = _filas(2)
    proc = ProcesadorFalso(bloqueo=asyncio.Event())  # nunca se abre
    detener = asyncio.Event()
    reportes = Reportes()
    env = _enviador(m, proc, reportes, lote=2, concurrencia=1, plazo_apagado_seg=0.1)
    ciclo = asyncio.ensure_future(env.ciclo(detener))
    await asyncio.sleep(0.05)
    detener.set()
    await asyncio.wait_for(ciclo, 3)
    assert sorted(proc.liberadas) == sorted(f["id"] for f in filas)
    assert env.contadores.enviadas == 0 and env.contadores.fallos == 0 and reportes.items == []
    assert proc.en_curso == 0  # la tarea cancelada terminó


async def test_si_devolver_las_filas_falla_el_apagado_igual_termina():
    m = Mundo()
    m.cola = _filas(2)

    class SinLiberar(ProcesadorFalso):
        async def liberar(self, fila_):
            raise RuntimeError("base caída")

    detener = asyncio.Event()
    env = _enviador(m, SinLiberar(bloqueo=asyncio.Event()), lote=2, concurrencia=1, plazo_apagado_seg=0.05)
    ciclo = asyncio.ensure_future(env.ciclo(detener))
    await asyncio.sleep(0.05)
    detener.set()
    await asyncio.wait_for(ciclo, 3)
    assert env.contadores.liberadas == 0


async def test_sin_filas_el_ciclo_no_hace_nada():
    m = Mundo()
    env = _enviador(m, ProcesadorFalso())
    assert await env.ciclo(asyncio.Event()) == 0 and env.contadores == Contadores()


async def test_un_reclamo_que_siempre_falla_no_cuenta_como_vuelta_sana_para_el_health():
    m = Mundo()
    m.falla_reclamo = [RuntimeError("x")] * 3
    detener = asyncio.Event()
    env = _enviador(m, ProcesadorFalso(), dormir=DormirRegistrado(detener, veces=3))
    await env.correr(detener)
    assert env.ultimo_ciclo is None
    detener2 = asyncio.Event()
    env2 = _enviador(Mundo(), ProcesadorFalso(), dormir=DormirRegistrado(detener2, veces=1))
    await env2.correr(detener2)
    assert env2.ultimo_ciclo is not None
