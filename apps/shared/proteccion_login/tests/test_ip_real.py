"""ip_real: solo confía en un header si la variable de entorno lo configura."""

from types import SimpleNamespace

from proteccion_login import ip_real


def req(headers=None, host="10.0.0.9"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


def test_sin_variable_usa_client_host_e_ignora_headers_falsificados(monkeypatch):
    monkeypatch.delenv("LOGIN_IP_HEADER", raising=False)
    r = req({"cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"})
    assert ip_real(r) == "10.0.0.9"


def test_cf_connecting_ip(monkeypatch):
    monkeypatch.setenv("LOGIN_IP_HEADER", "CF-Connecting-IP")
    assert ip_real(req({"cf-connecting-ip": "1.2.3.4"})) == "1.2.3.4"


def test_x_forwarded_for_toma_el_salto_confiable(monkeypatch):
    monkeypatch.setenv("LOGIN_IP_HEADER", "X-Forwarded-For")
    monkeypatch.delenv("LOGIN_PROXIES_CONFIABLES", raising=False)
    # el cliente puede anteponer IPs falsas; el proxy confiable agrega la real al final
    assert ip_real(req({"x-forwarded-for": "9.9.9.9, 1.2.3.4"})) == "1.2.3.4"
    monkeypatch.setenv("LOGIN_PROXIES_CONFIABLES", "2")
    assert ip_real(req({"x-forwarded-for": "9.9.9.9, 1.2.3.4, 172.16.0.1"})) == "1.2.3.4"


def test_header_configurado_ausente_o_invalido_cae_a_client_host(monkeypatch):
    monkeypatch.setenv("LOGIN_IP_HEADER", "CF-Connecting-IP")
    assert ip_real(req({})) == "10.0.0.9"
    assert ip_real(req({"cf-connecting-ip": "no-es-ip"})) == "10.0.0.9"


def test_header_no_soportado_se_ignora(monkeypatch):
    monkeypatch.setenv("LOGIN_IP_HEADER", "X-Custom")
    assert ip_real(req({"x-custom": "1.2.3.4"})) == "10.0.0.9"


def test_sin_client(monkeypatch):
    monkeypatch.delenv("LOGIN_IP_HEADER", raising=False)
    assert ip_real(SimpleNamespace(headers={}, client=None)) == "desconocida"
