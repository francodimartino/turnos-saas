"""Helper unico para lanzar git desde los tests (importable como `git_aislado`).

Con `core.fsmonitor=true` en la config global de git, cada repo temporal que crea un test deja
vivo un `git fsmonitor--daemon`. Por eso todo git de los tests pasa por aca: lleva
`-c core.fsmonitor=false` y un entorno sin `GIT_*` heredado (bajo un hook, apuntarian al repo real).
`test_git_aislado.py` falla si un test lanza git por otro camino.
"""

import os
import shutil
import subprocess
from pathlib import Path

GIT = shutil.which("git")
TIMEOUT = 120


def env_sin_git(base: dict[str, str] | None = None) -> dict[str, str]:
    """Entorno sin variables GIT_* heredadas y con `core.fsmonitor=false` para todo proceso git
    (incluido el que lance un script bash)."""
    origen = os.environ if base is None else base
    env = {k: v for k, v in origen.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    env["GIT_CONFIG_VALUE_0"] = "false"
    return env


def run_git(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    text: bool = True,
    timeout: int = TIMEOUT,
) -> subprocess.CompletedProcess:
    """Ejecuta git con fsmonitor desactivado. `env` por defecto: `env_sin_git()`."""
    assert GIT, "git no esta disponible"
    return subprocess.run(  # noqa: S603 - lista fija, sin shell
        [GIT, "-c", "core.fsmonitor=false", *args],
        cwd=cwd,
        env=env_sin_git() if env is None else env,
        capture_output=True,
        text=text,
        check=check,
        timeout=timeout,
    )
