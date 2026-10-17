"""Lógica pura del cálculo de huecos (ARCHITECTURE.md §7.1): sin base de datos.

Semana de referencia: lunes 2026-10-05 (día 1 en `availability_rules`, 0 = domingo).
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from disponibilidad.huecos import ReglaSemanal, Ventana, huecos_libres

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
