"""POST /tenants/{id}/slug: permisos, cuerpo, respuesta y traducción de cada rechazo a su 4xx (sin base real)."""

import uuid

import platform_admin as pa
import pytest

from admin_backend.tests.soporte import app_de_prueba, bearer, http

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
RUTA = f"/tenants/{TENANT}/slug"


async def test_cambia_el_slug_con_el_actor_del_jwt_y_responde_el_anterior_y_el_nuevo(monkeypatch):
    visto = {}

    async def cambiar(pool, tenant_id, slug_nuevo, *, actor_id):
        visto.update(tenant=tenant_id, slug=slug_nuevo, actor=actor_id)
        return pa.ResultadoCambioSlug(tenant_id=tenant_id, slug_anterior="viejo", slug_nuevo=slug_nuevo)

    monkeypatch.setattr(pa, "cambiar_slug", cambiar)
    app, _, _ = app_de_prueba()
    admin_id = uuid.uuid4()
    async with http(app) as c:
        # un "actor_id" en el body se ignora: el actor es siempre el del JWT verificado
        r = await c.post(RUTA, json={"slug": "nuevo", "actor_id": str(uuid.uuid4())}, headers=bearer(user_id=admin_id))
    assert r.status_code == 200
    assert r.json() == {"tenant_id": str(TENANT), "slug_anterior": "viejo", "slug_nuevo": "nuevo"}
    assert visto == {"tenant": TENANT, "slug": "nuevo", "actor": admin_id}


@pytest.mark.parametrize(
    ("error", "estado", "codigo"),
    [
        (pa.SlugReservado("r"), 422, "slug_reservado"),
        (pa.SlugInvalido("f"), 422, "slug_invalido"),
        (pa.SlugEnUso("u"), 409, "slug_en_uso"),
        (pa.SlugSinCambio("s"), 409, "slug_sin_cambio"),
        (pa.CambioSlugConcurrente("c"), 409, "cambio_slug_concurrente"),
        (pa.TenantNoEncontrado("t"), 404, "tenant_no_encontrado"),
        (pa.PurgaEnCurso("cambiar el slug de"), 409, "purga_en_curso"),
        (pa.TransicionInvalida("cambiar el slug de", "purged"), 409, "transicion_invalida"),
    ],
)
async def test_cada_rechazo_de_dominio_tiene_su_4xx_y_su_codigo(monkeypatch, error, estado, codigo):
    async def cambiar(pool, tenant_id, slug_nuevo, *, actor_id):
        raise error

    monkeypatch.setattr(pa, "cambiar_slug", cambiar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.post(RUTA, json={"slug": "x-y-z"}, headers=bearer())
    assert r.status_code == estado and r.json()["detail"]["codigo"] == codigo


@pytest.mark.parametrize(
    ("slug", "codigo"),
    [
        ("admin", "slug_reservado"),
        ("www", "slug_reservado"),
        ("Mayuscula", "slug_invalido"),
        ("ab", "slug_invalido"),
        ("a" * 41, "slug_invalido"),
        ("con espacio", "slug_invalido"),
        ("a" * 150, "slug_invalido"),
        (None, "slug_invalido"),
        (123, "slug_invalido"),
    ],
)
async def test_reservado_formato_y_largo_dan_422_con_el_dominio_real_sin_tocar_la_base(slug, codigo):
    app, _, _ = app_de_prueba()  # sin monkeypatch: valida el dominio real antes de tocar la base
    async with http(app) as c:
        r = await c.post(RUTA, json={"slug": slug}, headers=bearer())
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == codigo


async def test_sin_slug_en_el_body_o_con_un_id_que_no_es_uuid_es_422():
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        sin_slug = await c.post(RUTA, json={}, headers=bearer())
        sin_body = await c.post(RUTA, headers=bearer())
        mal_id = await c.post("/tenants/no-es-uuid/slug", json={"slug": "valido"}, headers=bearer())
    assert sin_slug.status_code == sin_body.status_code == mal_id.status_code == 422


@pytest.mark.parametrize(
    "credenciales",
    [
        {"is_platform_admin": False, "tenant_id": TENANT, "is_owner": True},  # dueño de un negocio
        {"is_platform_admin": False, "tenant_id": TENANT},  # staff de un negocio
        {"is_platform_admin": False},
        {"aal": "aal1"},  # platform_admin sin MFA
    ],
)
async def test_solo_platform_admin_con_mfa_staff_sin_mfa_o_sin_token_no_cambian_el_slug(monkeypatch, credenciales):
    async def cambiar(pool, tenant_id, slug_nuevo, *, actor_id):
        raise AssertionError("no debe llegar al dominio")

    monkeypatch.setattr(pa, "cambiar_slug", cambiar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.post(RUTA, json={"slug": "valido"}, headers=bearer(**credenciales))
        sin = await c.post(RUTA, json={"slug": "valido"})
    assert r.status_code == 403
    assert sin.status_code in (401, 403)
