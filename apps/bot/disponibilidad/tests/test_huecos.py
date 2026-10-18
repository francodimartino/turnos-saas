"""Lógica pura del cálculo de huecos (ARCHITECTURE.md §7.1): sin base de datos.

Semana de referencia: lunes 2026-10-05 (día 1 en `availability_rules`, 0 = domingo).
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from disponibilidad.huecos import ReglaSemanal, Ventana, _fechas, _restar, huecos_libres

BA = ZoneInfo("America/Argentina/Buenos_Aires")
LUNES = 5  # 2026-10-05


def dt(hora: int, minuto: int = 0, dia: int = LUNES) -> datetime:
    return datetime(2026, 10, dia, hora, minuto, tzinfo=BA)


def v(h1: int, m1: int, h2: int, m2: int, dia: int = LUNES) -> Ventana:
    return Ventana(dt(h1, m1, dia), dt(h2, m2, dia))


def lunes(h1: int, h2: int) -> ReglaSemanal:
    return ReglaSemanal(dia_semana=1, hora_inicio=time(h1), hora_fin=time(h2))


def inicios(huecos: list[Ventana]) -> list[tuple[int, int]]:
    return [(h.inicio.astimezone(BA).hour, h.inicio.astimezone(BA).minute) for h in huecos]


DIA = Ventana(dt(0), dt(0, dia=LUNES + 1))


def calcular(reglas, ocupados=(), duracion=30, rango=DIA, paso=30):
    return huecos_libres(reglas, list(ocupados), duracion, rango, paso_minutos=paso)


def test_sin_reglas_no_hay_huecos():
    assert calcular([]) == []


def test_regla_de_otro_dia_no_aplica():
    martes = ReglaSemanal(dia_semana=2, hora_inicio=time(9), hora_fin=time(12))
    assert calcular([martes]) == []


def test_domingo_es_dia_cero():
    domingo = ReglaSemanal(dia_semana=0, hora_inicio=time(9), hora_fin=time(10))
    rango = Ventana(dt(0, dia=4), dt(0, dia=5))  # domingo 2026-10-04
    assert inicios(calcular([domingo], rango=rango)) == [(9, 0), (9, 30)]


def test_ventana_libre_se_parte_en_huecos_del_tamano_del_servicio():
    huecos = calcular([lunes(9, 11)])
    assert inicios(huecos) == [(9, 0), (9, 30), (10, 0), (10, 30)]
    assert all(h.fin - h.inicio == timedelta(minutes=30) for h in huecos)


def test_los_huecos_no_cruzan_el_fin_de_la_ventana():
    # 9:00-10:15 con servicio de 30: el último hueco válido arranca 9:30 (termina 10:00); 10:00 terminaría 10:30.
    huecos = calcular([ReglaSemanal(1, time(9), time(10, 15))])
    assert inicios(huecos) == [(9, 0), (9, 30)]
    assert max(h.fin for h in huecos) <= dt(10, 15)


def test_hueco_que_termina_justo_al_cierre_es_valido():
    assert inicios(calcular([lunes(9, 10)])) == [(9, 0), (9, 30)]


def test_servicio_de_45_no_entra_en_un_hueco_de_30():
    assert calcular([lunes(9, 10)], ocupados=[v(9, 30, 10, 0)], duracion=45, paso=15) == []
    # y entre dos turnos con 30 libres tampoco
    assert calcular([lunes(9, 12)], ocupados=[v(9, 0, 10, 0), v(10, 30, 12, 0)], duracion=45, paso=15) == []


def test_servicio_mas_largo_que_la_ventana_no_da_huecos():
    assert calcular([lunes(9, 10)], duracion=90) == []


def test_booking_existente_recorta_la_ventana():
    huecos = calcular([lunes(9, 12)], ocupados=[v(10, 0, 10, 30)])
    assert inicios(huecos) == [(9, 0), (9, 30), (10, 30), (11, 0), (11, 30)]


def test_turnos_contiguos_no_dejan_huecos_entre_si():
    ocupados = [v(9, 0, 9, 30), v(9, 30, 10, 0), v(10, 0, 10, 30)]
    assert inicios(calcular([lunes(9, 11)], ocupados=ocupados)) == [(10, 30)]


def test_hueco_pegado_a_un_booking_es_valido_sin_solaparse():
    # el booking ocupa [10:00, 10:30): el hueco de 9:30 a 10:00 es contiguo, no se superpone
    huecos = calcular([lunes(9, 11)], ocupados=[v(10, 0, 10, 30)])
    assert Ventana(dt(9, 30), dt(10, 0)) in huecos
    assert Ventana(dt(10, 30), dt(11, 0)) in huecos


def test_bloqueo_parcial_recorta_sin_eliminar_el_resto():
    # bloqueo 10:15-10:45 (no alineado a la grilla) elimina los huecos que lo tocan
    huecos = calcular([lunes(9, 12)], ocupados=[v(10, 15, 10, 45)], paso=15)
    assert (10, 0) not in inicios(huecos) and (10, 15) not in inicios(huecos) and (10, 30) not in inicios(huecos)
    assert (9, 30) in inicios(huecos) and (10, 45) in inicios(huecos)


def test_ocupados_superpuestos_entre_si_se_fusionan():
    # un block de local y un booking que se pisan: sigue siendo una sola zona ocupada
    huecos = calcular([lunes(9, 12)], ocupados=[v(9, 30, 10, 30), v(10, 0, 11, 0)])
    assert inicios(huecos) == [(9, 0), (11, 0), (11, 30)]


def test_ocupado_que_cubre_toda_la_ventana_no_deja_huecos():
    assert calcular([lunes(9, 12)], ocupados=[v(8, 0, 13, 0)]) == []


def test_ocupado_fuera_de_la_ventana_no_afecta():
    assert inicios(calcular([lunes(9, 10)], ocupados=[v(12, 0, 13, 0), v(7, 0, 9, 0)])) == [(9, 0), (9, 30)]


def test_reglas_multiples_el_mismo_dia_turno_cortado():
    reglas = [lunes(9, 10), lunes(15, 16)]
    assert inicios(calcular(reglas)) == [(9, 0), (9, 30), (15, 0), (15, 30)]


def test_reglas_solapadas_no_duplican_huecos():
    reglas = [lunes(9, 11), lunes(10, 12)]
    huecos = calcular(reglas)
    assert inicios(huecos) == [(9, 0), (9, 30), (10, 0), (10, 30), (11, 0), (11, 30)]
    assert len(set(huecos)) == len(huecos)


def test_reglas_contiguas_se_unen_y_permiten_hueco_a_caballo():
    # 9-10 y 10-11 son una sola ventana de 2 h: un servicio de 90 entra a las 9:00 y 9:30
    huecos = calcular([lunes(9, 10), lunes(10, 11)], duracion=90, paso=30)
    assert inicios(huecos) == [(9, 0), (9, 30)]


def test_rango_recorta_la_ventana_de_la_regla():
    rango = Ventana(dt(10, 0), dt(11, 0))
    assert inicios(calcular([lunes(9, 12)], rango=rango)) == [(10, 0), (10, 30)]


def test_rango_de_varios_dias_aplica_cada_regla_a_su_dia():
    reglas = [lunes(9, 10), ReglaSemanal(2, time(14), time(15))]
    rango = Ventana(dt(0), dt(0, dia=LUNES + 8))
    huecos = calcular(reglas, rango=rango)
    esperados = [dt(9), dt(9, 30), dt(14, dia=6), dt(14, 30, dia=6), dt(9, dia=12), dt(9, 30, dia=12)]
    assert [h.inicio for h in huecos] == esperados


def test_rango_vacio_o_invertido():
    assert calcular([lunes(9, 12)], rango=Ventana(dt(10), dt(10))) == []
    assert calcular([lunes(9, 12)], rango=Ventana(dt(11), dt(10))) == []


def test_paso_menor_que_la_duracion_ofrece_inicios_intermedios():
    assert inicios(calcular([lunes(9, 10)], paso=15)) == [(9, 0), (9, 15), (9, 30)]


def test_inicios_alineados_a_la_grilla_tras_un_ocupado_desalineado():
    huecos = calcular([lunes(9, 12)], ocupados=[v(9, 0, 9, 10)], paso=15)
    assert inicios(huecos)[0] == (9, 15)


def test_resultado_en_zona_buenos_aires():
    huecos = calcular([lunes(9, 10)])
    assert huecos[0].inicio.utcoffset() == timedelta(hours=-3)


def test_acepta_ocupados_en_utc():
    utc = ZoneInfo("UTC")
    ocupado = Ventana(dt(10).astimezone(utc), dt(10, 30).astimezone(utc))
    assert inicios(calcular([lunes(9, 11)], ocupados=[ocupado])) == [(9, 0), (9, 30), (10, 30)]


def test_cambio_de_hora_usa_la_zona_y_no_un_offset_fijo():
    # Argentina no tiene DST, pero el cálculo usa zoneinfo: el 2025-03-09 en Nueva York (adelanta una hora a
    # las 2:00) la ventana 0:00-6:00 dura 5 horas reales, no 6.
    ny = ZoneInfo("America/New_York")
    regla = ReglaSemanal(dia_semana=0, hora_inicio=time(0), hora_fin=time(6))  # domingo
    rango = Ventana(datetime(2025, 3, 9, 0, tzinfo=ny), datetime(2025, 3, 10, 0, tzinfo=ny))
    huecos = huecos_libres([regla], [], 60, rango, paso_minutos=60, zona=ny)
    assert len(huecos) == 5


def test_rango_sin_zona_se_rechaza():
    with pytest.raises(ValueError):
        calcular([lunes(9, 12)], rango=Ventana(datetime(2026, 10, 5, 0), datetime(2026, 10, 6, 0)))


def test_duracion_o_paso_invalidos_se_rechazan():
    with pytest.raises(ValueError):
        calcular([lunes(9, 12)], duracion=0)
    with pytest.raises(ValueError):
        calcular([lunes(9, 12)], paso=0)


def test_anticipacion_minima_justo_en_el_limite_se_ofrece_y_un_minuto_antes_no():
    reglas = [lunes(9, 12)]
    limite = dt(9, 0) + timedelta(minutes=30)
    huecos = huecos_libres(reglas, [], 30, DIA, paso_minutos=15, no_antes_de=limite)
    assert inicios(huecos)[0] == (9, 30)  # 9:30 es justo el límite: se ofrece; 9:15 queda afuera
    mas_tarde = huecos_libres(reglas, [], 30, DIA, paso_minutos=15, no_antes_de=limite + timedelta(minutes=1))
    assert inicios(mas_tarde)[0] == (9, 45)  # un minuto más de anticipación: el de 9:30 ya no se ofrece


def test_sin_anticipacion_los_huecos_no_cambian():
    reglas = [lunes(9, 10)]
    assert huecos_libres(reglas, [], 30, DIA, paso_minutos=30) == huecos_libres(
        reglas, [], 30, DIA, paso_minutos=30, no_antes_de=None
    )


# --- Bordes de intervalos semiabiertos [inicio, fin) y rango de días (mutation testing) ---


def u(h: int, m: int = 0) -> datetime:
    """Instante de la tupla interna de `_restar`: hora local de BA el lunes de referencia."""
    return dt(h, m)


def test_restar_ocupado_que_termina_justo_al_inicio_no_solapa():
    assert _restar([(u(10), u(12))], [(u(9), u(10))]) == [(u(10), u(12))]


def test_restar_ocupado_que_empieza_justo_al_fin_no_solapa():
    assert _restar([(u(10), u(12))], [(u(12), u(13))]) == [(u(10), u(12))]


def test_restar_ocupado_pegado_al_inicio_deja_el_resto_sin_hueco_vacio():
    assert _restar([(u(10), u(12))], [(u(10), u(11))]) == [(u(11), u(12))]


def test_restar_ocupado_pegado_al_fin_deja_el_inicio():
    assert _restar([(u(10), u(12))], [(u(11), u(12))]) == [(u(10), u(11))]


def test_restar_ocupado_que_cubre_exacto_no_deja_nada():
    assert _restar([(u(10), u(12))], [(u(10), u(12))]) == []


def test_restar_un_minuto_de_solape_si_recorta():
    assert _restar([(u(10), u(12))], [(u(9), u(10, 1))]) == [(u(10, 1), u(12))]
    assert _restar([(u(10), u(12))], [(u(11, 59), u(13))]) == [(u(10), u(11, 59))]


def test_restar_varios_ocupados_dejan_los_tramos_intermedios():
    resultado = _restar([(u(9), u(13))], [(u(10), u(11)), (u(12), u(12, 30))])
    assert resultado == [(u(9), u(10)), (u(11), u(12)), (u(12, 30), u(13))]


def test_restar_sin_ocupados_devuelve_los_libres():
    assert _restar([(u(9), u(10)), (u(11), u(12))], []) == [(u(9), u(10)), (u(11), u(12))]


def test_hueco_de_exactamente_la_duracion_entra():
    huecos = calcular([lunes(9, 10)], duracion=60, paso=15)
    assert inicios(huecos) == [(9, 0)]


def test_hueco_un_minuto_menor_que_la_duracion_no_entra():
    ocupado = v(9, 59, 10, 0)  # deja 9:00-9:59 = 59 minutos
    assert calcular([lunes(9, 10)], [ocupado], duracion=60, paso=15) == []


def test_hueco_de_exactamente_la_duracion_entre_dos_ocupados_entra():
    ocupados = [v(9, 0, 10, 0), v(11, 0, 12, 0)]
    assert inicios(calcular([lunes(9, 12)], ocupados, duracion=60, paso=60)) == [(10, 0)]


def test_hueco_un_minuto_menor_entre_dos_ocupados_no_entra():
    ocupados = [v(9, 0, 10, 0), v(10, 59, 12, 0)]
    assert calcular([lunes(9, 12)], ocupados, duracion=60, paso=15) == []


def test_turno_que_termina_justo_cuando_empieza_otro_no_solapa():
    huecos = calcular([lunes(9, 11)], [v(9, 30, 10, 0)], duracion=30, paso=30)
    assert inicios(huecos) == [(9, 0), (10, 0), (10, 30)]
    assert huecos[0].fin == dt(9, 30)  # termina justo cuando empieza el ocupado


def test_hasta_igual_a_desde_devuelve_vacio():
    rango = Ventana(dt(10), dt(10))
    assert calcular([lunes(9, 12)], rango=rango) == []


def test_rango_de_un_minuto_dentro_de_la_ventana_con_duracion_mayor_es_vacio():
    assert calcular([lunes(9, 12)], rango=Ventana(dt(10), dt(10, 1))) == []


def test_regla_que_empieza_justo_donde_termina_el_rango_no_aporta():
    assert calcular([lunes(10, 12)], rango=Ventana(dt(8), dt(10))) == []


def test_regla_que_termina_justo_donde_empieza_el_rango_no_aporta():
    assert calcular([lunes(8, 10)], rango=Ventana(dt(10), dt(12))) == []


def test_regla_con_inicio_igual_a_fin_no_aporta():
    regla = ReglaSemanal(dia_semana=1, hora_inicio=time(10), hora_fin=time(10))
    assert calcular([regla]) == []


def test_ocupado_vacio_o_invertido_se_ignora():
    ocupados = [Ventana(dt(10), dt(10)), Ventana(dt(11), dt(10))]
    assert inicios(calcular([lunes(9, 11)], ocupados)) == [(9, 0), (9, 30), (10, 0), (10, 30)]


# Rango de días: `_fechas` devuelve exactamente los días locales del rango, ni uno de más ni de menos.


def test_fechas_mismo_dia_es_un_solo_dia():
    assert _fechas(dt(9), dt(18), BA) == [dt(0).date()]


def test_fechas_dias_consecutivos_son_dos_dias():
    assert _fechas(dt(9), dt(9, dia=LUNES + 1), BA) == [dt(0).date(), dt(0, dia=LUNES + 1).date()]


def test_fechas_tres_dias_son_exactamente_tres():
    fechas = _fechas(dt(9), dt(9, dia=LUNES + 2), BA)
    assert [f.day for f in fechas] == [LUNES, LUNES + 1, LUNES + 2]


def test_fechas_cruzando_medianoche_local_incluye_ambos_dias():
    # 23:00 locales (02:00Z del martes) a 01:00 locales del martes (04:00Z): en UTC es un día, en local son dos.
    fechas = _fechas(dt(23), dt(1, dia=LUNES + 1), BA)
    assert [f.day for f in fechas] == [LUNES, LUNES + 1]


def test_fechas_usa_la_zona_local_y_no_utc():
    # 22:00 locales del lunes = 01:00Z del martes: el día local sigue siendo el lunes.
    assert [f.day for f in _fechas(dt(21), dt(22), BA)] == [LUNES]


def test_huecos_cruzando_medianoche_toma_las_reglas_de_ambos_dias():
    reglas = [
        ReglaSemanal(dia_semana=1, hora_inicio=time(23), hora_fin=time(23, 59)),
        ReglaSemanal(dia_semana=2, hora_inicio=time(0), hora_fin=time(1)),
    ]
    rango = Ventana(dt(22), dt(2, dia=LUNES + 1))
    assert inicios(calcular(reglas, rango=rango)) == [(23, 0), (0, 0), (0, 30)]  # 23:30 + 30 min pasa de las 23:59


def test_huecos_rango_de_dias_consecutivos_no_agrega_un_dia_de_mas():
    reglas = [lunes(9, 10), ReglaSemanal(dia_semana=2, hora_inicio=time(9), hora_fin=time(10))]
    # El rango termina el martes a las 9:00 en punto: el martes no aporta nada.
    rango = Ventana(dt(0), dt(9, dia=LUNES + 1))
    assert inicios(calcular(reglas, rango=rango)) == [(9, 0), (9, 30)]
    # Si termina a las 9:30 aporta exactamente el primer turno del martes.
    rango = Ventana(dt(0), dt(9, 30, dia=LUNES + 1))
    assert len(calcular(reglas, rango=rango)) == 3


# Cantidad de horarios según duración y paso.


def test_duracion_igual_al_paso_cantidad_exacta():
    huecos = calcular([lunes(9, 11)], duracion=30, paso=30)
    assert len(huecos) == 4
    assert [h.inicio for h in huecos] == [dt(9), dt(9, 30), dt(10), dt(10, 30)]


def test_duracion_igual_al_paso_con_una_sola_ranura():
    assert inicios(calcular([lunes(9, 10)], duracion=60, paso=60)) == [(9, 0)]


def test_duracion_mayor_que_el_paso_cantidad_exacta():
    huecos = calcular([lunes(9, 11)], duracion=60, paso=30)
    assert inicios(huecos) == [(9, 0), (9, 30), (10, 0)]
    assert all(h.fin - h.inicio == timedelta(minutes=60) for h in huecos)


def test_duracion_mayor_que_el_paso_en_ventana_justa_da_uno():
    assert len(calcular([lunes(9, 10)], duracion=45, paso=30)) == 1


def test_duracion_menor_que_el_paso_deja_huecos_en_la_grilla():
    assert inicios(calcular([lunes(9, 11)], duracion=15, paso=30)) == [(9, 0), (9, 30), (10, 0), (10, 30)]


def test_no_antes_de_en_el_instante_exacto_conserva_el_hueco_y_un_minuto_despues_lo_descarta():
    reglas = [lunes(9, 11)]
    exacto = huecos_libres(reglas, [], 30, DIA, paso_minutos=30, no_antes_de=dt(10))
    assert inicios(exacto) == [(10, 0), (10, 30)]
    despues = huecos_libres(reglas, [], 30, DIA, paso_minutos=30, no_antes_de=dt(10, 1))
    assert inicios(despues) == [(10, 30)]
