"""Limitador de intentos en memoria con reloj inyectado."""

import pytest

from proteccion_login import LimitadorIntentos, limites_desde_entorno


class Reloj:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_rechaza_el_intento_n_mas_uno_con_espera_restante():
    reloj = Reloj()
    lim = LimitadorIntentos(limite=3, ventana_seg=60, reloj=reloj)
    for _ in range(3):
        assert lim.intentar("a").permitido
        reloj.t += 10
    r = lim.intentar("a")
    assert not r.permitido
    # el primer intento fue en t=1000; ahora es 1030: faltan 30 s para que salga de la ventana
    assert r.espera_seg == pytest.approx(30)


def test_otra_clave_no_se_afecta():
    lim = LimitadorIntentos(limite=1, ventana_seg=60, reloj=Reloj())
    assert lim.intentar("a").permitido
    assert not lim.intentar("a").permitido
    assert lim.intentar("b").permitido


def test_pasada_la_ventana_vuelve_a_permitir():
    reloj = Reloj()
    lim = LimitadorIntentos(limite=1, ventana_seg=60, reloj=reloj)
    assert lim.intentar("a").permitido
    assert not lim.intentar("a").permitido
    reloj.t += 60.1
    assert lim.intentar("a").permitido


def test_ventana_deslizante_libera_de_a_un_intento():
    reloj = Reloj()
    lim = LimitadorIntentos(limite=2, ventana_seg=60, reloj=reloj)
    assert lim.intentar("a").permitido
    reloj.t += 40
    assert lim.intentar("a").permitido
    reloj.t += 30  # el primero ya salió; el segundo todavía no
    assert lim.intentar("a").permitido
    assert not lim.intentar("a").permitido


def test_tope_de_claves_en_memoria():
    lim = LimitadorIntentos(limite=5, ventana_seg=60, max_claves=3, reloj=Reloj())
    for i in range(10):
        lim.intentar(f"k{i}")
    assert lim.cantidad_claves() <= 3


def test_limites_desde_entorno_con_defaults_y_override(monkeypatch):
    for v in ("LOGIN_LIMITE_IP", "LOGIN_LIMITE_EMAIL", "LOGIN_VENTANA_SEG"):
        monkeypatch.delenv(v, raising=False)
    cfg = limites_desde_entorno()
    assert cfg.por_ip == 20 and cfg.por_email == 5 and cfg.ventana_seg == 900
    monkeypatch.setenv("LOGIN_LIMITE_IP", "7")
    monkeypatch.setenv("LOGIN_LIMITE_EMAIL", "2")
    monkeypatch.setenv("LOGIN_VENTANA_SEG", "60")
    cfg = limites_desde_entorno()
    assert (cfg.por_ip, cfg.por_email, cfg.ventana_seg) == (7, 2, 60)


def test_valor_invalido_en_entorno_usa_el_default(monkeypatch):
    monkeypatch.setenv("LOGIN_LIMITE_IP", "abc")
    monkeypatch.setenv("LOGIN_LIMITE_EMAIL", "0")
    cfg = limites_desde_entorno()
    assert cfg.por_ip == 20 and cfg.por_email == 5
