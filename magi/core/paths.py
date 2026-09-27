"""
Anclaje de rutas (Plan MAGI 9.0 §1.3).

Sustituye las 8 apariciones de "D:/PROYECTOS/MAGI System IDE" que hacían que
el ejecutable publicado en Releases solo funcionara en la máquina del autor.

Toda ruta del sistema se resuelve aquí y solo aquí.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
import uuid
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "project_root", "data_dir", "workspace_dir", "journal_dir",
    "db_path", "logs_dir", "cache_dir", "is_frozen", "describe",
    "python_executable", "pytest_argv", "escritorio", "exe_dir",
]

_ENV_ROOT = "MAGI_ROOT"
_ENV_DATA = "MAGI_DATA_DIR"
_ENV_WORKSPACE = "MAGI_WORKSPACE"


def is_frozen() -> bool:
    """True si corremos dentro de un bundle de PyInstaller."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def exe_dir() -> Path:
    """Directorio del binario en marcha. Congelado, es el que contiene el
    .exe (sys.executable es el propio binario: por eso esto se resuelve
    aquí y no se toca sys.executable fuera de paths.py). En desarrollo,
    el del intérprete — nadie depende de él en ese caso."""
    return Path(sys.executable).resolve().parent


@lru_cache(maxsize=1)
def python_executable() -> str | None:
    """
    Un intérprete de Python de VERDAD, o `None` si no hay ninguno.

    EL FALLO QUE ESTO CIERRA, y que solo existe en el binario publicado.
    Dentro de un onefile de PyInstaller, `sys.executable` **es el propio
    .exe**, no un intérprete. Comprobado:

        sys.executable = /tmp/pyi-p/d/probe
        frozen = True

    Media docena de sitios lanzaban `[sys.executable, "-m", "pytest", ...]` o
    `"{sys.executable}" "juego.py"`. En desarrollo funciona porque
    `sys.executable` sí es python. En el .exe que se descarga de Releases,
    cada una de esas llamadas **relanza MAGI entero**:

      · `run_test_suite` y `_local_build` (la puerta previa a publicar):
        Magisys.exe -m pytest -> arranca otra GUI y otro servidor.
      · `observe_program`, `observe_game`, `capture_program`: el bucle de
        observación del §5 acababa mirando a MAGI en vez de al artefacto que
        acababa de generar.

    Ninguno daba error: daban resultados de otro programa. Que es peor.

    Devuelve `None` en vez de caer a `sys.executable` a propósito: quien no
    tenga Python instalado necesita que se lo digan, no que el sistema haga
    algo raro en silencio. Quinta regla del proyecto.
    """
    if not is_frozen():
        return sys.executable

    import shutil
    import subprocess

    for nombre in ("python3", "python"):
        ruta = shutil.which(nombre)
        if ruta and Path(ruta).resolve() != Path(sys.executable).resolve():
            return ruta

    # Windows: el lanzador `py` existe aunque `python` no esté en el PATH.
    if sys.platform == "win32":
        lanzador = shutil.which("py")
        if lanzador:
            try:
                r = subprocess.run([lanzador, "-3", "-c",
                                    "import sys; print(sys.executable)"],
                                   capture_output=True, text=True, timeout=15)
                salida = r.stdout.strip()
                if r.returncode == 0 and salida and Path(salida).exists():
                    return salida
            except Exception:
                pass

    # Si estamos congelados y no hay Python del sistema, intentar el embebido
    # que el bundle puede llevar consigo (Plan MAGI 9.0 §1.3).
    try:
        from .embedded_python import embedded_python_executable
        embebido = embedded_python_executable()
        if embebido and Path(embebido).resolve() != Path(sys.executable).resolve():
            return embebido
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def project_root() -> Path:
    """
    Raíz del proyecto MAGI.

    - Bajo PyInstaller: el directorio temporal de extracción (sys._MEIPASS).
    - En desarrollo: dos niveles por encima de este fichero (magi/core/paths.py).
    - Sobrescribible con MAGI_ROOT para tests y despliegues raros.
    """
    override = os.environ.get(_ENV_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """
    Directorio de datos persistentes del usuario (BD, journal, logs, caché).

    Nunca dentro del bundle: un .exe onefile se extrae en un temporal que se
    borra al salir, así que escribir ahí perdería la base de datos.
    """
    override = os.environ.get(_ENV_DATA)
    if override:
        p = Path(override).expanduser().resolve()
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        p = Path(base) / "MagiSystem"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "MagiSystem"
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        p = Path(base) / "magi"
    p.mkdir(parents=True, exist_ok=True)
    return p


@lru_cache(maxsize=1)
def workspace_dir() -> Path:
    """Donde MAGI construye proyectos (antes: .../scratch en una ruta absoluta)."""
    override = os.environ.get(_ENV_WORKSPACE)
    p = Path(override).expanduser().resolve() if override else data_dir() / "workspace"
    p.mkdir(parents=True, exist_ok=True)
    return p


def journal_dir() -> Path:
    """Journal de escrituras para deshacer (§4.2)."""
    p = data_dir() / "journal"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    """
    Ruta de magi_brain.db.

    Antes vivía en el CWD y acabó commiteada al repositorio con datos reales
    dentro. Ahora vive en el directorio de datos del usuario.
    """
    return data_dir() / "magi_brain.db"


def pytest_argv(path: str = "tests", *extra: str) -> list[str] | None:
    """
    Orden para lanzar pytest CON SU PROPIO directorio temporal.

    EL FALLO QUE ESTO CIERRA
    ========================
    MAGI lanza pytest desde tres sitios: la herramienta `run_tests` que usa
    Balthasar para criticar habiendo ejecutado, la verificación de Naoko antes
    de reparar, y la compuerta de publicación. Los tres invocaban `pytest` a
    secas, así que los tres compartían el directorio temporal por defecto.

    pytest guarda sus `tmp_path` en `<temp>/pytest-of-<usuario>/pytest-N`, y al
    arrancar BORRA las corridas antiguas para no dejar basura. Con dos procesos
    a la vez —Naoko verificando mientras el usuario corre la suite, o dos
    reparaciones solapadas— el segundo borra el directorio del primero mientras
    lo está usando. El resultado, medido en la máquina del usuario:

        732 ERROR ... FileNotFoundError: [WinError 3] No se puede encontrar la
        ruta: 'C:\\...\\Temp\\pytest-of-D\\pytest-2'

    Todos los tests que usan `tmp_path`, que son casi todos. Y lo que Naoko
    concluye de eso es lo peor del asunto:

        [naoko] la suite ya estaba roja antes de tocar nada

    Un diagnóstico falso que la deja sin reparar nada, causado por la propia
    verificación. El instrumento de medida rompiendo lo medido — que es
    exactamente contra lo que avisa la regla de oro de la telemetría.

    Cada corrida recibe ahora un directorio propio bajo el de datos de MAGI, con
    el PID en el nombre. No pueden colisionar.
    """
    interprete = python_executable()
    if interprete is None:
        return None

    base = data_dir() / "pytest-tmp"
    base.mkdir(parents=True, exist_ok=True)
    _poda_temporales(base)

    # El sufijo es un uuid, no una marca de tiempo. La primera versión usaba
    # milisegundos y el runner de Linux la tumbó al primer intento: dos
    # llamadas seguidas caían en el mismo milisegundo y devolvían el MISMO
    # directorio. Un aislamiento que depende de que el reloj tenga suficiente
    # resolución no es aislamiento, es una probabilidad — y la que falla es
    # justo la máquina rápida, que es donde más se solapan las corridas.
    propio = base / f"run-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return [interprete, "-m", "pytest", path, "-q", "--no-header",
            f"--basetemp={propio}", *extra]


def _poda_temporales(base: Path, max_edad_s: int = 6 * 3600) -> None:
    """
    Borra los directorios de corridas viejas.

    Con `--basetemp` explícito, pytest deja de rotar y limpiar por su cuenta:
    o los quita alguien, o crecen para siempre. Se hace aquí y no en el
    llamante porque un llamante que se olvide de limpiar no da error, solo va
    llenando el disco — el tipo de fallo que solo se nota meses después.
    """
    try:
        corte = time.time() - max_edad_s
        for d in base.iterdir():
            if d.is_dir() and d.stat().st_mtime < corte:
                shutil.rmtree(d, ignore_errors=True)
    except OSError as e:                                  # pragma: no cover
        # Limpiar nunca puede impedir correr los tests.
        logger.debug("[paths] no se pudieron podar temporales de pytest: %s", e)


def escritorio() -> Path | None:
    """
    El Escritorio real del usuario (entrega de artefactos, Plan §B1).

    En Windows se pregunta al shell (SHGetKnownFolderPath con FOLDERID_Desktop),
    que responde con la carpeta VERDADERA — la de OneDrive si el usuario la
    redirigió allí, que es exactamente el caso que rompería una entrega a
    `Path.home()/Desktop`: la copia iría a un Escritorio espejo que el usuario
    no ve, y el evento diría "entregado" sin haberlo entregado.

    Devuelve `None` si no hay Escritorio accesible; el llamante decide entonces
    dónde caer (workspace/entregas). `MAGI_DESKTOP` sobrescribe la ruta para
    pruebas y despliegues raros.
    """
    override = os.environ.get("MAGI_DESKTOP")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        try:
            from ctypes import byref, c_wchar_p, windll, wintypes

            # FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99FA87E641}
            GUID = getattr(wintypes, "GUID", None)
            if GUID is not None:
                guid = GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            b"\xB0\x29\x7F\xE9\x9F\xA8\x7E\x64\x01")
                puntero = c_wchar_p()
                if windll.shell32.SHGetKnownFolderPath(
                        byref(guid), 0, None, byref(puntero)) == 0 and puntero.value:
                    try:
                        ruta = Path(puntero.value)
                    finally:
                        windll.ole32.CoTaskMemFree(puntero)
                    return ruta if ruta.is_dir() else None
        except Exception:
            pass
        base = os.environ.get("USERPROFILE") or str(Path.home())
        p = Path(base) / "Desktop"
        return p if p.is_dir() else None
    p = Path.home() / "Desktop"
    return p if p.is_dir() else None


def describe() -> dict:
    """Volcado para el bloque de contexto de ejecución y para diagnóstico."""
    return {
        "project_root": str(project_root()),
        "data_dir": str(data_dir()),
        "workspace_dir": str(workspace_dir()),
        "db_path": str(db_path()),
        "frozen": is_frozen(),
        "platform": sys.platform,
    }
