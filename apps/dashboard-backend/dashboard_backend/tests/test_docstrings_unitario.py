"""Los docstrings de módulo describen lo que el código hace de verdad (permisos de clientes, clientes de Auth Admin)."""

import re
from pathlib import Path

from dashboard_backend import rutas_clientes

RAIZ = Path(__file__).resolve().parents[4]
AUTH_ADMIN = RAIZ / "platform" / "packages" / "platform_admin" / "platform_admin" / "auth_admin.py"
INVITACIONES = RAIZ / "platform" / "packages" / "platform_auth" / "platform_auth" / "invitaciones.py"


def test_docstring_de_clientes_cita_las_claves_de_permiso_reales():
    doc = rutas_clientes.__doc__ or ""
    citadas = set(re.findall(r"`([a-z]+_[a-z_]+)`", doc))
    reales = {rutas_clientes.PERMISO_VER, rutas_clientes.PERMISO_GESTIONAR, rutas_clientes.PERMISO_ANONIMIZAR}
    assert reales <= citadas
    # Toda clave de permiso citada (forma verbo_sustantivo) es una constante del módulo.
    assert {c for c in citadas if c.split("_")[0] in {"view", "manage", "delete", "approve", "edit"}} == reales
    assert "no incluye uno específico" not in doc


def test_docstring_de_auth_admin_nombra_los_dos_clientes_y_la_deuda():
    doc = AUTH_ADMIN.read_text(encoding="utf-8").split('"""')[1]
    assert "el único lugar" not in doc
    assert "ClienteAuthAdminHttp" in doc
    assert "SupabaseAuthAdmin" in doc
    assert "platform_auth/invitaciones.py" in doc
    assert "deuda" in doc


def test_docstring_de_invitaciones_referencia_al_otro_cliente():
    texto = INVITACIONES.read_text(encoding="utf-8")
    doc = texto[texto.index("class SupabaseAuthAdmin") :].split('"""')[1]
    assert "ClienteAuthAdminHttp" in doc
    assert "platform_admin/auth_admin.py" in doc
