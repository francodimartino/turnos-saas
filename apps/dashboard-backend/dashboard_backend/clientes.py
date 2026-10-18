"""Lógica y SQL de la gestión de `end_customers` desde el dashboard (ARCHITECTURE.md §2.3, §2.8, §3.2, §3.5).

Todas las queries filtran `tenant_id = $1` de forma explícita (la RLS es la segunda barrera, no la única) y el
tenant sale siempre de la sesión verificada. `dashboard_service` no tiene DELETE sobre `end_customers`: borrar es
`anonymize_end_customer`.
"""

import json
from typing import Any

from dashboard_backend.valores_json import contiene_no_finito

# Clave de `tenants.settings` donde el propio tenant declara los campos accesorios que acepta `extra_data`
# (§2.11: "Configuración de campos accesorios de end_customers"). Es una lista de claves (texto) o de objetos
# `{"key": "...", "label": "..."}`. Sin configuración, `extra_data` solo puede ser `{}`.
CAMPOS_EXTRA_SETTING = "end_customer_extra_fields"

MAX_LARGO_VALOR_EXTRA = 500

_COLUMNAS = "id, tenant_id, channel, phone, name, extra_data, deleted_at, created_at"

SQL_SETTINGS = "select settings from public.tenants where id = $1"

SQL_DETALLE = f"select {_COLUMNAS} from public.end_customers where tenant_id = $1 and id = $2"

SQL_EXISTE = "select 1 from public.end_customers where tenant_id = $1 and id = $2"

# Serializa las altas/ediciones del mismo teléfono dentro del tenant: sin esto dos requests simultáneos con el
# mismo número pasarían ambos el chequeo de duplicado (no hay unique sobre `phone`, §2.8).
SQL_BLOQUEO_ALTA = "select pg_advisory_xact_lock(hashtextextended($1::uuid::text || ':' || $2::text, 0))"

# Dedup de §2.8: mismo phone entre los clientes no anonimizados del tenant.
SQL_DUPLICADO = """
select id from public.end_customers
where tenant_id = $1 and phone = $2 and deleted_at is null and ($3::uuid is null or id <> $3::uuid)
limit 1
"""

SQL_INSERTAR = f"""
insert into public.end_customers (tenant_id, phone, name, extra_data)
values ($1, $2, $3, $4::jsonb)
returning {_COLUMNAS}
"""

# $3/$5/$7 indican qué campos vinieron; el resto no se toca. Solo filas no anonimizadas.
SQL_ACTUALIZAR = f"""
update public.end_customers
set phone = case when $3::boolean then $4::text else phone end,
    name = case when $5::boolean then $6::text else name end,
    extra_data = case when $7::boolean then $8::jsonb else extra_data end
where tenant_id = $1 and id = $2 and deleted_at is null
returning {_COLUMNAS}
"""

SQL_ANONIMIZAR = "select public.anonymize_end_customer($1)"

SQL_TARGETS = "select integration_id, google_calendar_event_id from public.calendar_scrub_targets($1, $2)"


class ErrorExtraData(ValueError):
    """`extra_data` no cumple la configuración del tenant. El mensaje nombra claves, nunca valores."""


def _claves_configuradas(campos: Any) -> set[str]:
    if not isinstance(campos, list):
        return set()
    claves: set[str] = set()
    for campo in campos:
        if isinstance(campo, str) and campo.strip():
            claves.add(campo)
        elif isinstance(campo, dict) and isinstance(campo.get("key"), str) and campo["key"].strip():
            claves.add(campo["key"])
    return claves


def validar_extra_data(extra: dict[str, Any], campos_configurados: Any) -> dict[str, Any]:
    """Valida `extra` contra los campos que configuró el tenant; devuelve el mismo dict o levanta `ErrorExtraData`."""
    if not extra:
        return {}
    permitidas = _claves_configuradas(campos_configurados)
    desconocidas = sorted(k for k in extra if k not in permitidas)
    if desconocidas:
        raise ErrorExtraData("campos no configurados por el negocio: " + ", ".join(desconocidas[:10]))
    for clave, valor in extra.items():
        if isinstance(valor, str):
            if len(valor) > MAX_LARGO_VALOR_EXTRA:
                raise ErrorExtraData(f"el valor de '{clave}' supera {MAX_LARGO_VALOR_EXTRA} caracteres")
        elif valor is not None and not isinstance(valor, bool | int | float):
            raise ErrorExtraData(f"el valor de '{clave}' debe ser texto, número, booleano o nulo")
        elif contiene_no_finito(valor):
            raise ErrorExtraData(f"el valor de '{clave}' debe ser un número finito")
    return extra


def a_json(valor: Any) -> Any:
    """asyncpg devuelve `jsonb` como texto salvo que haya un codec: se acepta ambas formas."""
    return json.loads(valor) if isinstance(valor, str) else valor


def presentar(fila: Any) -> dict[str, Any]:
    """Fila de `end_customers` -> respuesta. `sin_cliente_identificable` se deriva de `deleted_at` (§3.5).

    El listado no trae `extra_data` (la consulta compartida no lo selecciona): en ese caso la clave se omite.
    """
    d = dict(fila)
    salida = {
        "id": str(d["id"]),
        "channel": d.get("channel"),
        "phone": d.get("phone"),
        "name": d.get("name"),
        "deleted_at": d.get("deleted_at"),
        "created_at": d.get("created_at"),
        "sin_cliente_identificable": d.get("deleted_at") is not None,
    }
    if "extra_data" in d:
        salida["extra_data"] = a_json(d["extra_data"])
    return salida
