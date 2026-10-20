"""Fija las dependencias de sesión compartidas: contexto de log, cierre del generador y 401 sin tocar nada más."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from turnos_sesion import DependenciasSesion, dependencias_de, sesion_de_app


class _Sesion:
    tenant_id = "t-1"


def _app(*, falla: bool = False, tenant: bool = True) -> tuple[FastAPI, list[str]]:
    eventos: list[str] = []

    async def normal(request: Request):
        eventos.append("normal")
        if falla:
            raise HTTPException(401, "token_invalido")
        return _Sesion()

    async def enrollment(request: Request):
        eventos.append("enrollment")
        return _Sesion()

    deps = DependenciasSesion(
        normal,
        enrollment,
        actor_type="tenant_user",
        contexto_de=(lambda s: {"tenant_id": s.tenant_id}) if tenant else None,
    )
    app = FastAPI()
    app.state.dependencias = deps

    async def sesion_actual(request: Request):
        async for s in sesion_de_app(request):
            yield s

    @app.get("/x")
    async def x(s: Annotated[_Sesion, Depends(sesion_actual)]):
        from turnos_observabilidad.logs import _actor_type, _tenant_id

        return {"actor": _actor_type.get(), "tenant": _tenant_id.get()}

    @app.get("/e")
    async def e(request: Request):
        async for _ in dependencias_de(request).sesion_enrollment(request):
            return {"ok": True}
        return {"ok": False}

    return app, eventos


async def test_el_contexto_de_log_lleva_actor_y_tenant():
    app, _ = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/x")
    assert r.json() == {"actor": "tenant_user", "tenant": "t-1"}


async def test_sin_contexto_de_solo_fija_el_actor():
    app, _ = _app(tenant=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/x")
    assert r.json() == {"actor": "tenant_user", "tenant": None}


async def test_el_error_de_la_dependencia_base_se_propaga_tal_cual():
    app, _ = _app(falla=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/x")
    assert r.status_code == 401
    assert r.json() == {"detail": "token_invalido"}


async def test_enrollment_usa_la_dependencia_de_enrollment():
    app, eventos = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/e")
    assert r.status_code == 200
    assert eventos == ["enrollment"]


def test_dependencias_de_lee_app_state():
    class R:
        class app:  # noqa: N801
            class state:  # noqa: N801
                dependencias = object()

    assert dependencias_de(R) is R.app.state.dependencias
