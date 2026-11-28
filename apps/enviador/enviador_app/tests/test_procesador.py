"""Una fila del outbox: destinatarios, canales, reintentos, cierre y registro por canal, con dobles de prueba."""

import json
import logging
import uuid

import httpx
import pytest
from platform_notifications import BACKOFF_POR_DEFECTO

from enviador_app.procesador import MAX_DESTINATARIOS_POR_FILA, CredencialesDelLote, ProcesadorOutbox
from enviador_app.tests.soporte import (
    CHAT,
    EMAIL_CLIENTE,
    EMAIL_NEGOCIO,
    TOKEN,
    CorreoSimulado,
    Esperas,
    Mundo,
    Reportes,
    Telegram,
    destino,
    email,
    error_email,
    fila,
    sin_canal,
    sin_espera,
    telegram,
)

MAX_INTENTOS = 5
REINTENTO = 300
TENANT = uuid.uuid4()


def _procesador(mundo, tg=None, correo=None, esperar=sin_espera, reportes=None, backoff=BACKOFF_POR_DEFECTO):
    tg = tg or Telegram(mundo=mundo)
    correo = correo or CorreoSimulado(mundo=mundo)
    return (
        ProcesadorOutbox(
            mundo,
            cliente_http=tg.cliente,
            cliente_email=correo,
            base_url_telegram="https://api.telegram.org",
            backoff=backoff,
            esperar=esperar,
            max_intentos=MAX_INTENTOS,
            reintento_seg=REINTENTO,
            reportar=reportes if reportes is not None else Reportes(),
        ),
        tg,
        correo,
    )


def _mundo(*pares, bot=True):
    m = Mundo()
    for f, d in pares:
        m.poner(f, d)
        if bot:
            m.con_bot(f["tenant_id"])
    return m


async def _procesar(proc, f):
    return await proc.procesar(f, CredencialesDelLote())


def _cierre(m, f):
    (c,) = [c for c in m.cierres if c[0] == f["id"]]
    return c[1], c[2], c[3], c[4]  # status, error, reintento, tenant del contexto


# --- Por canal y mixto ---------------------------------------------------------------------------------------------


async def test_envia_por_telegram_y_por_email_y_cierra_la_fila():
    f = fila(TENANT)
    m = _mundo((f, [telegram(), email()]))
    proc, tg, correo = _procesador(m)
    salida = await _procesar(proc, f)
    assert salida.tipo == "enviada"
    assert tg.chats == [CHAT]
    assert [(d, a) for d, a, _ in correo.enviados] == [(EMAIL_CLIENTE, "Tu turno fue confirmado")]
    assert _cierre(m, f) == ("sent", None, None, str(TENANT))
    assert m.entregas == {(f["id"], "cc:tg"): ("sent", None), (f["id"], "cc:mail"): ("sent", None)}


async def test_el_texto_del_aviso_sale_por_los_dos_canales_y_usa_el_token_del_negocio():
    f = fila(TENANT, "turno_cancelado")
    m = _mundo((f, [telegram(), email()]))
    proc, tg, correo = _procesador(m)
    await _procesar(proc, f)
    ((url, cuerpo),) = tg.pedidos
    assert f"/bot{TOKEN}/sendMessage" in url
    assert cuerpo["text"] == correo.enviados[0][2]
    assert "Corte de pelo" in cuerpo["text"] and "cancelado" in cuerpo["text"]


async def test_si_falla_telegram_con_un_error_permanente_el_email_sale_igual():
    f = fila(TENANT)
    m = _mundo((f, [telegram(), email()]))
    proc, tg, correo = _procesador(m, Telegram({CHAT: 403}, mundo=None))
    salida = await _procesar(proc, f)
    assert [(c.canal, c.estado, c.permanente) for c in salida.canales] == [
        ("telegram", "failed", True),
        ("email", "sent", False),
    ]
    assert len(correo.enviados) == 1
    # el 403 es permanente (sin reintentos); el email llegó, así que la fila se cierra `sent` con el motivo del fallo
    assert salida.tipo == "enviada"
    status, error, _, _ = _cierre(m, f)
    assert status == "sent" and error.startswith("telegram: 403")
    assert m.entregas[(f["id"], "cc:tg")] == ("failed", "http_403")
    assert m.entregas[(f["id"], "cc:mail")] == ("sent", None)


async def test_si_falla_el_email_de_forma_transitoria_telegram_sale_y_la_fila_se_reprograma():
    f = fila(TENANT, intentos=1)
    m = _mundo((f, [telegram(), email()]))
    proc, tg, correo = _procesador(m, correo=CorreoSimulado({EMAIL_CLIENTE: error_email(503)}, mundo=m))
    salida = await _procesar(proc, f)
    assert salida.tipo == "reprogramada"
    assert tg.chats == [CHAT]
    assert len(correo.enviados) == 1 + len(BACKOFF_POR_DEFECTO)  # primer intento y los tres reintentos
    status, error, reintento, _ = _cierre(m, f)
    assert (status, reintento) == ("pending", REINTENTO) and error.startswith("email: ")
    assert m.entregas[(f["id"], "cc:tg")] == ("sent", None)
    assert m.entregas[(f["id"], "cc:mail")] == ("failed", "http_503")


async def test_el_reintento_no_repite_el_canal_ya_entregado_y_solo_envia_el_email():
    """Una fila con el Telegram ya registrado envía únicamente el email."""
    f = fila(TENANT, intentos=2)
    m = _mundo((f, [telegram(entregado=True), email()]))
    proc, tg, correo = _procesador(m)
    salida = await _procesar(proc, f)
    assert tg.pedidos == []
    assert [d for d, _, _ in correo.enviados] == [EMAIL_CLIENTE]
    assert salida.tipo == "enviada" and _cierre(m, f)[0] == "sent"
    assert [(c[2]) for c in m.entregas_llamadas] == ["cc:mail"]  # no registra de nuevo el Telegram


async def test_dos_pasadas_sobre_la_misma_fila_no_repiten_lo_que_ya_salio():
    f = fila(TENANT, intentos=1)
    m = _mundo((f, [telegram(), email()]))
    proc, tg, correo = _procesador(m, correo=CorreoSimulado({EMAIL_CLIENTE: error_email(500)}, mundo=m))
    assert (await _procesar(proc, f)).tipo == "reprogramada"
    correo.errores.clear()  # el proveedor se recupera
    f["attempts"] = 2
    assert (await _procesar(proc, f)).tipo == "enviada"
    assert tg.chats == [CHAT]  # Telegram salió una sola vez entre las dos pasadas
    assert len(correo.enviados) == 1 + len(BACKOFF_POR_DEFECTO) + 1


async def test_con_todos_los_canales_ya_entregados_solo_cierra_la_fila():
    f = fila(TENANT)
    m = _mundo((f, [telegram(entregado=True), email(entregado=True)]))
    proc, tg, correo = _procesador(m)
    salida = await _procesar(proc, f)
    assert salida.tipo == "enviada" and tg.pedidos == [] and correo.enviados == []
    assert _cierre(m, f)[0] == "sent"


async def test_fallan_los_dos_canales_de_forma_transitoria_con_intentos_disponibles():
    f = fila(TENANT, intentos=4)
    m = _mundo((f, [telegram(), email()]))
    proc, _, _ = _procesador(m, Telegram({CHAT: 500}), CorreoSimulado({EMAIL_CLIENTE: error_email(500)}))
    salida = await _procesar(proc, f)
    assert salida.tipo == "reprogramada"
    assert _cierre(m, f)[:3:2] == ("pending", REINTENTO)


async def test_en_el_ultimo_intento_un_fallo_transitorio_sin_nada_entregado_deja_la_fila_failed():
    f = fila(TENANT, intentos=MAX_INTENTOS)
    m = _mundo((f, [telegram(), email()]))
    proc, _, _ = _procesador(m, Telegram({CHAT: 500}), CorreoSimulado({EMAIL_CLIENTE: error_email(500)}))
    salida = await _procesar(proc, f)
    assert salida.tipo == "fallo"
    status, error, _, _ = _cierre(m, f)
    assert status == "failed" and "telegram: " in error and "email: " in error


async def test_en_el_ultimo_intento_con_un_canal_entregado_la_fila_se_cierra_sent():
    f = fila(TENANT, intentos=MAX_INTENTOS)
    m = _mundo((f, [telegram(), email()]))
    proc, _, _ = _procesador(m, correo=CorreoSimulado({EMAIL_CLIENTE: error_email(500)}))
    salida = await _procesar(proc, f)
    assert salida.tipo == "enviada" and _cierre(m, f)[0] == "sent"


async def test_los_dos_canales_fallan_de_forma_permanente():
    f = fila(TENANT, intentos=1)
    m = _mundo((f, [telegram(), email()]))
    proc, _, correo = _procesador(m, Telegram({CHAT: 400}), CorreoSimulado({EMAIL_CLIENTE: error_email(422)}))
    salida = await _procesar(proc, f)
    assert salida.tipo == "fallo" and _cierre(m, f)[0] == "failed"
    assert len(correo.enviados) == 1  # un 4xx no se reintenta


async def test_un_timeout_de_telegram_se_reintenta_con_el_backoff_de_la_politica():
    f = fila(TENANT, intentos=1)
    m = _mundo((f, [telegram()]))
    esperas = Esperas()
    proc, tg, _ = _procesador(m, Telegram(error_de_red=httpx.ReadTimeout("x")), esperar=esperas)
    salida = await _procesar(proc, f)
    assert esperas.segundos == [1, 5, 25]
    assert salida.tipo == "reprogramada" and _cierre(m, f)[:3:2] == ("pending", REINTENTO)
    assert salida.canales[0].codigo == "red"


async def test_un_timeout_del_email_es_transitorio_y_no_pierde_el_telegram():
    f = fila(TENANT, intentos=1)
    m = _mundo((f, [telegram(), email()]))
    proc, tg, _ = _procesador(m, correo=CorreoSimulado({EMAIL_CLIENTE: error_email(None)}))
    salida = await _procesar(proc, f)
    assert salida.tipo == "reprogramada" and tg.chats == [CHAT]
    assert m.entregas[(f["id"], "cc:mail")] == ("failed", "red")


# --- Credenciales ----------------------------------------------------------------------------------------------------


async def test_sin_integracion_de_telegram_ese_canal_falla_y_el_email_sale():
    f = fila(TENANT)
    m = _mundo((f, [telegram(), email()]), bot=False)
    proc, tg, correo = _procesador(m)
    salida = await _procesar(proc, f)
    assert tg.pedidos == [] and len(correo.enviados) == 1
    assert [(c.canal, c.codigo, c.permanente) for c in salida.canales if c.estado == "failed"] == [
        ("telegram", "sin_integracion", True)
    ]
    assert salida.tipo == "enviada"


async def test_solo_telegram_y_sin_integracion_deja_la_fila_failed_sin_reintentar():
    f = fila(TENANT)
    m = _mundo((f, telegram()), bot=False)
    proc, tg, _ = _procesador(m)
    salida = await _procesar(proc, f)
    assert tg.pedidos == [] and salida.tipo == "fallo"
    assert _cierre(m, f)[:2] == ("failed", "telegram: IntegracionNoConfigurada")


async def test_un_secret_sin_token_es_una_credencial_invalida_y_no_se_envia():
    f = fila(TENANT)
    m = _mundo((f, telegram()), bot=False)
    m.integraciones[str(TENANT)] = json.dumps({"webhook_secret": "x"})
    proc, tg, _ = _procesador(m)
    salida = await _procesar(proc, f)
    assert tg.pedidos == [] and salida.tipo == "fallo"
    assert _cierre(m, f)[:2] == ("failed", "telegram: CredencialInvalida")


async def test_un_token_revocado_es_permanente_y_no_se_reintenta():
    f = fila(TENANT)
    m = _mundo((f, telegram()))
    esperas = Esperas()
    proc, tg, _ = _procesador(m, Telegram({CHAT: 401}), esperar=esperas)
    salida = await _procesar(proc, f)
    assert esperas.segundos == [] and len(tg.pedidos) == 1 and salida.tipo == "fallo"


async def test_las_filas_del_mismo_tenant_comparten_la_lectura_del_token_del_lote():
    filas = [fila(TENANT) for _ in range(3)]
    m = _mundo(*[(f, telegram()) for f in filas])
    proc, tg, _ = _procesador(m)
    lote = CredencialesDelLote()
    for f in filas:
        await proc.procesar(f, lote)
    assert len(tg.pedidos) == 3
    assert m.lecturas_de_secret == [str(TENANT)]  # una sola lectura por tenant y lote
    otra = fila(TENANT)
    m.poner(otra, telegram())
    await proc.procesar(otra, CredencialesDelLote())
    assert m.lecturas_de_secret == [str(TENANT)] * 2  # el lote siguiente vuelve a leerlo (toma una rotación)


# --- Sin a quién enviar -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("destinos", "motivo"),
    [
        (None, "turno_inexistente"),
        ([telegram(tenant_active=False), email(tenant_active=False)], "tenant_inactivo"),
        ([sin_canal(anonimizada=True)], "cliente_sin_destinatario"),
        ([sin_canal()], "sin_destinatarios"),
    ],
)
async def test_sin_a_quien_avisar_la_fila_se_cierra_failed_con_el_motivo_y_no_se_envia(destinos, motivo):
    f = fila(TENANT)
    m = _mundo((f, destinos))
    proc, tg, correo = _procesador(m)
    salida = await _procesar(proc, f)
    assert (salida.tipo, salida.motivo) == ("descartada", motivo)
    assert _cierre(m, f)[:2] == ("failed", motivo)
    assert tg.pedidos == [] and correo.enviados == [] and m.entregas == {}


async def test_un_destinatario_vacio_de_un_aviso_al_negocio_se_cierra_failed():
    f = fila(TENANT, "aviso_negocio_reserva", negocio=True)
    m = _mundo((f, [sin_canal(destino_fila="negocio")]))
    proc, _, correo = _procesador(m)
    assert (await _procesar(proc, f)).motivo == "sin_destinatarios" and correo.enviados == []


async def test_una_plantilla_que_no_es_un_aviso_de_turno_se_descarta():
    f = fila(TENANT, "plantilla_inexistente")
    m = _mundo((f, [telegram()]))
    proc, tg, _ = _procesador(m)
    assert (await _procesar(proc, f)).motivo == "plantilla_desconocida" and tg.pedidos == []


async def test_la_sincronizacion_con_calendar_no_se_envia_ni_se_cierra():
    f = fila(TENANT, "sincronizacion_calendar", negocio=True)
    m = _mundo((f, [destino("calendar", None, clave="calendar", destino_fila="negocio")]))
    proc, tg, correo = _procesador(m)
    salida = await _procesar(proc, f)
    assert (salida.tipo, salida.motivo) == ("omitida", "sincronizacion_calendar")
    assert m.cierres == [] and tg.pedidos == [] and correo.enviados == [] and m.entregas == {}


# --- Fila reclamada por otra instancia / cierre -------------------------------------------------------------------


async def test_si_otra_instancia_ya_cerro_la_fila_el_cierre_no_la_pisa():
    f = fila(TENANT)
    m = _mundo((f, [telegram()]))
    m.cierres_perdidos.add(f["id"])
    proc, _, _ = _procesador(m)
    assert (await _procesar(proc, f)).tipo == "cerrada"


async def test_si_el_cierre_falla_la_fila_queda_pending_y_no_corta_el_proceso(caplog):
    f = fila(TENANT)
    m = _mundo((f, [telegram()]))
    m.falla_cierre = RuntimeError("base caída")
    proc, tg, _ = _procesador(m)
    with caplog.at_level(logging.WARNING):
        salida = await _procesar(proc, f)
    assert salida.tipo == "cerrada" and tg.chats == [CHAT]
    assert any(r.getMessage() == "outbox_sin_cerrar" for r in caplog.records)


async def test_si_no_se_puede_resolver_el_error_sube_para_que_lo_cuente_quien_llama():
    f = fila(TENANT)
    m = _mundo((f, [telegram()]))
    m.falla_resolver = True
    proc, tg, _ = _procesador(m)
    with pytest.raises(RuntimeError):
        await _procesar(proc, f)
    assert tg.pedidos == [] and m.cierres == []


async def test_si_falla_el_registro_en_notifications_log_el_envio_sale_igual_y_una_sola_vez(caplog):
    f = fila(TENANT)
    m = _mundo((f, [telegram()]))
    m.falla_registro_envio = RuntimeError("log caído")
    proc, tg, _ = _procesador(m)
    with caplog.at_level(logging.WARNING):
        salida = await _procesar(proc, f)
    assert salida.tipo == "enviada" and len(tg.pedidos) == 1
    assert any(r.getMessage() == "registro_de_envio_fallido" for r in caplog.records)


async def test_si_no_se_puede_registrar_la_entrega_se_cuenta_pero_no_se_pierde_el_envio(caplog):
    f = fila(TENANT)
    m = _mundo((f, [telegram()]))
    m.falla_registrar_entrega = RuntimeError("base caída")
    proc, tg, _ = _procesador(m)
    with caplog.at_level(logging.WARNING):
        salida = await _procesar(proc, f)
    assert salida.tipo == "enviada" and len(tg.pedidos) == 1
    assert salida.canales[0].registrada is False
    assert any(r.getMessage() == "entrega_sin_registrar" for r in caplog.records)


async def test_un_error_inesperado_en_un_canal_no_impide_el_otro():
    f = fila(TENANT)
    m = _mundo((f, [telegram(), email()]))

    class CorreoRoto(CorreoSimulado):
        async def enviar(self, *a, **k):
            raise RuntimeError("falla con " + EMAIL_CLIENTE)

    proc, tg, _ = _procesador(m, correo=CorreoRoto(), backoff=())
    salida = await _procesar(proc, f)
    assert tg.chats == [CHAT]
    mail = [c for c in salida.canales if c.canal == "email"][0]
    assert mail.estado == "failed" and "interno" in mail.motivo
    assert EMAIL_CLIENTE not in json.dumps(
        [c.motivo for c in salida.canales]
    )  # el texto de la excepción no se arrastra


# --- Avisos al negocio -----------------------------------------------------------------------------------------------


async def test_un_aviso_al_negocio_va_por_email_a_cada_destinatario_y_no_toca_notifications_log():
    f = fila(TENANT, "aviso_negocio_reserva", negocio=True)
    otro = "socia@negocio.test"
    m = _mundo(
        (
            f,
            [
                destino("email", EMAIL_NEGOCIO, clave="tu:1:email", destino_fila="negocio"),
                destino("email", otro, clave="tu:2:email", destino_fila="negocio"),
            ],
        )
    )
    proc, tg, correo = _procesador(m)
    salida = await _procesar(proc, f)
    assert salida.tipo == "enviada" and tg.pedidos == []
    assert sorted(d for d, _, _ in correo.enviados) == sorted([EMAIL_NEGOCIO, otro])
    assert {a for _, a, _ in correo.enviados} == {"Nueva reserva"}
    assert "Nueva reserva: Corte de pelo con Ana" in correo.enviados[0][2]
    assert m.registros == []  # no hay ficha de cliente: `record_notification_attempt` no aplica
    assert set(m.entregas) == {(f["id"], "tu:1:email"), (f["id"], "tu:2:email")}


async def test_un_destinatario_del_negocio_que_falla_no_impide_a_los_demas():
    f = fila(TENANT, "aviso_negocio_cancelacion", negocio=True)
    m = _mundo(
        (
            f,
            [
                destino("email", EMAIL_NEGOCIO, clave="tu:1:email", destino_fila="negocio"),
                destino("email", "otra@negocio.test", clave="tu:2:email", destino_fila="negocio"),
            ],
        )
    )
    proc, _, correo = _procesador(m, correo=CorreoSimulado({EMAIL_NEGOCIO: error_email(422)}))
    salida = await _procesar(proc, f)
    assert len(correo.enviados) == 2 and salida.tipo == "enviada"


# --- Registro en notifications_log y aislamiento -------------------------------------------------------------------


async def test_cada_registro_de_envio_lleva_el_tenant_de_la_fila_y_su_canal():
    otro_tenant = uuid.uuid4()
    f1, f2 = fila(TENANT), fila(otro_tenant)
    m = _mundo((f1, [telegram(), email()]), (f2, [telegram(), email()]))
    proc, _, _ = _procesador(m)
    for f in (f1, f2):
        await _procesar(proc, f)
    por_tenant = {}
    for ctx, cliente, canal, plantilla, estado in m.registros:
        por_tenant.setdefault(ctx, set()).add((cliente, canal, plantilla, estado))
    assert set(por_tenant) == {str(TENANT), str(otro_tenant)}
    for f in (f1, f2):
        assert por_tenant[str(f["tenant_id"])] == {
            (f["end_customer_id"], canal, "turno_confirmado", estado)
            for canal in ("telegram", "email")
            for estado in ("pending", "sent")
        }
    # cierre y entregas, con el contexto de la fila
    assert {c[4] for c in m.cierres if c[0] == f1["id"]} == {str(TENANT)}
    assert {e[0] for e in m.entregas_llamadas if e[1] == f2["id"]} == {str(otro_tenant)}


async def test_cada_tenant_envia_con_su_propio_token():
    otro = uuid.uuid4()
    f1, f2 = fila(TENANT), fila(otro)
    m = Mundo()
    for f in (f1, f2):
        m.poner(f, telegram())
    m.con_bot(TENANT, "111111:token-del-primero")
    m.con_bot(otro, "222222:token-del-segundo")
    proc, tg, _ = _procesador(m)
    lote = CredencialesDelLote()
    await proc.procesar(f1, lote)
    await proc.procesar(f2, lote)
    assert [
        "/bot111111:token-del-primero/" in tg.pedidos[0][0],
        "/bot222222:token-del-segundo/" in tg.pedidos[1][0],
    ] == [
        True,
        True,
    ]


# --- Llamadas externas fuera de la transacción ------------------------------------------------------------------


async def test_ninguna_llamada_externa_ocurre_con_una_transaccion_abierta():
    f = fila(TENANT)
    m = _mundo((f, [telegram(), email()]))
    proc, tg, correo = _procesador(
        m, Telegram({CHAT: 500}, mundo=m), CorreoSimulado({EMAIL_CLIENTE: error_email(503)}, mundo=m)
    )
    await _procesar(proc, f)
    assert len(m.http_con_tx) == len(tg.pedidos) + len(correo.enviados) > 2
    assert set(m.http_con_tx) == {0}
    assert m.tx_abiertas == 0


# --- Tope de destinatarios ----------------------------------------------------------------------------------------


def _formato_jsonb(entregas):
    """El texto que da Postgres para un jsonb: claves ordenadas por largo y luego por contenido, `", "` y `": "`."""
    partes = []
    for clave in sorted(entregas, key=lambda k: (len(k), k)):
        v = entregas[clave]
        interior = ", ".join(f'"{k}": {json.dumps(v[k])}' for k in sorted(v, key=lambda k: (len(k), k)))
        partes.append(f'"{clave}": {{{interior}}}')
    return "{" + ", ".join(partes) + "}"


def test_las_entregas_de_la_cantidad_maxima_de_destinatarios_caben_en_el_limite_de_la_tabla():
    from enviador_app.procesador import MAX_CODIGO_ERROR

    peor = {
        f"tu:{uuid.uuid4()}:email": {
            "estado": "failed",
            "error": "x" * MAX_CODIGO_ERROR,
            "at": "2026-11-28T13:12:43.431624+00:00",
        }
        for _ in range(MAX_DESTINATARIOS_POR_FILA)
    }
    assert len(_formato_jsonb(peor)) <= 16000 - 1000  # el CHECK de `notification_outbox.entregas` y un margen


async def test_con_mas_destinatarios_que_el_tope_solo_se_envia_hasta_el_tope_y_se_reporta():
    f = fila(TENANT, "aviso_negocio_reserva", negocio=True)
    exceso = MAX_DESTINATARIOS_POR_FILA + 20
    m = _mundo(
        (
            f,
            [
                destino("email", f"p{i}@negocio.test", clave=f"tu:{i:03d}:email", destino_fila="negocio")
                for i in range(exceso)
            ],
        )
    )
    reportes = Reportes()
    proc, _, correo = _procesador(m, reportes=reportes)
    salida = await _procesar(proc, f)
    assert len(correo.enviados) == MAX_DESTINATARIOS_POR_FILA and salida.tipo == "enviada"
    assert [e[1]["motivo"] for e in reportes.items] == ["destinatarios_excedidos"]
    assert "negocio.test" not in json.dumps([(d, {k: str(v) for k, v in e.items()}) for d, e in reportes.items])


def test_el_codigo_corto_de_error_no_pasa_del_maximo_de_la_tabla():
    from platform_notifications import ErrorEnvio

    from enviador_app.procesador import MAX_CODIGO_ERROR, _codigo_corto

    assert len(_codigo_corto(ErrorEnvio("x", "y", estado_http=503))) <= MAX_CODIGO_ERROR
    assert _codigo_corto(None) == "interno" and _codigo_corto(ErrorEnvio("red", "x")) == "red"
