"""Cookies de sesión y defensa CSRF compartidas por los backends FastAPI (dashboard-backend y admin-backend).

Mecanismo (decisión de seguridad):
  - Los tokens de Supabase viajan al navegador **solo** en cookies `HttpOnly` + `Secure` + `SameSite=Strict`
    (configurable a `Lax`; `None` no se admite): el JavaScript del frontend nunca los lee, así que un XSS no puede
    robarlos. Nunca van en el body de una respuesta.
  - La cookie del access token (`<prefijo>_acceso`) vale en todo el sitio; la del refresh token
    (`<prefijo>_refresco`) solo se envía a `/auth/*` para minimizar su exposición. Cada backend usa su propio
    prefijo (`dash`, `adm`) para no mezclar sesiones.
  - Defensa contra CSRF en capas: `SameSite`, CORS acotado y `MiddlewareSesionCookie`, que exige `Origin` igual al
    del frontend en todo método que escribe (un navegador siempre lo manda en esos métodos cross-origin y no lo
    puede falsificar un sitio ajeno). La excepción es un request con `Authorization` explícito, que no depende de
    la cookie.
  - Sin `Authorization` pero con la cookie de acceso, el middleware la inyecta como `Authorization: Bearer <token>`
    para que `platform_auth` (que lee ese header) siga siendo la única puerta.

Además, `DependenciasSesion` y `sesion_de_app` dan las dependencias de FastAPI de sesión con su contexto de log, y
`rutas_auth` define una sola vez los endpoints `/auth/*` (login, MFA, refresh, logout y sesión) como router de módulo;
cada backend lo parametriza con un `ServiciosAuth` en `app.state.auth`.

Este paquete no importa código de las apps.
"""

from .cookies import RUTA_REFRESCO, CookiesSesion, MiddlewareSesionCookie
from .dependencias import DependenciasSesion, dependencias_de, sesion_de_app
from .rutas_auth import ServiciosAuth

__all__ = [
    "RUTA_REFRESCO",
    "CookiesSesion",
    "DependenciasSesion",
    "MiddlewareSesionCookie",
    "ServiciosAuth",
    "dependencias_de",
    "sesion_de_app",
]
