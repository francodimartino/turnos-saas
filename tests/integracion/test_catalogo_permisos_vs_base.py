"""El catálogo de permisos de Python coincide con la tabla `permissions` de la base migrada, en ambos sentidos."""

import pytest
from platform_auth import Permiso

pytestmark = pytest.mark.integracion


async def test_el_catalogo_de_python_coincide_con_la_tabla_permissions(db_superuser):
    filas = await db_superuser.fetch("select key from public.permissions")
    en_base = {f["key"] for f in filas}
    en_python = {p.value for p in Permiso}
    assert en_python - en_base == set(), f"en Python y no en la base: {sorted(en_python - en_base)}"
    assert en_base - en_python == set(), f"en la base y no en Python: {sorted(en_base - en_python)}"
