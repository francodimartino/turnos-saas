"""Endpoints `/auth/*`: proxy de Supabase Auth con cookies HttpOnly (ARCHITECTURE.md §3.1.5, §3.6).

Ningún endpoint toma `tenant_id`, `is_owner` ni `aal` del body: los modelos solo declaran los campos que usan (el
resto se ignora) y la identidad sale del token que emitió Supabase, verificado con `platform_auth`.

  POST /auth/login                email + password -> cookies de sesión; indica el siguiente paso (MFA)
  POST /auth/mfa/enrolar          alta de un factor TOTP (acepta sesión aal1 de owner/admin: §3.6)
  POST /auth/mfa/verificar        código TOTP -> sesión aal2 (reemplaza las cookies)
  POST /auth/invitacion/aceptar   token_hash del mail de invitación + contraseña nueva -> sesión
  POST /auth/refrescar            renueva las cookies con el refresh token
  POST /auth/logout               cierra la sesión y borra las cookies
  GET  /auth/sesion               quién soy (user_id, tenant_id, is_owner, aal, permisos vivos)
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from platform_auth import (
    ErrorAuth,
    NoAutenticado,
    SesionDashboard,
    VerificadorJWT,
    extraer_bearer,
    listar_permisos_vivos,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dashboard_backend.config import Config
from dashboard_backend.cookies import COOKIE_ACCESO, COOKIE_REFRESCO, borrar_sesion, poner_sesion
from dashboard_backend.dependencias import DependenciasDashboard, Transaccion, http_desde_error_auth
from dashboard_backend.supabase_auth import (
    AuthNoDisponible,
    ClienteSupabaseAuth,
    SesionExpirada,
    TokensSesion,
)

_log = logging.getLogger("dashboard_backend.auth")
NOMBRE_TOTP = "Turnos"


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
            raise ValueError("el email no tiene formato válido")
        return v


class VerificarMFAIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    factor_id: uuid.UUID
    codigo: str = Field(pattern=r"^\d{6}$")


class AceptarInvitacionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token_hash: str = Field(min_length=1, max_length=2048)
    password: str = Field(min_length=1, max_length=1024)


def _sin_acceso() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"codigo": "sin_acceso_dashboard", "mensaje": "Esta cuenta no tiene acceso a ningún negocio"},
    )


def crear_router_auth(
    config: Config,
    cliente: ClienteSupabaseAuth,
    deps: DependenciasDashboard,
    verificador: VerificadorJWT,
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    def _identidad_de(tokens: TokensSesion):
        try:
            return verificador.verificar(tokens.access_token)
        except ErrorAuth:
            # Supabase emitió un token que no verifica: configuración rota (secreto/JWKS/audiencia), no del usuario.
            _log.error("token_emitido_no_verifica")
            raise AuthNoDisponible() from None

    def _siguiente_paso(tokens: TokensSesion) -> dict[str, Any]:
        identidad = _identidad_de(tokens)
        if identidad.tenant_id is None:
            # Franco o staff desactivado: no es una sesión de dashboard y no se le entregan cookies.
            raise _sin_acceso()
        if tokens.factores_verificados and identidad.aal != "aal2":
            return {"siguiente_paso": "verificar_mfa", "factor_id": tokens.factores_verificados[0]}
        if not tokens.factores_verificados and (identidad.is_owner or identidad.is_platform_admin):
            return {"siguiente_paso": "enrolar_mfa"}
        return {"siguiente_paso": "listo"}

    def _token_de(request: Request) -> str:
        try:
            return extraer_bearer(request.headers.get("authorization"))
        except ErrorAuth as e:
            raise http_desde_error_auth(e) from None

    @router.post("/login")
    async def login(datos: LoginIn, respuesta: Response) -> dict[str, Any]:
        tokens = await cliente.login_password(datos.email, datos.password)
        cuerpo = {**_siguiente_paso(tokens), "expira_en": tokens.expires_in}
        poner_sesion(respuesta, tokens, config)
        return cuerpo

    @router.post("/mfa/enrolar")
    async def enrolar_mfa(
        request: Request,
        respuesta: Response,
        _sesion: Annotated[SesionDashboard, Depends(deps.sesion_enrollment)],
    ) -> dict[str, str]:
        e = await cliente.enrolar_totp(_token_de(request), NOMBRE_TOTP)
        respuesta.headers["Cache-Control"] = "no-store"
        return {"factor_id": e.factor_id, "qr_code": e.qr_code, "secret": e.secret, "uri": e.uri}

    @router.post("/mfa/verificar")
    async def verificar_mfa(
        datos: VerificarMFAIn,
        request: Request,
        respuesta: Response,
        _sesion: Annotated[SesionDashboard, Depends(deps.sesion_enrollment)],
    ) -> dict[str, Any]:
        tokens = await cliente.verificar_totp(_token_de(request), str(datos.factor_id), datos.codigo)
        poner_sesion(respuesta, tokens, config)
        return {"siguiente_paso": "listo", "expira_en": tokens.expires_in}

    @router.post("/invitacion/aceptar")
    async def aceptar_invitacion(datos: AceptarInvitacionIn, respuesta: Response) -> dict[str, Any]:
        tokens = await cliente.aceptar_invitacion(datos.token_hash, datos.password)
        cuerpo = {**_siguiente_paso(tokens), "expira_en": tokens.expires_in}
        poner_sesion(respuesta, tokens, config)
        return cuerpo

    @router.post("/refrescar")
    async def refrescar(request: Request, respuesta: Response) -> Any:
        refresco = request.cookies.get(COOKIE_REFRESCO)
        if not refresco:
            raise http_desde_error_auth(NoAutenticado())
        try:
            tokens = await cliente.refrescar(refresco)
        except SesionExpirada:
            # El refresh ya no sirve: se limpian las cookies para que el frontend vuelva al login.
            vencida = JSONResponse(
                status_code=401,
                content={"detail": {"codigo": SesionExpirada.codigo, "mensaje": SesionExpirada.mensaje}},
            )
            borrar_sesion(vencida, config)
            return vencida
        poner_sesion(respuesta, tokens, config)
        return {"expira_en": tokens.expires_in}

    @router.post("/logout", status_code=204)
    async def logout(request: Request, respuesta: Response) -> Response:
        acceso = request.cookies.get(COOKIE_ACCESO)
        if request.headers.get("authorization"):
            try:
                acceso = extraer_bearer(request.headers["authorization"])
            except ErrorAuth:
                acceso = None
        if acceso:
            await cliente.cerrar_sesion(acceso)
        salida = Response(status_code=204)
        borrar_sesion(salida, config)
        return salida

    @router.get("/sesion")
    async def sesion(tx: Annotated[Transaccion, Depends(deps.tx)]) -> dict[str, Any]:
        s = tx.sesion
        # Los permisos se leen en vivo en cada request (nunca del JWT): el frontend filtra la navegación con ellos.
        try:
            permisos = await listar_permisos_vivos(tx.conn, s)
        except ErrorAuth as e:
            raise http_desde_error_auth(e) from None
        return {
            "user_id": str(s.user_id),
            "tenant_id": str(s.tenant_id),
            "is_owner": s.is_owner,
            "aal": s.aal,
            "permisos": permisos,
        }

    return router
