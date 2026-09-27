"""
LA COMPUERTA: el release prueba EXACTAMENTE lo que publica (v5.28.0).

Hasta v5.27.1, el job que aprobaba un release corría en ubuntu — donde los
tests que compilan un .exe se saltan solos — con requirements.txt flotante,
mientras el binario se compilaba en windows desde requirements.lock. Verde
sin probar lo publicado. Este test congela el CONTRATO del workflow para que
debilitarlo cueste un CI rojo, no un descubrimiento del usuario.

Es el mismo patrón que test_wiring.py aplica a los requirements: el workflow
es código, y el código que decide si se publica no puede ser el único sin
revisar.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _release() -> str:
    return (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8")


def _build_job() -> str:
    """La sección del job `build:` (el que compila y publica)."""
    texto = _release()
    idx = texto.find("\n  build:")
    assert idx >= 0, "release.yml no tiene job build"
    return texto[idx:]


def test_la_suite_del_release_corre_sobre_el_lock_en_windows():
    build = _build_job()
    assert "windows-latest" in build
    assert "requirements.lock" in build, (
        "el binario se compila desde el lock: la suite que lo aprueba "
        "tiene que correr sobre ESE lock, no sobre requirements.txt")
    assert '-m "not slow"' in build or "-m 'not slow'" in build, (
        "la suite rápida debe correr en el job que compila")


def test_el_binario_se_ejecuta_antes_de_publicarse():
    build = _build_job()
    assert "--selftest" in build, (
        "sin smoke test del .exe, «compila» no implica «arranca»: el suelo "
        "de tamaño es un proxy, y los proxies mienten (ver historial)")


def test_ningun_paso_del_release_puede_fingir_verde():
    assert "continue-on-error" not in _release(), (
        "un paso no bloqueante en el release es un check que no comprueba")


def test_el_asset_lleva_la_version_en_el_nombre():
    build = _build_job()
    assert "Magisys-v${{ env.VERSION }}-win64.zip" in build, (
        "el zip debe identificarse con la versión: MAGI-IDE-v5.zip en cada "
        "release no distinguía nada")


def test_el_tag_tiene_que_coincidir_con_la_version_de_pyproject():
    texto = _release()
    assert "fuente de verdad" in texto, (
        "el gate tag==versión de pyproject es bloqueante; sin él, pyproject "
        "5.27.0 y tag v5.27.1 conviven sin que nada lo diga (ya pasó)")


def test_pyproject_y_release_notes_dicen_lo_mismo():
    """Una versión, una fuente de verdad: si pyproject dice X, las notas
    tienen la sección de X (es lo que el release publica como body)."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
    notas = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert re.search(rf"^# v{re.escape(version)}\b", notas, re.M), (
        f"pyproject dice {version} y RELEASE_NOTES.md no tiene sección "
        f"'# v{version}': el release publicaría notas de otra versión")


def test_el_ci_no_deja_colgar_un_runner_sin_techar():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    # Un job = trozo que empieza con "  nombre:" y contiene runs-on. El split
    # por líneas de 2 espacios también corta las claves de `concurrency`
    # (group:, cancel-in-progress:), que no son jobs — la primera versión de
    # este test las contaba como jobs sin timeout y fallaba siempre.
    trozos = re.split(r"\n  (?=\w[\w-]*:)", ci)
    jobs = [t for t in trozos if "runs-on:" in t]
    assert jobs, "no se encontró ningún job en ci.yml (¿parser roto?)"
    for trozo in jobs:
        nombre = trozo.split(":", 1)[0].strip()
        assert "timeout-minutes" in trozo, (
            f"el job '{nombre}' de ci.yml no tiene timeout-minutes: un "
            f"cuelgue de runner consume 360 min del plan Free si nada lo corta")


def test_las_notas_del_release_se_extraen_por_tag():
    build = _build_job()
    assert "NOTAS.md" in build, (
        "el body del release debe ser la sección del tag, no RELEASE_NOTES.md "
        "entero (48 KB en cada release: nadie distingue qué cambió en esta)")
