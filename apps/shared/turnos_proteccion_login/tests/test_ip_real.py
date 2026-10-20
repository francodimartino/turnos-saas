"""ip_real: solo confía en un header si la configuración inyectada lo pide; no lee el entorno del proceso."""

from types import SimpleNamespace

from turnos_proteccion_login import ConfigIp, ip_real


def req(headers=None, host="10.0.0.9"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


def test_sin_header_configurado_usa_client_host_e_ignora_headers_falsificados():
    r = req({"cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"})
    assert ip_real(r) == "10.0.0.9"
    assert ip_real(r, ConfigIp()) == "10.0.0.9"


def test_cf_connecting_ip():
    assert ip_real(req({"cf-connecting-ip": "1.2.3.4"}), ConfigIp(header="CF-Connecting-IP")) == "1.2.3.4"


def test_x_forwarded_for_toma_el_salto_confiable():
    # el cliente puede anteponer IPs falsas; el proxy confiable agrega la real al final
    uno = ConfigIp(header="X-Forwarded-For")
    assert ip_real(req({"x-forwarded-for": "9.9.9.9, 1.2.3.4"}), uno) == "1.2.3.4"
    dos = ConfigIp(header="X-Forwarded-For", proxies_confiables=2)
    assert ip_real(req({"x-forwarded-for": "9.9.9.9, 1.2.3.4, 172.16.0.1"}), dos) == "1.2.3.4"
    # menos saltos que proxies confiables: no se puede confiar en la cadena
    assert ip_real(req({"x-forwarded-for": "1.2.3.4"}), dos) == "10.0.0.9"


def test_header_configurado_ausente_o_invalido_cae_a_client_host():
    cfg = ConfigIp(header="CF-Connecting-IP")
    assert ip_real(req({}), cfg) == "10.0.0.9"
    assert ip_real(req({"cf-connecting-ip": "no-es-ip"}), cfg) == "10.0.0.9"


def test_header_no_soportado_se_ignora():
    assert ip_real(req({"x-custom": "1.2.3.4"}), ConfigIp(header="X-Custom")) == "10.0.0.9"


def test_sin_client():
    assert ip_real(SimpleNamespace(headers={}, client=None)) == "desconocida"


def test_el_entorno_del_proceso_no_tiene_efecto(monkeypatch):
    monkeypatch.setenv("LOGIN_IP_HEADER", "CF-Connecting-IP")
    monkeypatch.setenv("LOGIN_PROXIES_CONFIABLES", "3")
    assert ip_real(req({"cf-connecting-ip": "1.2.3.4"})) == "10.0.0.9"
    cfg = ConfigIp(header="X-Forwarded-For")
    assert ip_real(req({"x-forwarded-for": "9.9.9.9, 1.2.3.4"}), cfg) == "1.2.3.4"
