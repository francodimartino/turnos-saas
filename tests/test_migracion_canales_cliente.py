"""Chequeos estáticos de la migración de canales del cliente.

- La sentencia de copia de la prueba pgTAP es idéntica a la de la migración (si una cambia, falla este test).
- La anonimización y la purga borran los canales (el identificador de Telegram no puede sobrevivir al olvido).
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACION = RAIZ / "supabase" / "migrations" / "20260103003400_canales_cliente.sql"
MIGRACION_ANONIMIZAR = RAIZ / "supabase" / "migrations" / "20260103003500_anonimizar_canales_cliente.sql"
PRUEBA_PGTAP = RAIZ / "supabase" / "tests" / "turnos_70_canales_cliente.sql"


def sentencia_de_copia(texto: str) -> str:
    """Extrae el `insert into public.canales_cliente ... select ...` y lo normaliza (espacios y mayúsculas)."""
    sin_comentarios = "\n".join(x for x in texto.splitlines() if not x.lstrip().startswith("--"))
    m = re.search(r"insert into public\.canales_cliente \(.*?select.*?(?=;|\$\$)", sin_comentarios, re.S | re.I)
    assert m, "no se encontró la sentencia de copia"
    return re.sub(r"\s+", " ", m.group(0)).strip().lower()


def test_la_copia_de_la_prueba_pgtap_es_la_de_la_migracion():
    migracion = sentencia_de_copia(MIGRACION.read_text(encoding="utf-8"))
    prueba = sentencia_de_copia(PRUEBA_PGTAP.read_text(encoding="utf-8"))
    assert prueba == migracion


def test_anonimizar_y_purgar_borran_los_canales():
    sql = re.sub(r"\s+", " ", MIGRACION_ANONIMIZAR.read_text(encoding="utf-8").lower())
    assert "delete from public.canales_cliente cc where cc.end_customer_id = p_id and cc.tenant_id = v_tenant_id" in sql
    assert "delete from public.canales_cliente cc where cc.tenant_id = p_tenant_id" in sql
