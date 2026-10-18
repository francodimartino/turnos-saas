"""Regla de MFA (§3.6): owner o platform admin exigen aal2, salvo aal1 hacia el enrollment."""

import uuid

import pytest

from platform_auth import IdentidadJWT, MFARequerida, exigir_mfa


def _ident(*, owner=False, admin=False, aal="aal1"):
    return IdentidadJWT(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), is_owner=owner, is_platform_admin=admin, aal=aal)


PRIVILEGIOS = [(True, False), (False, True), (True, True)]


@pytest.mark.parametrize("owner,admin", PRIVILEGIOS)
@pytest.mark.parametrize("aal", ["aal1", None, "", "aal3", "AAL2"])
def test_privilegiado_sin_aal2_es_rechazado_en_endpoint_comun(owner, admin, aal):
    with pytest.raises(MFARequerida) as e:
        exigir_mfa(_ident(owner=owner, admin=admin, aal=aal), enrollment=False)
    assert e.value.status_code == 403


@pytest.mark.parametrize("owner,admin", PRIVILEGIOS)
def test_privilegiado_con_aal2_pasa(owner, admin):
    exigir_mfa(_ident(owner=owner, admin=admin, aal="aal2"), enrollment=False)


@pytest.mark.parametrize("owner,admin", PRIVILEGIOS)
def test_privilegiado_aal1_pasa_solo_en_enrollment(owner, admin):
    exigir_mfa(_ident(owner=owner, admin=admin, aal="aal1"), enrollment=True)


@pytest.mark.parametrize("aal", [None, "", "aal3"])
def test_enrollment_no_acepta_aal_desconocido(aal):
    with pytest.raises(MFARequerida):
        exigir_mfa(_ident(owner=True, aal=aal), enrollment=True)


@pytest.mark.parametrize("aal", ["aal1", "aal2", None])
@pytest.mark.parametrize("enrollment", [False, True])
def test_staff_no_owner_pasa_con_cualquier_aal(aal, enrollment):
    exigir_mfa(_ident(aal=aal), enrollment=enrollment)


def test_owner_efectivo_en_vivo_fuerza_mfa_aunque_el_claim_diga_false():
    # El claim puede estar desactualizado (ascendido a owner después de emitido el token).
    ident = _ident(owner=False, aal="aal1")
    with pytest.raises(MFARequerida):
        exigir_mfa(ident, enrollment=False, es_owner=True)
    exigir_mfa(_ident(owner=False, aal="aal2"), enrollment=False, es_owner=True)


@pytest.mark.parametrize("owner,admin", PRIVILEGIOS)
def test_default_sin_enrollment_rechaza_privilegiado_aal1(owner, admin):
    # Sin pasar `enrollment`, el default es False: aal1 se rechaza (fallaría si el default pasara a True).
    with pytest.raises(MFARequerida) as e:
        exigir_mfa(_ident(owner=owner, admin=admin, aal="aal1"))
    assert e.value.status_code == 403


@pytest.mark.parametrize("owner,admin", PRIVILEGIOS)
def test_enrollment_true_explicito_acepta_aal1(owner, admin):
    exigir_mfa(_ident(owner=owner, admin=admin, aal="aal1"), enrollment=True)
