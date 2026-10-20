"""Dependencias de FastAPI de sesión que comparten los backends.

`DependenciasSesion` envuelve las dos dependencias de autenticación de `platform_auth` (la normal y la que acepta
`aal1` para enrolar el segundo factor) y fija el contexto de log (`actor_type` y, si el backend lo pide,
`tenant_id`) durante el request. Cada backend la crea con lo que difiere: qué dependencia de `platform_auth` usa
(tipo de sesión, exigencia de `aal2`), su `actor_type` y los campos de log que salen de la sesión.

`dependencias_de` y `sesion_de_app` resuelven en cada request lo que `crear_app` dejó en `app.state.dependencias`,
para que los routers de módulo no reciban `deps` por parámetro.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import Request
from observabilidad import contexto_log

DependenciaBase = Callable[[Request], Awaitable[Any]]


class DependenciasSesion:
    def __init__(
        self,
        normal: DependenciaBase,
        enrollment: DependenciaBase,
        *,
        actor_type: str,
        contexto_de: Callable[[Any], dict[str, str]] | None = None,
    ):
        self.actor_type = actor_type
        self._contexto_de = contexto_de

        async def sesion(request: Request) -> AsyncIterator[Any]:
            s = await normal(request)
            with self.contexto(s):
                yield s

        async def sesion_enrollment(request: Request) -> AsyncIterator[Any]:
            s = await enrollment(request)
            with self.contexto(s):
                yield s

        self.sesion = sesion
        self.sesion_enrollment = sesion_enrollment

    def contexto(self, sesion: Any) -> Any:
        """Context manager que fija el contexto de log de esta sesión (actor y, si corresponde, tenant)."""
        extra = self._contexto_de(sesion) if self._contexto_de else {}
        return contexto_log(actor_type=self.actor_type, **extra)


def dependencias_de(request: Request) -> Any:
    return request.app.state.dependencias


async def sesion_de_app(request: Request) -> AsyncGenerator[Any]:
    """La sesión autenticada de `app.state.dependencias.sesion`, con su contexto de log, cerrando el generador."""
    generador = dependencias_de(request).sesion(request)
    try:
        yield await anext(generador)
    finally:
        await generador.aclose()
