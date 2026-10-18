"""Traducción HTTP de OwnerNoDisponible: 409 estable y mensaje neutro (sin negocio ni email)."""

from platform_admin import OwnerNoDisponible

from admin_backend.errores import http_desde_error_dominio


def test_owner_no_disponible_es_409_con_codigo_estable_y_mensaje_neutro():
    h = http_desde_error_dominio(OwnerNoDisponible())
    assert h.status_code == 409
    assert h.detail["codigo"] == "owner_no_disponible"
    mensaje = h.detail["mensaje"].lower()
    for palabra in ("negocio", "tenant", "pendiente", "vinculada", "@"):
        assert palabra not in mensaje
