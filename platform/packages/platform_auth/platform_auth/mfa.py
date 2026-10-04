"""Regla de MFA (§3.6): quien es owner o platform admin necesita aal2."""

from platform_auth.errores import MFARequerida
from platform_auth.identidad import IdentidadJWT


def exigir_mfa(identidad: IdentidadJWT, *, enrollment: bool = False, es_owner: bool | None = None) -> None:
    """Rechaza (`MFARequerida`, 403) a un owner/platform admin cuya sesión no sea `aal2`.

    Excepción única: en el endpoint de enrollment de MFA se acepta `aal1`, porque sin ella nadie podría
    configurar el segundo factor la primera vez. Cualquier otro valor de `aal` (ausente, desconocido)
    se rechaza también en el enrollment.

    `es_owner` es el valor leído en vivo de la base: si es verdadero pesa aunque el claim diga lo contrario
    (un ascenso posterior a la emisión del token). Un valor en vivo `False` no relaja lo que dice el claim.
    """
    privilegiado = identidad.is_owner or identidad.is_platform_admin or bool(es_owner)
    if not privilegiado:
        return
    if identidad.aal == "aal2":
        return
    if enrollment and identidad.aal == "aal1":
        return
    raise MFARequerida()
