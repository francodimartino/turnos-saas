"""`platform/admin-frontend-template` es una copia de `apps/admin-frontend` (la única fuente) hecha por
`scripts/sincronizar_admin_frontend.py`. Si difieren, el CI falla.

Como la plantilla es idéntica a la fuente, el CI y `scripts/verificar.py` solo lintean, tipan, testean y construyen
la fuente: construir la copia sería repetir el mismo código.
"""

import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar():
    spec = importlib.util.spec_from_file_location("sincronizar", RAIZ / "scripts" / "sincronizar_admin_frontend.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


sync = _cargar()


def test_la_plantilla_coincide_con_la_fuente():
    problemas = sync.diferencias(RAIZ / sync.FUENTE, RAIZ / sync.PLANTILLA)
    assert problemas == [], f"Resincronizá con: {sync.COMANDO}\n" + "\n".join(problemas)


def test_lockfile_y_script_de_tipos_estan_en_las_dos_carpetas():
    for nombre in ("package-lock.json", "package.json", "scripts/tipos-api.mjs", "README.md", "vercel.json"):
        assert (RAIZ / sync.FUENTE / nombre).read_bytes() == (RAIZ / sync.PLANTILLA / nombre).read_bytes(), nombre


def test_las_exclusiones_declaran_su_motivo_y_existen_en_la_fuente():
    for ruta, motivo in sync.EXCLUSIONES.items():
        assert motivo.strip(), ruta
        assert (RAIZ / sync.FUENTE / ruta).exists(), ruta


def _copias(tmp_path):
    fuente, plantilla = tmp_path / "fuente", tmp_path / "plantilla"
    (fuente / "src").mkdir(parents=True)
    (fuente / "node_modules" / "x").mkdir(parents=True)
    (fuente / "src" / "a.ts").write_text("a", encoding="utf-8")
    (fuente / "package.json").write_text("{}", encoding="utf-8")
    (fuente / "node_modules" / "x" / "i.js").write_text("ignorado", encoding="utf-8")
    sync.sincronizar(fuente, plantilla)
    return fuente, plantilla


def test_sincronizar_copia_todo_menos_lo_ignorado(tmp_path):
    fuente, plantilla = _copias(tmp_path)
    assert sync.diferencias(fuente, plantilla) == []
    assert (plantilla / "src" / "a.ts").read_text(encoding="utf-8") == "a"
    assert not (plantilla / "node_modules").exists()


def test_detecta_archivo_distinto_faltante_y_sobrante(tmp_path):
    fuente, plantilla = _copias(tmp_path)
    (plantilla / "src" / "a.ts").write_text("cambiado", encoding="utf-8")
    assert sync.diferencias(fuente, plantilla) == ["distinto: src/a.ts"]
    (plantilla / "package.json").unlink()
    (plantilla / "extra.ts").write_text("x", encoding="utf-8")
    assert sync.diferencias(fuente, plantilla) == [
        "falta en la plantilla: package.json",
        "sobra en la plantilla: extra.ts",
        "distinto: src/a.ts",
    ]


def test_sincronizar_repara_la_desincronizacion(tmp_path):
    fuente, plantilla = _copias(tmp_path)
    (plantilla / "src" / "a.ts").write_text("cambiado", encoding="utf-8")
    (plantilla / "sobra").mkdir()
    (plantilla / "sobra" / "b.ts").write_text("x", encoding="utf-8")
    sync.sincronizar(fuente, plantilla)
    assert sync.diferencias(fuente, plantilla) == []
    assert not (plantilla / "sobra").exists()


def test_una_exclusion_no_se_copia_ni_cuenta_como_sobrante(tmp_path, monkeypatch):
    fuente, plantilla = _copias(tmp_path)
    (fuente / "propio.md").write_text("solo fuente", encoding="utf-8")
    monkeypatch.setitem(sync.EXCLUSIONES, "propio.md", "propio de la fuente")
    assert sync.diferencias(fuente, plantilla) == []
    sync.sincronizar(fuente, plantilla)
    assert not (plantilla / "propio.md").exists()


def test_el_mensaje_del_script_indica_el_comando(tmp_path, monkeypatch, capsys):
    _, plantilla = _copias(tmp_path)
    (plantilla / "src" / "a.ts").write_text("cambiado", encoding="utf-8")
    monkeypatch.setattr(sync, "RAIZ", tmp_path)
    monkeypatch.setattr(sync, "FUENTE", Path("fuente"))
    monkeypatch.setattr(sync, "PLANTILLA", Path("plantilla"))
    assert sync.main(["--verificar"]) == 1
    assert sync.COMANDO in capsys.readouterr().out
    assert sync.main([]) == 0
    assert sync.main(["--verificar"]) == 0
