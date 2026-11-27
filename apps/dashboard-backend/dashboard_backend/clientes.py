"""Lógica y SQL de la gestión de `end_customers` desde el dashboard (ARCHITECTURE.md §2.3, §2.8, §3.2, §3.5).

Todas las queries filtran `tenant_id = $1` de forma explícita (la RLS es la segunda barrera, no la única) y el
tenant sale siempre de la sesión verificada. `dashboard_service` no tiene DELETE sobre `end_customers`: borrar es
`anonymize_end_customer`.
"""

import json
import re
from typing import Any

from turnos_dominio.valores_json import contiene_no_finito

# Clave de `tenants.settings` donde el propio tenant declara los campos accesorios que acepta `extra_data`
# (§2.11: "Configuración de campos accesorios de end_customers"). Es una lista de claves (texto) o de objetos
# `{"key": "...", "label": "..."}`. Sin configuración, `extra_data` solo puede ser `{}`.
CAMPOS_EXTRA_SETTING = "end_customer_extra_fields"

MAX_LARGO_VALOR_EXTRA = 500

MAX_LARGO_EMAIL = 254
MIN_LARGO_EMAIL = 3

SQL_SAVEPOINT_ESCRITURA = "savepoint escritura_cliente"
SQL_VOLVER_AL_SAVEPOINT = "rollback to savepoint escritura_cliente"
SQL_LIBERAR_SAVEPOINT = "release savepoint escritura_cliente"

# Misma forma que el check de la base (`end_customers_email_formato`), más solo ASCII: `lower` de Python y el de la
# base pueden diferir en caracteres no ASCII y romper la unicidad por minúsculas.
_FORMA_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

_COLUMNAS = "id, tenant_id, channel, phone, name, email, extra_data, deleted_at, created_at"

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

# Misma unicidad que el índice de la base: por tenant y sin distinguir mayúsculas, incluidas las fichas anonimizadas.
SQL_EMAIL_DUPLICADO = """
select id from public.end_customers
where tenant_id = $1 and lower(email) = $2 and ($3::uuid is null or id <> $3::uuid)
limit 1
"""

SQL_INSERTAR = f"""
insert into public.end_customers (tenant_id, phone, name, extra_data, email)
values ($1, $2, $3, $4::jsonb, $5)
returning {_COLUMNAS}
"""

# $3/$5/$7/$9 indican qué campos vinieron; el resto no se toca. Solo filas no anonimizadas.
SQL_ACTUALIZAR = f"""
update public.end_customers
set phone = case when $3::boolean then $4::text else phone end,
    name = case when $5::boolean then $6::text else name end,
    extra_data = case when $7::boolean then $8::jsonb else extra_data end,
    email = case when $9::boolean then $10::text else email end
where tenant_id = $1 and id = $2 and deleted_at is null
returning {_COLUMNAS}
"""

# El listado compartido no trae el email: se pide aparte para las fichas de la página.
SQL_EMAILS = "select id, email from public.end_customers where tenant_id = $1 and id = any($2::uuid[])"

# Solo el tipo de canal y si está verificado: el identificador (id de Telegram, teléfono de WhatsApp) nunca sale.
SQL_CANALES = """
select end_customer_id, canal, verificado_at is not null as verificado
from public.canales_cliente
where tenant_id = $1 and end_customer_id = any($2::uuid[])
order by created_at, canal
"""

SQL_ANONIMIZAR = "select public.anonymize_end_customer($1)"

# Unir dos fichas ($1 = la que queda, $2 = la que se absorbe). Todo ocurre dentro de la función de la base: bloquea
# las dos filas, mueve turnos, packs, canales y avisos, anonimiza la absorbida y deja la constancia en `audit_log`.
SQL_UNIR = "select public.unir_end_customers($1, $2)"

# SQLSTATE propios de `unir_end_customers`.
UNION_FICHA_INEXISTENTE = "UF001"
UNION_FICHA_ANONIMIZADA = "UF002"
UNION_CANAL_EN_CONFLICTO = "UF003"
UNION_MISMA_FICHA = "UF004"

SQL_TARGETS = "select integration_id, google_calendar_event_id from public.calendar_scrub_targets($1, $2)"


class ErrorEmailInvalido(ValueError):
    """El email no tiene forma de email. El mensaje no incluye el valor."""


def normalizar_email(valor: str) -> str:
    """Recorta y pasa a minúsculas; levanta `ErrorEmailInvalido` si no es un email aceptable.

    Rechaza largo fuera de rango, forma inválida, no ASCII y caracteres de control (NUL incluido: Postgres no los
    admite en `text` y terminaría en un error de base).
    """
    email = valor.strip().lower()
    if (
        not email.isascii()
        or any(ord(car) < 32 or ord(car) == 127 for car in email)
        or not MIN_LARGO_EMAIL <= len(email) <= MAX_LARGO_EMAIL
        or _FORMA_EMAIL.fullmatch(email) is None
    ):
        raise ErrorEmailInvalido("El email no es válido")
    return email


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


def presentar(fila: Any, canales: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Fila de `end_customers` -> respuesta. `sin_cliente_identificable` se deriva de `deleted_at` (§3.5).

    El listado no trae `extra_data` (la consulta compartida no lo selecciona): en ese caso la clave se omite.
    `canales` son los de `canales_cliente` ya reducidos a tipo y verificación (`SQL_CANALES`).
    """
    d = dict(fila)
    salida = {
        "id": str(d["id"]),
        "channel": d.get("channel"),
        "phone": d.get("phone"),
        "name": d.get("name"),
        "email": d.get("email"),
        "canales": canales or [],
        "deleted_at": d.get("deleted_at"),
        "created_at": d.get("created_at"),
        "sin_cliente_identificable": d.get("deleted_at") is not None,
    }
    if "extra_data" in d:
        salida["extra_data"] = a_json(d["extra_data"])
    return salida
