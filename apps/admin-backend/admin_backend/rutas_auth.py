"""Endpoints `/auth/*`: proxy de Supabase Auth con cookies HttpOnly (ARCHITECTURE.md §3.1.5, §3.6).

La identidad sale del token que emitió Supabase, verificado con `platform_auth`; nada (`aal`, `is_platform_admin`)
se toma del body. El panel exige MFA a todo platform admin: no hay `siguiente_paso: listo` sin `aal2`.

  POST /auth/login           email + password -> cookies de sesión; indica el siguiente paso (MFA)
  POST /auth/mfa/enrolar     alta de un factor TOTP (sesión aal1 de platform admin: §3.6; si ya hay un factor
                            verificado exige aal2, 403 `mfa_ya_configurada`)
  POST /auth/mfa/verificar   código TOTP -> sesión aal2 (reemplaza las cookies); un código inválido deja
                            `auth_mfa_failed` en el audit (con el actor, sin el código ni el factor)
  POST /auth/refrescar       renueva las cookies con el refresh token (límite por IP propio, `LOGIN_LIMITE_REFRESCO`)
  POST /auth/logout          cierra la sesión y borra las cookies
  GET  /auth/sesion          quién soy (user_id, aal)
"""

import logging
import math
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from platform_audit import registrar
from platform_auth import ClavesNoDisponibles, ErrorAuth, NoAutenticado, SesionAdmin, VerificadorJWT, extraer_bearer
from platform_tenancy import admin_tx
from proteccion_login import LimitadorIntentos, TurnstileNoDisponible, VerificadorTurnstile, ip_real
from pydantic import BaseModel, ConfigDict, Field, field_validator
from supabase_proxy import (
    AuthNoDisponible,
    ClienteSupabaseAuth,
    CodigoMFAInvalido,
    CredencialesInvalidas,
    ErrorSupabaseAuth,
    MFAYaConfigurada,
    SesionExpirada,
    TokensSesion,
)

from admin_backend.config import Config
from admin_backend.cookies import COOKIE_ACCESO, COOKIE_REFRESCO, borrar_sesion, poner_sesion
from admin_backend.dependencias import DependenciasAdmin
from admin_backend.errores import http_desde_error_auth

_log = logging.getLogger("admin_backend.auth")
NOMBRE_TOTP = "Turnos Admin"

# Acciones de `audit_log` de los eventos de autenticación (actor platform_admin, sin tenant). La metadata lleva
# solo códigos: nunca email, IP, contraseña ni token.
AUDIT_LOGIN_OK = "auth_login_succeeded"
AUDIT_LOGIN_FALLIDO = "auth_login_failed"
AUDIT_MFA_ENROLADO = "auth_mfa_enrolled"
AUDIT_MFA_VERIFICADO = "auth_mfa_verified"
AUDIT_MFA_FALLIDO = "auth_mfa_failed"
AUDIT_REFRESCO = "auth_session_refreshed"
AUDIT_LOGOUT = "auth_logged_out"


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    turnstile_token: str | None = Field(default=None, max_length=2048)

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


def _sin_acceso() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"codigo": "sin_acceso_admin", "mensaje": "Esta cuenta no es administradora de la plataforma"},
    )


def _demasiados_intentos(espera_seg: float, motivo: str) -> HTTPException:
    _log.warning("login_limite_excedido", extra={"codigo": motivo})
    return HTTPException(
        status_code=429,
        detail={"codigo": "demasiados_intentos", "mensaje": "Demasiados intentos, probá de nuevo más tarde"},
        headers={"Retry-After": str(max(1, math.ceil(espera_seg)))},
    )


def crear_router_auth(
    config: Config,
    cliente: ClienteSupabaseAuth,
    deps: DependenciasAdmin,
    verificador: VerificadorJWT,
    turnstile: VerificadorTurnstile | None = None,
    obtener_pool: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    turnstile = turnstile or VerificadorTurnstile(config.turnstile_secret_key)
    por_ip = LimitadorIntentos(config.login_limite_ip, config.login_ventana_seg)
    por_ip_refresco = LimitadorIntentos(config.refresco_limite_ip, config.login_ventana_seg)
    por_email = LimitadorIntentos(config.login_limite_email, config.login_ventana_seg)

    async def _auditar(accion: str, actor_id: uuid.UUID | None, metadata: dict[str, Any] | None = None) -> None:
        """Escribe el evento en `audit_log` sin romper el flujo: si falla, queda un log de error sin datos."""
        if obtener_pool is None:
            return
        try:
            async with admin_tx(obtener_pool(), actor_id) as conn:
                await registrar(conn, accion, "auth", actor_id, metadata)
        except Exception as e:
            _log.error("audit_auth_fallido", extra={"accion": accion, "error_tipo": type(e).__name__})

    def _actor_de(access_token: str | None) -> uuid.UUID | None:
        """Actor del token para el audit. Un token inválido no tiene identidad (None, en silencio); si el JWKS no
        está disponible el token puede ser válido: se deja el motivo en el log y se propaga `ClavesNoDisponibles`
        para que quien llama omita solo el audit."""
        if not access_token:
            return None
        try:
            return verificador.verificar(access_token).user_id
        except ClavesNoDisponibles:
            _log.error("audit_omitido_jwks_no_disponible")
            raise
        except ErrorAuth:
            return None

    async def _auditar_con_token(accion: str, access_token: str, metadata: dict[str, Any] | None = None) -> None:
        try:
            actor = _actor_de(access_token)
        except ClavesNoDisponibles:
            return  # JWKS caído (ya logueado): se omite solo este audit
        await _auditar(accion, actor, metadata)

    def _limitar_ip(request: Request) -> str:
        ip = ip_real(request, config.config_ip)
        r = por_ip.intentar(ip)
        if not r.permitido:
            raise _demasiados_intentos(r.espera_seg, "ip")
        return ip

    def _limitar_ip_refresco(request: Request) -> None:
        r = por_ip_refresco.intentar(ip_real(request, config.config_ip))
        if not r.permitido:
            raise _demasiados_intentos(r.espera_seg, "ip_refresco")

    def _siguiente_paso(tokens: TokensSesion) -> dict[str, Any]:
        try:
            identidad = verificador.verificar(tokens.access_token)
        except ErrorAuth:
            # Supabase emitió un token que no verifica: configuración rota (secreto/JWKS/audiencia), no del usuario.
            _log.error("token_emitido_no_verifica")
            raise AuthNoDisponible() from None
        if not identidad.is_platform_admin:
            # No es un admin: no se le entregan cookies (el gate igual lo frenaría, esto evita sesiones inútiles).
            raise _sin_acceso()
        if tokens.factores_verificados and identidad.aal != "aal2":
            return {"siguiente_paso": "verificar_mfa", "factor_id": tokens.factores_verificados[0]}
        if not tokens.factores_verificados:
            return {"siguiente_paso": "enrolar_mfa"}
        return {"siguiente_paso": "listo"}

    def _token_de(request: Request) -> str:
        try:
            return extraer_bearer(request.headers.get("authorization"))
        except ErrorAuth as e:
            raise http_desde_error_auth(e) from None

    @router.post("/login")
    async def login(datos: LoginIn, request: Request, respuesta: Response) -> dict[str, Any]:
        ip = _limitar_ip(request)
        try:
            token_valido = await turnstile.verificar(datos.turnstile_token, ip)
        except TurnstileNoDisponible:
            raise HTTPException(
                status_code=503,
                detail={"codigo": "turnstile_no_disponible", "mensaje": "No se pudo verificar el captcha"},
            ) from None
        if not token_valido:
            raise HTTPException(
                status_code=400, detail={"codigo": "turnstile_invalido", "mensaje": "Captcha inválido o ausente"}
            )
        # El límite por email cuenta solo los fallos y se consulta antes de validar las credenciales.
        bloqueo = por_email.consultar(datos.email)
        if not bloqueo.permitido:
            raise _demasiados_intentos(bloqueo.espera_seg, "email")
        try:
            tokens = await cliente.login_password(datos.email, datos.password)
        except ErrorSupabaseAuth as e:
            if isinstance(e, CredencialesInvalidas):
                por_email.intentar(datos.email)
            await _auditar(AUDIT_LOGIN_FALLIDO, None, {"codigo": e.codigo})
            raise
        try:
            paso = _siguiente_paso(tokens)
        except HTTPException as e:
            await _auditar(AUDIT_LOGIN_FALLIDO, None, {"codigo": "sin_acceso_admin"})
            raise e from None
        cuerpo = {**paso, "expira_en": tokens.expires_in}
        await _auditar_con_token(AUDIT_LOGIN_OK, tokens.access_token, {"siguiente_paso": paso["siguiente_paso"]})
        poner_sesion(respuesta, tokens, config)
        return cuerpo

    @router.post("/mfa/enrolar")
    async def enrolar_mfa(
        request: Request,
        respuesta: Response,
        _sesion: Annotated[SesionAdmin, Depends(deps.sesion_enrollment)],
    ) -> dict[str, str]:
        acceso = _token_de(request)
        # Defensa propia (no depende de GoTrue): con un factor ya verificado, agregar otro exige haber pasado por
        # aal2; si no, quien robó una sesión aal1 enrolaría su propio TOTP. Si la consulta falla, se rechaza.
        if _sesion.aal != "aal2" and await cliente.factores_verificados(acceso):
            raise MFAYaConfigurada()
        e = await cliente.enrolar_totp(acceso, NOMBRE_TOTP)
        await _auditar(AUDIT_MFA_ENROLADO, _sesion.user_id)
        respuesta.headers["Cache-Control"] = "no-store"
        return {"factor_id": e.factor_id, "qr_code": e.qr_code, "secret": e.secret, "uri": e.uri}

    @router.post("/mfa/verificar")
    async def verificar_mfa(
        datos: VerificarMFAIn,
        request: Request,
        respuesta: Response,
        _limite: Annotated[str, Depends(_limitar_ip)],
        _sesion: Annotated[SesionAdmin, Depends(deps.sesion_enrollment)],
    ) -> dict[str, Any]:
        try:
            tokens = await cliente.verificar_totp(_token_de(request), str(datos.factor_id), datos.codigo)
        except CodigoMFAInvalido as e:
            # El actor ya está identificado (pasó la contraseña): el intento fallido va al audit, sin código ni factor.
            await _auditar(AUDIT_MFA_FALLIDO, _sesion.user_id, {"codigo": e.codigo})
            raise
        await _auditar(AUDIT_MFA_VERIFICADO, _sesion.user_id)
        poner_sesion(respuesta, tokens, config)
        return {"siguiente_paso": "listo", "expira_en": tokens.expires_in}

    @router.post("/refrescar")
    async def refrescar(request: Request, respuesta: Response) -> Any:
        _limitar_ip_refresco(request)
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
        await _auditar_con_token(AUDIT_REFRESCO, tokens.access_token)
        poner_sesion(respuesta, tokens, config)
        return {"expira_en": tokens.expires_in}

    @router.post("/logout", status_code=204)
    async def logout(request: Request) -> Response:
        acceso = request.cookies.get(COOKIE_ACCESO)
        if request.headers.get("authorization"):
            try:
                acceso = extraer_bearer(request.headers["authorization"])
            except ErrorAuth:
                acceso = None
        if acceso:
            try:
                actor = _actor_de(acceso)
            except ClavesNoDisponibles:
                actor = None  # JWKS caído (ya logueado): la sesión se cierra igual, solo se omite el audit
            await cliente.cerrar_sesion(acceso)
            if actor is not None:
                await _auditar(AUDIT_LOGOUT, actor)
        salida = Response(status_code=204)
        borrar_sesion(salida, config)
        return salida

    @router.get("/sesion")
    async def sesion(s: Annotated[SesionAdmin, Depends(deps.sesion)]) -> dict[str, Any]:
        return {"user_id": str(s.user_id), "aal": s.aal}

    return router
