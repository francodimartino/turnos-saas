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

Protección contra fuerza bruta (variables en `config`): /auth/login, /auth/refrescar, /auth/mfa/verificar y
/auth/invitacion/aceptar cuentan intentos por IP (429 `demasiados_intentos` con `Retry-After`); /auth/login además
bloquea un email tras N intentos FALLIDOS (los logins correctos no cuentan). Con `TURNSTILE_SECRET_KEY`, login e
invitación exigen `turnstile_token` (400 `turnstile_invalido`; 503 `turnstile_no_disponible` si Cloudflare no
responde: falla cerrado). El rechazo ocurre antes de llamar a Supabase Auth. Nada de esto loguea email, IP ni token.

Auditoría (`audit_log`): login ok, alta y verificación de MFA, refresh y logout dejan una fila
(`auth_login_succeeded`, `auth_mfa_enrolled`, `auth_mfa_verified`, `auth_session_refreshed`,
`auth_logged_out`) con tenant y
actor cuando se conocen. La metadata lleva solo códigos genéricos (nunca email, password, IP ni token). Es de mejor
esfuerzo: si escribir el audit falla, se loguea sin PII y el flujo de autenticación sigue.
El login FALLIDO no va a `audit_log`: un intento con un email cualquiera no pertenece a ningún local (no hay tenant), y
abrir la escritura sin local permitiría llenar la tabla desde afuera. Queda solo como log estructurado
(`auth_login_failed`, sin email, IP ni token) y en los logs de Supabase Auth.
"""

import logging
import math
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from platform_audit import registrar
from platform_auth import (
    ErrorAuth,
    NoAutenticado,
    SesionDashboard,
    VerificadorJWT,
    extraer_bearer,
    listar_permisos_vivos,
)
from platform_tenancy import tenant_tx
from proteccion_login import (
    LimitadorIntentos,
    TurnstileNoDisponible,
    VerificadorTurnstile,
    ip_real,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dashboard_backend.config import Config
from dashboard_backend.cookies import COOKIE_ACCESO, COOKIE_REFRESCO, borrar_sesion, poner_sesion
from dashboard_backend.dependencias import DependenciasDashboard, Transaccion, http_desde_error_auth
from dashboard_backend.supabase_auth import (
    AuthNoDisponible,
    ClienteSupabaseAuth,
    CredencialesInvalidas,
    SesionExpirada,
    TokensSesion,
)

_log = logging.getLogger("dashboard_backend.auth")
NOMBRE_TOTP = "Turnos"


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    turnstile_token: str | None = Field(default=None, max_length=4096)

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
    turnstile_token: str | None = Field(default=None, max_length=4096)


def _demasiados_intentos(espera_seg: float) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "codigo": "demasiados_intentos",
            "mensaje": "Demasiados intentos. Esperá un rato antes de volver a probar.",
        },
        headers={"Retry-After": str(max(1, math.ceil(espera_seg)))},
    )


def _sin_acceso() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "codigo": "sin_acceso_dashboard",
            "mensaje": (
                "Esta cuenta no tiene acceso a ningún negocio. "
                "Si administrás la plataforma, entrá por el panel de administración."
            ),
        },
    )


def crear_router_auth(
    config: Config,
    cliente: ClienteSupabaseAuth,
    deps: DependenciasDashboard,
    verificador: VerificadorJWT,
    turnstile: VerificadorTurnstile | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    turnstile = turnstile or VerificadorTurnstile(config.turnstile_secret)
    limites = config.login_limites
    por_ip = LimitadorIntentos(limites.por_ip, limites.ventana_seg)
    por_email = LimitadorIntentos(limites.por_email, limites.ventana_seg)

    def _limitar_ip(request: Request) -> str:
        ip = ip_real(request)
        resultado = por_ip.intentar(ip)
        if not resultado.permitido:
            _log.warning("login_limite_ip", extra={"codigo": "demasiados_intentos"})
            raise _demasiados_intentos(resultado.espera_seg)
        return ip

    async def _exigir_turnstile(token: str | None, ip: str) -> None:
        try:
            valido = await turnstile.verificar(token, ip)
        except TurnstileNoDisponible:
            raise HTTPException(
                status_code=503,
                detail={
                    "codigo": "turnstile_no_disponible",
                    "mensaje": "No pudimos verificar que sos una persona. Probá de nuevo en unos minutos.",
                },
                headers={"Retry-After": "5"},
            ) from None
        if not valido:
            raise HTTPException(
                status_code=400,
                detail={"codigo": "turnstile_invalido", "mensaje": "No pudimos verificar que sos una persona."},
            )

    async def _auditar(
        accion: str,
        *,
        tenant_id: uuid.UUID,
        actor_id: uuid.UUID,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Escribe una fila de audit de autenticación. Nunca rompe el flujo: ante cualquier fallo, loguea y sigue."""
        try:
            pool = deps.obtener_pool()
            async with tenant_tx(pool, tenant_id, actor_id) as conn:
                await registrar(conn, accion, "auth", actor_id, metadata)
        except Exception as e:  # noqa: BLE001 - el audit es de mejor esfuerzo; el detalle puede nombrar datos
            _log.error("audit_auth_fallo", extra={"codigo": accion, "error_tipo": type(e).__name__})

    async def _auditar_identidad(accion: str, tokens: TokensSesion, metadata: dict[str, Any] | None = None) -> None:
        try:
            identidad = verificador.verificar(tokens.access_token)
        except ErrorAuth:
            return
        if identidad.tenant_id is None:
            return
        await _auditar(accion, tenant_id=identidad.tenant_id, actor_id=identidad.user_id, metadata=metadata)

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
    async def login(datos: LoginIn, request: Request, respuesta: Response) -> dict[str, Any]:
        ip = _limitar_ip(request)
        await _exigir_turnstile(datos.turnstile_token, ip)
        bloqueo = por_email.consultar(datos.email)
        if not bloqueo.permitido:
            _log.warning("login_limite_email", extra={"codigo": "demasiados_intentos"})
            raise _demasiados_intentos(bloqueo.espera_seg)
        try:
            tokens = await cliente.login_password(datos.email, datos.password)
        except CredencialesInvalidas:
            por_email.intentar(datos.email)  # solo los fallos cuentan contra el email
            _log.warning("auth_login_failed", extra={"motivo": "credenciales_invalidas"})
            raise
        paso = _siguiente_paso(tokens)
        cuerpo = {**paso, "expira_en": tokens.expires_in}
        poner_sesion(respuesta, tokens, config)
        await _auditar_identidad("auth_login_succeeded", tokens, {"siguiente_paso": paso["siguiente_paso"]})
        return cuerpo

    @router.post("/mfa/enrolar")
    async def enrolar_mfa(
        request: Request,
        respuesta: Response,
        sesion_mfa: Annotated[SesionDashboard, Depends(deps.sesion_enrollment)],
    ) -> dict[str, str]:
        e = await cliente.enrolar_totp(_token_de(request), NOMBRE_TOTP)
        await _auditar("auth_mfa_enrolled", tenant_id=sesion_mfa.tenant_id, actor_id=sesion_mfa.user_id)
        respuesta.headers["Cache-Control"] = "no-store"
        return {"factor_id": e.factor_id, "qr_code": e.qr_code, "secret": e.secret, "uri": e.uri}

    @router.post("/mfa/verificar", dependencies=[Depends(_limitar_ip)])
    async def verificar_mfa(
        datos: VerificarMFAIn,
        request: Request,
        respuesta: Response,
        sesion_mfa: Annotated[SesionDashboard, Depends(deps.sesion_enrollment)],
    ) -> dict[str, Any]:
        tokens = await cliente.verificar_totp(_token_de(request), str(datos.factor_id), datos.codigo)
        poner_sesion(respuesta, tokens, config)
        await _auditar("auth_mfa_verified", tenant_id=sesion_mfa.tenant_id, actor_id=sesion_mfa.user_id)
        return {"siguiente_paso": "listo", "expira_en": tokens.expires_in}

    @router.post("/invitacion/aceptar")
    async def aceptar_invitacion(datos: AceptarInvitacionIn, request: Request, respuesta: Response) -> dict[str, Any]:
        ip = _limitar_ip(request)
        await _exigir_turnstile(datos.turnstile_token, ip)
        tokens = await cliente.aceptar_invitacion(datos.token_hash, datos.password)
        cuerpo = {**_siguiente_paso(tokens), "expira_en": tokens.expires_in}
        poner_sesion(respuesta, tokens, config)
        return cuerpo

    @router.post("/refrescar")
    async def refrescar(request: Request, respuesta: Response) -> Any:
        _limitar_ip(request)
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
        await _auditar_identidad("auth_session_refreshed", tokens)
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
            try:
                quien = verificador.verificar(acceso)
            except ErrorAuth:
                quien = None  # token vencido o inválido: no hay identidad confiable que auditar
            if quien is not None and quien.tenant_id is not None:
                await _auditar("auth_logged_out", tenant_id=quien.tenant_id, actor_id=quien.user_id)
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
