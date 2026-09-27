"""
RESCATE DE RELEASE — publica SIN Actions. NO es la vía normal (v5.28.0).

LA VÍA NORMAL ES EL TAG: `git tag vX.Y.Z && git push origin vX.Y.Z`, y
`release.yml` compila, prueba (suite sobre el lock en Windows + `--selftest`
del binario) y publica. Este script existe para UN caso: Actions
indisponible (cuota agotada, GitHub caído) y necesitas publicar HOY.

Se llama "rescate" —antes era `publicar.py`— para que nadie lo confunda con
la receta: dos recetas para el mismo binario es una que se queda atrás, y ya
pasó (los releases v5.26-v5.27.1 salieron por esta vía local y el workflow de
Actions derivó sin que nada lo dijera).

POR QUÉ EXISTE
==============
El 13-ago el CI dejó de arrancar: repositorio privado y límite de gasto
alcanzado. Con él se cayó también la única vía para publicar, porque el .exe se
compilaba en un runner de Windows.

Pero publicar no necesita Actions. Necesita tres cosas —compilar, comprobar y
subir— y las tres se pueden hacer aquí:

  · compilar   -> PyInstaller, en tu máquina, con el MISMO `.spec` que el CI
  · comprobar  -> `scripts/verificar.py --todo`, los mismos comandos que el CI
  · subir      -> `gh release create`, que usa la API REST de GitHub

**La API REST no consume minutos de Actions.** Se factura el cómputo de los
runners, no las llamadas al API. Así que esta vía es gratis incluso con el
límite de gasto agotado.

QUÉ NO HACE, Y ES DELIBERADO
============================
No relaja ni una comprobación del CI. Todo lo que `release.yml` exige antes de
publicar se exige aquí, incluidas las tres que un .exe puede fallar SIN dar
error al arrancar —y que por eso son las peligrosas—:

  1. que `magi/data/catalogo_proveedores.json` exista;
  2. que el Python embebido entrara DE VERDAD en el binario (se lee el
     inventario de PyInstaller, no el disco: que el fichero esté en el repo no
     prueba que viajara dentro);
  3. que el .exe pese al menos 100 MB — un suelo MEDIDO (94,1 sin embebido,
     107,3 con él), no calculado. La primera versión de esa comprobación puso
     120 MB sumando tamaños en disco y tumbó un build correcto, porque dentro
     va comprimido.

Y una que el CI no puede hacer y aquí sí: **avisar si tu entorno no coincide
con `requirements.lock`**. El .exe del CI se compila desde el lock; el de aquí,
desde lo que tengas instalado. Si difieren, el binario publicado no es el que se
probó — y una versión que no se puede reproducir no se puede depurar.

USO
===
    python scripts/publicar.py v5.4.0              # comprueba, compila, sube
    python scripts/publicar.py v5.4.0 --ensayo     # todo menos subir
    python scripts/publicar.py v5.4.0 --sin-tests  # si ya los pasaste

REQUISITOS
==========
`gh` autenticado (`gh auth status`). Nada más: ni tokens en ficheros ni
secretos que copiar.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SPEC = RAIZ / "Magisys.spec"
EXE = RAIZ / "dist" / "Magisys.exe"
ZIP = RAIZ / "dist" / "Magisys.zip"
TOC = RAIZ / "build" / "Magisys" / "Analysis-00.toc"
NOTAS = RAIZ / "RELEASE_NOTES.md"

#: Suelo de tamaño del .exe, en MB. MEDIDO, no calculado: 94,1 sin el Python
#: embebido (v5.2.0) y 107,3 con él (v5.3.0). 100 queda entre los dos.
MINIMO_MB = 100


def _entorno() -> dict:
    """
    El entorno de los subprocesos se DECIDE, no se hereda.

    ESTO NO ES PARANOIA: PASÓ.
    ==========================
    La máquina de desarrollo tiene `NODE_ENV=production` y `npm config
    omit=dev`. Con eso, `npm ci` instala 140 paquetes y **se salta todas las
    devDependencies** — TypeScript, Vite y Vitest incluidos. El build muere con

        "tsc" no se reconoce como un comando interno o externo

    …y parece un problema del proyecto. No lo es: el CI no lleva esa variable
    y allí funciona. Es una diferencia invisible entre dos máquinas, que es la
    peor clase de diferencia porque nadie la busca.

    Se limpia aquí, para todos los subprocesos, en vez de confiar en que quien
    compile tenga su shell configurado de una forma concreta.
    """
    import os

    e = dict(os.environ)
    e.pop("NODE_ENV", None)          # que npm no crea que es un despliegue
    e["NPM_CONFIG_OMIT"] = ""        # ni que sobran las dependencias de desarrollo
    e["NPM_CONFIG_PRODUCTION"] = ""
    e["PYTHONUTF8"] = "1"            # y que nada reviente por un acento
    return e


def plegar(t: str) -> str:
    """ASCII imprimible en cualquier consola. Quinta vez que cp1252 muerde."""
    d = unicodedata.normalize("NFKD", t)
    return "".join(c for c in d if not unicodedata.combining(c)).encode(
        "ascii", "replace").decode("ascii")


def di(t: str = "") -> None:
    print(plegar(t), flush=True)


def correr(orden: list[str], *, cwd: Path = RAIZ, titulo: str = "") -> bool:
    di(f"\n=== {titulo or ' '.join(orden[:3])} ===")
    r = subprocess.run(orden, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_entorno())
    salida = (r.stdout or "") + (r.stderr or "")
    for ln in [x for x in salida.splitlines() if x.strip()][-10:]:
        di("    " + ln[:150])
    return r.returncode == 0


# ------------------------------------------------------- reproducibilidad

def _directas() -> set[str]:
    fuera = set()
    for ln in (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)", ln)
        if m:
            fuera.add(m.group(1).lower().replace("_", "-"))
    return fuera


def divergencias_con_el_lock() -> list[tuple[str, str, str]]:
    """
    `(paquete, instalado, en el lock)` para las dependencias DIRECTAS que no
    coinciden. Solo las directas: las transitivas las resuelve pip y exigir que
    cuadren todas convertiría esto en un test de pip, no del proyecto.
    """
    directas = _directas()
    fuera: list[tuple[str, str, str]] = []
    for ln in (RAIZ / "requirements.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9._-]+)==([^\s;]+)", ln)
        if not m:
            continue
        nombre = m.group(1).lower().replace("_", "-")
        if nombre not in directas:
            continue
        try:
            inst = version(nombre)
        except PackageNotFoundError:
            fuera.append((nombre, "NO INSTALADO", m.group(2)))
            continue
        if inst != m.group(2):
            fuera.append((nombre, inst, m.group(2)))
    return fuera


# ------------------------------------------------------------ el proceso

def tag_existe(tag: str) -> bool:
    r = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                       cwd=RAIZ, capture_output=True, text=True)
    return r.returncode == 0


def comprobar_integridad() -> list[str]:
    """Las tres cosas que un .exe puede perder sin dar error al arrancar."""
    fallos: list[str] = []

    if not (RAIZ / "magi/data/catalogo_proveedores.json").is_file():
        fallos.append("falta magi/data/catalogo_proveedores.json")

    if not (RAIZ / "assets/python-embed/extracted/python.exe").is_file():
        fallos.append("falta el Python embebido: el .exe no podria ejecutar nada")

    if not EXE.is_file():
        fallos.append("PyInstaller no produjo el .exe")
        return fallos

    # Que esté en el repo NO prueba que viajara dentro. Se lee el inventario
    # que deja PyInstaller, que es evidencia directa de lo empaquetado.
    if not TOC.is_file():
        fallos.append(f"no encuentro el inventario {TOC.name}")
    elif "python-embed" not in TOC.read_text(encoding="utf-8", errors="replace"):
        fallos.append("el Python embebido NO entro en el binario")

    mb = round(EXE.stat().st_size / (1024 * 1024), 1)
    di(f"    exe: {mb} MB")
    if mb < MINIMO_MB:
        fallos.append(f"el .exe pesa {mb} MB (minimo {MINIMO_MB}): falta algo")
    return fallos


def main() -> int:
    ap = argparse.ArgumentParser(description="Publica sin gastar Actions.")
    ap.add_argument("tag", help="etiqueta a publicar, p. ej. v5.4.0")
    ap.add_argument("--ensayo", action="store_true",
                    help="hace todo menos subir el release")
    ap.add_argument("--sin-tests", action="store_true",
                    help="salta la suite (solo si ya la pasaste)")
    ap.add_argument("--igual-da", action="store_true",
                    help="publica aunque el entorno no coincida con el lock")
    args = ap.parse_args()

    t0 = time.perf_counter()
    di(f"Publicando {args.tag} desde esta maquina, sin minutos de Actions.")

    # ---- 0. el tag
    if not tag_existe(args.tag):
        di(f"\nEl tag {args.tag} no existe. Crealo primero:")
        di(f"    git tag -a {args.tag} -m \"...\" && git push origin {args.tag}")
        return 1

    # ---- 1. reproducibilidad
    di("\n=== entorno frente a requirements.lock ===")
    divs = divergencias_con_el_lock()
    if divs:
        for p, inst, lock in divs:
            di(f"    {p:22} instalado {inst:16} lock {lock}")
        di(f"\n    {len(divs)} dependencia(s) DIRECTA(s) no coinciden con el lock.")
        di("    El .exe que compiles aqui NO sera el que el lock describe, y")
        di("    una version publicada que no se puede reproducir no se puede")
        di("    depurar. Dos salidas:")
        di("      1) pip install -r requirements.lock   (alinea el entorno)")
        di("      2) regenera el lock desde lo que tienes y pruebalo:")
        di("         python -m piptools compile --strip-extras "
           "-o requirements.lock requirements.txt")
        if not args.igual_da:
            return 1
        di("    --igual-da: sigo, pero queda dicho.")
    else:
        di("    coincide: el binario sera reproducible")

    # ---- 2. la suite entera, incluidos los que compilan
    if not args.sin_tests:
        if not correr([sys.executable, str(RAIZ / "scripts/verificar.py"), "--todo"],
                      titulo="suite completa (verificar.py --todo)"):
            di("\nTests en rojo. Sin tests verdes no hay release.")
            return 1

    # ---- 3. frontend, con `npm ci` (del lock, como el CI)
    npm = shutil.which("npm") or shutil.which("npm.cmd") or "npm"
    gui = RAIZ / "magi-gui"
    # `--include=dev` explícito, además de limpiar el entorno: dos capas para
    # lo mismo, porque `npm ci` sin las dependencias de desarrollo produce un
    # árbol que parece completo y no compila.
    if not correr([npm, "ci", "--include=dev"], cwd=gui,
                  titulo="frontend: npm ci (con dependencias de desarrollo)"):
        return 1
    if not correr([npm, "run", "build"], cwd=gui, titulo="frontend: build"):
        return 1

    # ---- 4. el binario, DESDE EL .spec (una sola receta, como el CI)
    if not correr([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                   str(SPEC)], titulo="PyInstaller (desde el .spec)"):
        return 1

    # ---- 5. las tres comprobaciones que importan
    di("\n=== que lo que hace falta viajo dentro ===")
    fallos = comprobar_integridad()
    if fallos:
        for f in fallos:
            di(f"    FALLA: {f}")
        return 1
    di("    python embebido: dentro del binario (verificado en el inventario)")

    # ---- 6. el zip
    ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(EXE, EXE.name)
    di(f"\n    zip: {round(ZIP.stat().st_size / (1024 * 1024), 1)} MB")

    # ---- 7. subir, por la API REST (esto NO consume minutos de Actions)
    if args.ensayo:
        di(f"\n--ensayo: todo listo y NO se ha subido nada.\n    {ZIP}")
        di(f"    total: {time.perf_counter() - t0:.0f}s")
        return 0

    orden = ["gh", "release", "create", args.tag, str(ZIP),
             "--title", f"Magisys {args.tag}"]
    if NOTAS.is_file():
        orden += ["--notes-file", str(NOTAS)]
    else:
        orden += ["--generate-notes"]

    if not correr(orden, titulo="gh release create"):
        # Si ya existía, se sube el fichero al release existente en vez de
        # fallar: repetir una publicación es lo más normal del mundo cuando
        # algo salió mal la primera vez.
        di("\n    el release ya existia; subiendo el fichero encima")
        if not correr(["gh", "release", "upload", args.tag, str(ZIP), "--clobber"],
                      titulo="gh release upload"):
            return 1

    di(f"\nPublicado {args.tag} en {time.perf_counter() - t0:.0f}s, "
       f"sin gastar minutos de Actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
