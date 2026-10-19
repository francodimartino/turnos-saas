"""Los docstrings de módulo describen lo que el código hace de verdad (permisos de clientes, clientes de Auth Admin)."""

import re
from pathlib import Path

from dashboard_backend import rutas_clientes

RAIZ = Path(__file__).resolve().parents[4]
AUTH_ADMIN = RAIZ / "platform" / "packages" / "platform_auth" / "platform_auth" / "auth_admin.py"
AUTH_ADMIN_ADMIN = RAIZ / "platform" / "packages" / "platform_admin" / "platform_admin" / "auth_admin.py"


def test_docstring_de_clientes_cita_las_claves_de_permiso_reales():
    doc = rutas_clientes.__doc__ or ""
    citadas = set(re.findall(r"`([a-z]+_[a-z_]+)`", doc))
    reales = {rutas_clientes.PERMISO_VER, rutas_clientes.PERMISO_GESTIONAR, rutas_clientes.PERMISO_ANONIMIZAR}
    assert reales <= citadas
    # Toda clave de permiso citada (forma verbo_sustantivo) es una constante del módulo.
    assert {c for c in citadas if c.split("_")[0] in {"view", "manage", "delete", "approve", "edit"}} == reales
    assert "no incluye uno específico" not in doc


def test_docstring_de_auth_admin_declara_al_unico_cliente():
    doc = AUTH_ADMIN.read_text(encoding="utf-8").split('"""')[1]
    assert "el único lugar" in doc
    assert "SupabaseAuthAdmin" in doc
    assert "deuda" not in doc


def test_docstring_de_las_interfaces_de_platform_admin_apunta_al_unico_cliente():
    doc = AUTH_ADMIN_ADMIN.read_text(encoding="utf-8").split('"""')[1]
    assert "platform_auth.SupabaseAuthAdmin" in doc
    assert "ClienteAuthAdminHttp" not in doc
