"""Los nombres públicos en inglés de los paquetes compartidos están documentados como excepción.

Qué se revisa: los nombres que expone la API de cada paquete `platform_*` y `turnos_*` (apps/shared): los módulos
públicos (`nombre.py`), lo que exporta su `__init__.py` en `__all__`, y las clases, funciones y constantes públicas
de cada módulo, con los métodos públicos de las clases. Se excluyen los tests y lo que empieza con `_`.

Qué es un "nombre en inglés": se parte el identificador en palabras (por `_` y por mayúsculas) y es inglés si alguna
palabra está en `PALABRAS_INGLESAS`, un diccionario chico de palabras inglesas frecuentes en identificadores que no
son también español. Las siglas y términos técnicos sin traducción usual (`TECNICISMOS`: api, mfa, json…) no cuentan.

Qué se exige: todo nombre en inglés aparece entre comillas invertidas en la regla "Idioma de los nombres" de
`AGENTS.md` (los módulos como `nombre.py`), con su motivo. Y lo que esa regla declara como API propia con inglés
tiene que existir: una excepción de un nombre que ya no está se borra.
"""

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AGENTS = RAIZ / "AGENTS.md"

PALABRAS_INGLESAS = frozenset(
    {
        *("acquire", "adapter", "admin", "audit", "backoff", "body", "booking", "bookings", "button", "cache"),
        *("calendar", "callback", "claim", "client", "command", "connect", "cookie", "cookies", "create", "dashboard"),
        *("default", "delete", "enrollment", "failed", "handler", "header", "headers", "invite", "key", "like"),
        *("limit", "log", "logging", "message", "middleware", "outbox", "owner", "pack", "payload", "pending"),
        *("platform", "pool", "provider", "purged", "purging", "query", "readonly", "refresh", "request", "response"),
        *("retry", "secret", "send", "sent", "service", "session", "setting", "settings", "staff", "state"),
        *("store", "sync", "tenant", "tenants", "timeout", "token", "tokens", "tx", "update", "user"),
    }
)
# Siglas y términos técnicos que en español se usan tal cual; no cuentan como inglés.
TECNICISMOS = frozenset(
    {
        *("api", "id", "ip", "url", "json", "jwt", "jwks", "mfa", "pii", "sql", "http", "uuid", "email"),
        *("login", "logout", "bot", "webhook", "hmac", "totp", "oauth", "utc", "config", "ok", "cf"),
    }
)
_PALABRA = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def palabras(nombre: str) -> list[str]:
    """Parte un identificador en palabras en minúscula: `NoEsPlatformAdmin` -> ['no', 'es', 'platform', 'admin']."""
    return [p.lower() for p in _PALABRA.findall(nombre)]


def es_ingles(nombre: str) -> bool:
    return any(p in PALABRAS_INGLESAS for p in palabras(nombre) if p not in TECNICISMOS)


def _publico(nombre: str) -> bool:
    return not nombre.startswith("_")


def _nombres_del_modulo(arbol: ast.Module) -> set[str]:
    nombres: set[str] = set()
    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and _publico(nodo.name):
            nombres.add(nodo.name)
        if isinstance(nodo, ast.ClassDef) and _publico(nodo.name):
            for miembro in nodo.body:
                if isinstance(miembro, ast.FunctionDef | ast.AsyncFunctionDef) and _publico(miembro.name):
                    nombres.add(miembro.name)
        if isinstance(nodo, ast.Assign):
            nombres |= {t.id for t in nodo.targets if isinstance(t, ast.Name) and _publico(t.id)}
        if isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name) and _publico(nodo.target.id):
            nombres.add(nodo.target.id)
    return nombres


def _exportados(arbol: ast.Module) -> set[str]:
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in nodo.targets):
            return {e.value for e in getattr(nodo.value, "elts", []) if isinstance(e, ast.Constant)}
    return set()


def carpetas_de_paquetes(raiz: Path) -> list[Path]:
    """Las carpetas Python de `platform/packages/platform_*` y `apps/shared/turnos_*` (con `__init__.py`)."""
    candidatas = [*(raiz / "platform" / "packages").glob("platform_*"), *(raiz / "apps" / "shared").glob("turnos_*")]
    carpetas = [c / c.name if (c / c.name / "__init__.py").is_file() else c for c in candidatas]
    return sorted(c for c in carpetas if (c / "__init__.py").is_file())


def nombres_publicos(raiz: Path) -> dict[str, set[str]]:
    """Mapa nombre -> paquetes donde es público. Los módulos aparecen como `nombre.py`."""
    salida: dict[str, set[str]] = {}
    for carpeta in carpetas_de_paquetes(raiz):
        for archivo in sorted(carpeta.rglob("*.py")):
            partes = archivo.relative_to(carpeta).parts
            if any(p.startswith("_") and p != "__init__.py" for p in partes):
                continue
            if any(p in ("tests", "test") or p.startswith("test_") or p == "conftest.py" for p in partes):
                continue
            arbol = ast.parse(archivo.read_text(encoding="utf-8"))
            if archivo.name == "__init__.py":
                nombres = _exportados(arbol)
            else:
                nombres = {f"{archivo.stem}.py"} | _nombres_del_modulo(arbol)
            for nombre in nombres:
                salida.setdefault(nombre, set()).add(carpeta.name)
    return salida


def _regla_idioma(texto: str) -> str:
    """El texto de la regla 'Idioma de los nombres' de AGENTS.md, hasta el siguiente título."""
    inicio = texto.index("**Idioma de los nombres**")
    return texto[inicio : texto.index("\n## ", inicio)]


def documentados(texto_agents: str) -> set[str]:
    """Los identificadores entre comillas invertidas de la regla de idioma."""
    return set(re.findall(r"`([A-Za-z_][\w.]*)`", _regla_idioma(texto_agents)))


def propios_declarados(texto_agents: str) -> set[str]:
    """Los nombres de las líneas 'API propia con inglés' de la regla de idioma."""
    lineas = [ln for ln in _regla_idioma(texto_agents).splitlines() if "API propia con inglés" in ln]
    return {n for ln in lineas for n in re.findall(r"`([A-Za-z_][\w.]*)`", ln)}


def sin_documentar(publicos: dict[str, set[str]], documentadas: set[str]) -> list[str]:
    return sorted(n for n in publicos if es_ingles(n.removesuffix(".py")) and n not in documentadas)


def test_parte_los_identificadores_en_palabras():
    assert palabras("NoEsPlatformAdmin") == ["no", "es", "platform", "admin"]
    assert palabras("tenant_tx") == ["tenant", "tx"]
    assert palabras("LIMITE_BODY_POR_DEFECTO") == ["limite", "body", "por", "defecto"]
    assert palabras("normalizar_e164") == ["normalizar", "e", "164"]


def test_reconoce_el_ingles_y_deja_pasar_el_espanol_y_los_tecnicismos():
    for nombre in ("tenant_tx", "send_message", "BotAdapter", "MiddlewareLimiteBody", "listar_bookings"):
        assert es_ingles(nombre), nombre
    for nombre in ("crear_turno", "ConfigInvalida", "verificar_mfa", "limites_desde_entorno", "cuerpo_error"):
        assert not es_ingles(nombre), nombre


def test_caso_negativo_un_nombre_en_ingles_nuevo_sin_documentar_falla(tmp_path):
    paquete = tmp_path / "apps" / "shared" / "turnos_ejemplo" / "turnos_ejemplo"
    paquete.mkdir(parents=True)
    (paquete / "__init__.py").write_text('from .envio import enviar\n\n__all__ = ["enviar"]\n', encoding="utf-8")
    (paquete / "envio.py").write_text(
        "def enviar():\n    pass\n\n\ndef send_notification():\n    pass\n\n\nclass ColaDeEnvios:\n"
        "    def vaciar(self):\n        pass\n",
        encoding="utf-8",
    )
    publicos = nombres_publicos(tmp_path)
    assert {"enviar", "envio.py", "send_notification", "ColaDeEnvios", "vaciar"} <= set(publicos)
    assert sin_documentar(publicos, set()) == ["send_notification"]
    assert sin_documentar(publicos, {"send_notification"}) == []


def test_los_nombres_en_ingles_de_la_api_publica_estan_documentados():
    publicos = nombres_publicos(RAIZ)
    assert len(publicos) > 300, "el barrido no encontró los paquetes"
    faltan = sin_documentar(publicos, documentados(AGENTS.read_text(encoding="utf-8")))
    assert not faltan, (
        f"Nombres públicos en inglés sin documentar en la regla 'Idioma de los nombres' de AGENTS.md: {faltan}. "
        "Se renombran al español o se agregan a la regla con su motivo."
    )


def test_la_api_propia_con_ingles_declarada_existe():
    declarados = propios_declarados(AGENTS.read_text(encoding="utf-8"))
    sobran = sorted(declarados - set(nombres_publicos(RAIZ)))
    assert declarados and not sobran, f"Excepciones de AGENTS.md que ya no existen en la API pública: {sobran}"
