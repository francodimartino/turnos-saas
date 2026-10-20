"""Endpoints `/auth/*` compartidos por los backends: proxy de Supabase Auth con cookies HttpOnly.

Ningún endpoint toma `tenant_id`, `is_owner` ni `aal` del body: los modelos solo declaran los campos que usan (el
resto se ignora) y la identidad sale del token que emitió Supabase, verificado con `platform_auth`.

  POST /auth/login           email + password -> cookies de sesión; indica el siguiente paso (MFA)
  POST /auth/mfa/enrolar     alta de un factor TOTP (sesión aal1; si ya hay un factor verificado exige aal2,
                             403 `mfa_ya_configurada`)
  POST /auth/mfa/verificar   código TOTP -> sesión aal2 (reemplaza las cookies)
  POST /auth/refrescar       renueva las cookies con el refresh token
  POST /auth/logout          cierra la sesión y borra las cookies
  GET  /auth/sesion          quién soy (el cuerpo lo arma cada backend)

El `router` es de módulo: en cada request toma lo que cada backend dejó en `app.state.auth` (un `ServiciosAuth`) y
sus dependencias de sesión de `app.state.dependencias`. Un backend suma sus endpoints propios en su propio router y
reutiliza las piezas de este módulo (`limitar_ip`, `exigir_turnstile`, `siguiente_paso`, `auditar_con_tokens`).

Protección contra fuerza bruta: /auth/login y /auth/mfa/verificar cuentan intentos por IP (429 `demasiados_intentos`
con `Retry-After`); /auth/refrescar tiene su propio contador por IP, con un tope mayor, para que un login agotado no
impida renovar la sesión; /auth/login además bloquea un email tras N intentos FALLIDOS (los logins correctos no
cuentan). Con Turnstile activo, el login exige `turnstile_token` (400 `turnstile_invalido`; 503
`turnstile_no_disponible` si Cloudflare no responde: falla cerrado). El rechazo ocurre antes de llamar a Supabase
Auth. Nada de esto loguea email, IP ni token.

Auditoría: login ok, alta y verificación de MFA, código TOTP inválido, refresh y logout dejan una fila
(`auth_login_succeeded`, `auth_mfa_enrolled`, `auth_mfa_verified`, `auth_mfa_failed`, `auth_session_refreshed`,
`auth_logged_out`) con la identidad conocida; la metadata lleva solo códigos genéricos (nunca email, password, IP ni
token). Quién escribe la fila y cómo lo hace lo decide `ServiciosAuth.auditar` (de mejor esfuerzo: si escribir el
audit falla, el flujo de autenticación sigue). Si el JWKS no está disponible, el logout cierra la sesión igual y omite
solo el audit.
"""

import logging
import math
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from platform_auth import ClavesNoDisponibles, ErrorAuth, IdentidadJWT, NoAutenticado, VerificadorJWT, extraer_bearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from turnos_http import http_desde_error_auth
from turnos_proteccion_login import ConfigIp, LimitadorIntentos, TurnstileNoDisponible, VerificadorTurnstile, ip_real
from turnos_supabase_proxy import (
    AuthNoDisponible,
    ClienteSupabaseAuth,
    CodigoMFAInvalido,
    CredencialesInvalidas,
    ErrorSupabaseAuth,
    MFAYaConfigurada,
    SesionExpirada,
    TokensSesion,
)

from .cookies import CookiesSesion
from .dependencias import dependencias_de
from .respuestas_auth import EnrolarMFAOut, MFAVerificadaOut, RefrescoOut, SesionOut, SiguientePasoOut

_log = logging.getLogger("turnos_sesion.auth")

# Acciones de auditoría de autenticación compartidas.
AUDIT_LOGIN_OK = "auth_login_succeeded"
AUDIT_MFA_ENROLADO = "auth_mfa_enrolled"
AUDIT_MFA_VERIFICADO = "auth_mfa_verified"
AUDIT_MFA_FALLIDO = "auth_mfa_failed"
AUDIT_REFRESCO = "auth_session_refreshed"
AUDIT_LOGOUT = "auth_logged_out"


@dataclass(frozen=True)
class ServiciosAuth:
    """Lo que cada backend decide para sus `/auth/*`; lo deja `crear_app` en `app.state.auth`.

    `auditar(accion, quien, metadata)` recibe la identidad o la sesión (algo con `user_id`, y `tenant_id` si el backend
    lo usa) o `None` si el token no se pudo identificar; nunca debe romper el flujo. `cuerpo_sesion(request)` arma la
    respuesta de `GET /auth/sesion` resolviendo su propia sesión. `login_fallido(codigo, error)` es opcional: se llama
    ante un login rechazado por Supabase (`error`) o por una cuenta sin acceso (`error=None`).
    """

    cliente: ClienteSupabaseAuth
    verificador: VerificadorJWT
    cookies: CookiesSesion
    turnstile: VerificadorTurnstile
    config_ip: ConfigIp
    por_ip: LimitadorIntentos
    por_email: LimitadorIntentos
    por_ip_refresco: LimitadorIntentos
    nombre_totp: str
    codigo_sin_acceso: str
    mensaje_sin_acceso: str
    tiene_acceso: Callable[[IdentidadJWT], bool]
    exige_mfa: Callable[[IdentidadJWT], bool]
    auditar: Callable[[str, Any, dict[str, Any] | None], Awaitable[None]]
    cuerpo_sesion: Callable[[Request], Awaitable[dict[str, Any]]]
    login_fallido: Callable[[str, ErrorSupabaseAuth | None], Awaitable[None]] | None = None


def servicios_de(request: Request) -> ServiciosAuth:
    return request.app.state.auth


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


def _demasiados_intentos(espera_seg: float, motivo: str) -> HTTPException:
    _log.warning("login_limite_excedido", extra={"codigo": "demasiados_intentos", "motivo": motivo})
    return HTTPException(
        status_code=429,
        detail={
            "codigo": "demasiados_intentos",
            "mensaje": "Demasiados intentos. Esperá un rato antes de volver a probar.",
        },
        headers={"Retry-After": str(max(1, math.ceil(espera_seg)))},
    )


def limitar_ip(request: Request) -> str:
    """Cuenta un intento de la IP real; 429 si la agotó. Devuelve la IP (para Turnstile)."""
    s = servicios_de(request)
    ip = ip_real(request, s.config_ip)
    resultado = s.por_ip.intentar(ip)
    if not resultado.permitido:
        raise _demasiados_intentos(resultado.espera_seg, "ip")
    return ip


def _limitar_ip_refresco(request: Request) -> None:
    s = servicios_de(request)
    resultado = s.por_ip_refresco.intentar(ip_real(request, s.config_ip))
    if not resultado.permitido:
        raise _demasiados_intentos(resultado.espera_seg, "ip_refresco")


async def exigir_turnstile(request: Request, token: str | None, ip: str) -> None:
    try:
        valido = await servicios_de(request).turnstile.verificar(token, ip)
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


def _sin_acceso(s: ServiciosAuth) -> HTTPException:
    return HTTPException(status_code=403, detail={"codigo": s.codigo_sin_acceso, "mensaje": s.mensaje_sin_acceso})


def _identidad_de(s: ServiciosAuth, tokens: TokensSesion) -> IdentidadJWT:
    try:
        return s.verificador.verificar(tokens.access_token)
    except ErrorAuth:
        # Supabase emitió un token que no verifica: configuración rota (secreto/JWKS/audiencia), no del usuario.
        _log.error("token_emitido_no_verifica")
        raise AuthNoDisponible() from None


def siguiente_paso(request: Request, tokens: TokensSesion) -> dict[str, Any]:
    """Qué sigue tras autenticarse. Una cuenta sin acceso a este backend no recibe cookies (403)."""
    s = servicios_de(request)
    identidad = _identidad_de(s, tokens)
    if not s.tiene_acceso(identidad):
        raise _sin_acceso(s)
    if tokens.factores_verificados and identidad.aal != "aal2":
        return {"siguiente_paso": "verificar_mfa", "factor_id": tokens.factores_verificados[0]}
    if not tokens.factores_verificados and s.exige_mfa(identidad):
        return {"siguiente_paso": "enrolar_mfa"}
    return {"siguiente_paso": "listo"}


def _identidad_para_audit(s: ServiciosAuth, accion: str, access_token: str) -> tuple[bool, IdentidadJWT | None]:
    """(hay con qué auditar, identidad). Un token inválido no tiene identidad (None); con el JWKS caído el token puede
    ser válido: se deja el motivo en el log y se omite solo este audit."""
    try:
        return True, s.verificador.verificar(access_token)
    except ClavesNoDisponibles:
        _log.error("audit_omitido_jwks_no_disponible", extra={"codigo": accion})
        return False, None
    except ErrorAuth:
        return True, None


async def auditar_con_tokens(
    request: Request, accion: str, tokens: TokensSesion, metadata: dict[str, Any] | None = None
) -> None:
    s = servicios_de(request)
    hay, identidad = _identidad_para_audit(s, accion, tokens.access_token)
    if hay:
        await s.auditar(accion, identidad, metadata)


def _token_de(request: Request) -> str:
    try:
        return extraer_bearer(request.headers.get("authorization"))
    except ErrorAuth as e:
        raise http_desde_error_auth(e) from None


async def _sesion_enrollment(request: Request) -> AsyncIterator[Any]:
    """La sesión que acepta `aal1` (solo para enrolar y verificar el segundo factor), con su contexto de log."""
    async with aclosing(dependencias_de(request).sesion_enrollment(request)) as generador:
        yield await anext(generador)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=SiguientePasoOut, response_model_exclude_unset=True)
async def login(datos: LoginIn, request: Request, respuesta: Response) -> Any:
    s = servicios_de(request)
    ip = limitar_ip(request)
    await exigir_turnstile(request, datos.turnstile_token, ip)
    # El límite por email cuenta solo los fallos por credenciales y se consulta antes de validarlas.
    bloqueo = s.por_email.consultar(datos.email)
    if not bloqueo.permitido:
        raise _demasiados_intentos(bloqueo.espera_seg, "email")
    try:
        tokens = await s.cliente.login_password(datos.email, datos.password)
    except ErrorSupabaseAuth as e:
        if isinstance(e, CredencialesInvalidas):
            s.por_email.intentar(datos.email)
        if s.login_fallido:
            await s.login_fallido(e.codigo, e)
        raise
    try:
        paso = siguiente_paso(request, tokens)
    except HTTPException:
        if s.login_fallido:
            await s.login_fallido(s.codigo_sin_acceso, None)
        raise
    cuerpo = {**paso, "expira_en": tokens.expires_in}
    s.cookies.poner(respuesta, tokens)
    await auditar_con_tokens(request, AUDIT_LOGIN_OK, tokens, {"siguiente_paso": paso["siguiente_paso"]})
    return cuerpo


@router.post("/mfa/enrolar", response_model=EnrolarMFAOut)
async def enrolar_mfa(
    request: Request, respuesta: Response, sesion_mfa: Annotated[Any, Depends(_sesion_enrollment)]
) -> Any:
    s = servicios_de(request)
    acceso = _token_de(request)
    # Defensa propia (no depende de GoTrue): con un factor ya verificado, agregar otro exige haber pasado por
    # aal2; si no, quien robó una sesión aal1 enrolaría su propio TOTP. Si la consulta falla, se rechaza.
    if sesion_mfa.aal != "aal2" and await s.cliente.factores_verificados(acceso):
        raise MFAYaConfigurada()
    e = await s.cliente.enrolar_totp(acceso, s.nombre_totp)
    await s.auditar(AUDIT_MFA_ENROLADO, sesion_mfa, None)
    respuesta.headers["Cache-Control"] = "no-store"
    return {"factor_id": e.factor_id, "qr_code": e.qr_code, "secret": e.secret, "uri": e.uri}


@router.post("/mfa/verificar", dependencies=[Depends(limitar_ip)], response_model=MFAVerificadaOut)
async def verificar_mfa(
    datos: VerificarMFAIn,
    request: Request,
    respuesta: Response,
    sesion_mfa: Annotated[Any, Depends(_sesion_enrollment)],
) -> Any:
    s = servicios_de(request)
    try:
        tokens = await s.cliente.verificar_totp(_token_de(request), str(datos.factor_id), datos.codigo)
    except CodigoMFAInvalido as e:
        # A diferencia del login fallido, acá el actor ya está identificado (pasó la contraseña): el intento va al
        # audit, sin el código ni el factor.
        await s.auditar(AUDIT_MFA_FALLIDO, sesion_mfa, {"codigo": e.codigo})
        raise
    s.cookies.poner(respuesta, tokens)
    await s.auditar(AUDIT_MFA_VERIFICADO, sesion_mfa, None)
    return {"siguiente_paso": "listo", "expira_en": tokens.expires_in}


@router.post("/refrescar", response_model=RefrescoOut)
async def refrescar(request: Request, respuesta: Response) -> Any:
    s = servicios_de(request)
    _limitar_ip_refresco(request)
    refresco = request.cookies.get(s.cookies.refresco)
    if not refresco:
        raise http_desde_error_auth(NoAutenticado())
    try:
        tokens = await s.cliente.refrescar(refresco)
    except SesionExpirada:
        # El refresh ya no sirve: se limpian las cookies para que el frontend vuelva al login.
        vencida = JSONResponse(
            status_code=401,
            content={"detail": {"codigo": SesionExpirada.codigo, "mensaje": SesionExpirada.mensaje}},
        )
        s.cookies.borrar(vencida)
        return vencida
    s.cookies.poner(respuesta, tokens)
    await auditar_con_tokens(request, AUDIT_REFRESCO, tokens)
    return {"expira_en": tokens.expires_in}


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    s = servicios_de(request)
    acceso = request.cookies.get(s.cookies.acceso)
    if request.headers.get("authorization"):
        try:
            acceso = extraer_bearer(request.headers["authorization"])
        except ErrorAuth:
            acceso = None
    if acceso:
        # JWKS caído: la sesión se cierra igual y se omite solo el audit. Token vencido o inválido: no hay identidad
        # confiable que auditar.
        hay, quien = _identidad_para_audit(s, AUDIT_LOGOUT, acceso)
        await s.cliente.cerrar_sesion(acceso)
        if hay and quien is not None:
            await s.auditar(AUDIT_LOGOUT, quien, None)
    salida = Response(status_code=204)
    s.cookies.borrar(salida)
    return salida


@router.get("/sesion", response_model=SesionOut, response_model_exclude_unset=True)
async def sesion(request: Request) -> Any:
    return await servicios_de(request).cuerpo_sesion(request)
