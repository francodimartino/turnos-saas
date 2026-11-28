"""Soporte común de los tests del web-backend: pool y conexión falsos y el cliente HTTP en proceso.

Los dobles devuelven lo mismo que la base real: las claves son los alias del SQL y donde la base da null, el doble
da `None`. La lógica de las consultas no se prueba contra el doble sino en `test_integracion`, contra Postgres.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from platform_tenancy.testing import TransaccionPorExecute

from web_backend.app import crear_app
from web_backend.config import Config

ORIGEN = "https://agendemos.ar"
BASE = "https://reservas-api.ejemplo.test"
UTC = timezone.utc  # noqa: UP017 - datetime.UTC no existe en Python 3.10

TENANT_A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
TENANT_B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
SERVICIO_1 = uuid.UUID("11111111-0000-4000-8000-000000000001")
SERVICIO_2 = uuid.UUID("11111111-0000-4000-8000-000000000002")
PROFESIONAL_1 = uuid.UUID("22222222-0000-4000-8000-000000000001")
PROFESIONAL_2 = uuid.UUID("22222222-0000-4000-8000-000000000002")
LOGO_A = f"{TENANT_A}/logo-{'a' * 32}.png"


def negocio_a() -> dict:
    """Fila de `SQL_NEGOCIO` de un negocio con todo cargado."""
    return {
        "nombre": "Estética Sol",
        "direccion": "Av. Siempreviva 742",
        "color_principal": "#112233",
        "color_secundario": "#AABBCC",
        "logo_ruta": LOGO_A,
    }


def negocio_vacio() -> dict:
    """Fila de `SQL_NEGOCIO` de un negocio sin settings de marca: la base da null en cada columna."""
    return {
        "nombre": "Barbería Luna",
        **dict.fromkeys(("direccion", "color_principal", "color_secundario", "logo_ruta")),
    }


def fila_catalogo(servicio, profesional, *, nombre_s="Corte", duracion=30, precio="1500", nombre_p="Ana") -> dict:
    return {
        "servicio_id": servicio,
        "servicio": nombre_s,
        "duration_minutes": duracion,
        "price": Decimal(precio),
        "profesional_id": profesional,
        "profesional": nombre_p,
    }


class ConnFalsa(TransaccionPorExecute):
    """Conexión asyncpg mínima: despacha por el SQL y registra lo ejecutado."""

    def __init__(self):
        self.ejecutado: list[tuple[str, tuple]] = []
        # slug -> fila de resolver_slug_tenant. La función solo devuelve tenants `active`: un negocio suspendido, dado
        # de baja o inexistente simplemente no está acá.
        self.slugs: dict[str, dict] = {
            "sol": {"tenant_id": TENANT_A, "nombre": "Estética Sol", "slug_vigente": "sol", "reemplazado": False},
            "sol-viejo": {"tenant_id": TENANT_A, "nombre": "Estética Sol", "slug_vigente": "sol", "reemplazado": True},
        }
        self.negocio: dict | None = negocio_a()
        self.catalogo: list[dict] = [fila_catalogo(SERVICIO_1, PROFESIONAL_1)]
        self.aviso_minimo: str | None = None  # `settings ->> 'booking_min_notice_minutes'`
        self.par_reservable = True
        self.error: BaseException | None = None  # se lanza en cualquier consulta (base caída, rol sin permiso...)

    async def execute(self, sql, *args):
        self.ejecutado.append((sql, args))

    def _revisar(self, sql, args):
        self.ejecutado.append((sql, args))
        if self.error is not None:
            raise self.error

    async def fetchrow(self, sql, *args):
        self._revisar(sql, args)
        if "resolver_slug_tenant" in sql:
            return self.slugs.get(args[0])
        if "booking_min_notice_minutes" in sql:
            return {"aviso_minimo": self.aviso_minimo}
        if "from public.tenants" in sql:
            return self.negocio
        if "from public.professional_services" in sql:
            return {"existe": 1} if self.par_reservable else None
        return None

    async def fetch(self, sql, *args):
        self._revisar(sql, args)
        if "from public.professional_services" in sql:
            return self.catalogo
        return []

    async def fetchval(self, sql, *args):
        self._revisar(sql, args)
        return 1

    def sentencias(self) -> list[str]:
        return [s for s, _ in self.ejecutado]


class PoolFalso:
    def __init__(self):
        self.conn = ConnFalsa()
        self.adquisiciones = 0
        self.error_al_adquirir: BaseException | None = None

    @asynccontextmanager
    async def acquire(self):
        self.adquisiciones += 1
        if self.error_al_adquirir is not None:
            raise self.error_al_adquirir
        yield self.conn


class AlmacenamientoFalso:
    """Imita a `ClienteAlmacenamiento.url_publica_o_firmada`: devuelve una URL firmada o falla como el real."""

    def __init__(self, error: BaseException | None = None):
        self.llamadas: list[tuple] = []
        self.error = error
        self.cerrado = False

    async def url_publica_o_firmada(self, tenant_id, ruta, *, expira_seg=3600):
        self.llamadas.append((tenant_id, ruta, expira_seg))
        if self.error is not None:
            raise self.error
        return f"https://storage.ejemplo.test/object/sign/logos/{ruta}?token=firmado"

    async def cerrar(self):
        self.cerrado = True


def config_prueba(**cambios) -> Config:
    base = {"database_url": "postgresql://no-se-usa@localhost/x", "web_origin": ORIGEN}
    base.update(cambios)
    return Config(**base)


AHORA = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # lunes 12:00 en Buenos Aires


def app_de_prueba(*, pool=None, config: Config | None = None, almacenamiento=None, reloj=None):
    pool = pool or PoolFalso()
    app = crear_app(
        config or config_prueba(),
        pool=pool,
        almacenamiento=almacenamiento,
        reloj=reloj or (lambda: AHORA),
        configurar_observabilidad=False,
    )
    return app, pool


def http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
