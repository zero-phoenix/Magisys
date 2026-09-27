"""
Cortafuegos de navegador — la garantía dura de §I.3.

QUÉ PASÓ (3 intentos fallidos antes de este)
============================================
El usuario reportó tres veces que preguntar algo a MAGI abría ventanas de
navegador. Las dos correcciones previas atacaron la ruta equivocada:

  intento 1: filtrar providers con `use_nodriver=True`  -> no sirvió
  intento 2: parchear g4f.requests.get_nodriver/webview -> no sirvió

LA CAUSA REAL, encontrada leyendo el código de g4f 7.9.4:

  g4f/Provider/Cloudflare.py:106-117
      from ..requests.cdp import CDPSession
      session = CDPSession(headless=False)     # <-- headless=False
      await session.start()

  g4f/requests/cdp.py:157-233  get_shared_browser(...)
      cmd = [chrome_path, f"--remote-debugging-port={port}", ...]
      if headless: cmd.append("--headless=new")   # <-- NO se añade
      subprocess.Popen(cmd)                        # <-- VENTANA VISIBLE

Cloudflare declara `use_nodriver = False`, así que pasaba limpiamente el
filtro del intento 1. Y no llama a ninguna de las funciones parcheadas en el
intento 2: importa `CDPSession` DENTRO del método, en tiempo de llamada.

Y Cloudflare era justo el proveedor que respondía en TODOS los logs del
usuario ("[g4f-deepseek] respondió Cloudflare/deepseek-coder-6.7b"). O sea:
cada inferencia con éxito abría una ventana. Lo mismo hace DeepInfra vía
SyncCDPSession. Peor aún, cdp.py:190 se engancha a un Chrome del usuario ya
abierto si encuentra uno con depuración remota.

EL DISEÑO DE ESTA DEFENSA
=========================
No confía en lo que los providers declaran. Corta las rutas de lanzamiento,
y por debajo de todas ellas pone un interruptor a nivel de proceso sobre
subprocess.Popen que ningún código de g4f puede esquivar, presente o futuro.

  capa 1  CDP        - CDPSession/SyncCDPSession/get_shared_browser cortados,
                       find_chrome_path -> None, find_running_cdp_port -> None
                       (para no secuestrar el Chrome del propio usuario)
  capa 2  nodriver/webview - flags a False *y* re-parcheo de las copias que
                       los módulos de provider ya importaron por valor
  capa 3  webbrowser  - webbrowser.open* neutralizado
  capa 4  Popen       - kill switch: ningún binario de navegador se ejecuta

Cada bloqueo queda registrado en `violations()`. Naoko lo lee: la queja del
usuario no fue solo que se abriera el navegador, sino que Naoko no se enteró.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

# Binarios de navegador que jamás debe lanzar MAGI.
_BROWSER_BINARIES = (
    "chrome", "chromium", "msedge", "brave", "opera", "vivaldi",
    "firefox", "helium", "thorium", "iexplore",
)

# Excepción crítica: la GUI de MAGI ES pywebview, que en Windows arranca el
# runtime WebView2 (msedgewebview2.exe). Bloquearlo mataría la propia interfaz.
_ALLOWED_BINARIES = ("msedgewebview2", "webview2", "webview")

_violations: list[dict] = []
_installed = False

#: True mientras `self_test()` se comprueba a sí mismo. Las sondas del propio
#: cortafuegos no son intentos de abrir un navegador y no deben registrarse
#: como tales.
_probing = False


class BrowserBlocked(RuntimeError):
    """MAGI prohíbe abrir navegadores (§I.3)."""


def violations() -> list[dict]:
    """Intentos bloqueados, del más reciente al más antiguo."""
    return list(reversed(_violations))


def violation_count() -> int:
    return len(_violations)


def _record(source: str, detail: str) -> None:
    _violations.append({"source": source, "detail": detail[:400], "ts": time.time()})
    logger.warning("[no_browser] BLOQUEADO %s: %s", source, detail[:200])


#: Aperturas AUTORIZADAS. Se guardan aparte de las violaciones porque son otra
#: cosa: una violación es algo que el sistema intentó a tus espaldas, y esto es
#: algo que tú pediste. Mezclarlas dejaría el registro sin poder distinguir un
#: intento de secuestro de un permiso concedido.
_autorizadas: list[dict] = []


def autorizadas() -> list[dict]:
    """Aperturas permitidas, de la más reciente a la más antigua."""
    return list(reversed(_autorizadas))


def _permitida(source: str, motivo: str) -> None:
    _autorizadas.append({"source": source, "motivo": motivo[:400],
                         "ts": time.time()})
    logger.info("[no_browser] PERMITIDO %s: %s", source, motivo[:200])


def _hay_permiso() -> tuple[bool, str]:
    """
    ¿Hay permiso explícito y vigente del usuario para abrir un navegador?

    LA ÚNICA GRIETA DEL CORTAFUEGOS, Y POR QUÉ EXISTE
    =================================================
    Este módulo no existe porque los navegadores sean malos: existe porque g4f
    abría el Chrome DEL USUARIO, sin avisar, en mitad de una petición. El
    problema era la apertura invisible y no consentida.

    Seis proveedores —Claude, OpenaiChat, Copilot, LMArena, Cloudflare,
    DeepInfra— no pueden responder sin una sesión autenticada. Cerrarles la
    puerta para siempre es renunciar a ellos; abrirla del todo es volver al
    fallo. La grieta es del tamaño exacto del problema:

      · headless, sin ninguna ventana además de la interfaz de MAGI;
      · con perfil PROPIO, nunca el del usuario;
      · solo con permiso explícito y CADUCABLE que concede una acción suya;
      · y cada apertura queda registrada.

    Sin permiso vigente, todo se bloquea exactamente igual que antes. La
    invariante pasa de «no se abre ningún navegador» a «ninguno se abre sin que
    tú lo pidas, y todos quedan registrados», que es más fuerte: la primera se
    cumplía por no poder, y esta se cumple pudiendo.
    """
    try:
        from magi.core import sesion_web
        return sesion_web.puede_abrir()
    except Exception:
        # Sin el módulo de sesión no hay permiso posible. Ante la duda, se
        # bloquea: es la dirección segura del error.
        return False, "el módulo de sesión web no está disponible"


def _is_browser(argv) -> bool:
    """True si este comando lanzaría un navegador visible."""
    if not argv:
        return False
    first = argv[0] if isinstance(argv, (list, tuple)) else argv
    exe = os.path.basename(str(first)).lower()
    if any(a in exe for a in _ALLOWED_BINARIES):
        return False
    if any(b in exe for b in _BROWSER_BINARIES):
        return True
    # Firma inequívoca de automatización CDP, sea cual sea el binario.
    joined = " ".join(str(x) for x in argv) if isinstance(argv, (list, tuple)) else str(argv)
    return "--remote-debugging-port" in joined


# --------------------------------------------------------------- capa 4: Popen

def _install_popen_killswitch() -> None:
    if getattr(subprocess.Popen, "_magi_guarded", False):
        return
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        _magi_guarded = True

        def __init__(self, args, *a, **kw):
            if _is_browser(args):
                argv = args if isinstance(args, (list, tuple)) else [args]
                orden = " ".join(str(x) for x in argv[:3])
                permitido, motivo = _hay_permiso()
                if permitido:
                    _permitida("subprocess.Popen", f"{orden} — {motivo}")
                else:
                    _record("subprocess.Popen", orden)
                    raise BrowserBlocked(
                        "MAGI no abre navegadores sin permiso tuyo (§I.3). "
                        f"Comando bloqueado: {os.path.basename(str(argv[0]))}. "
                        f"Motivo: {motivo}")
            super().__init__(args, *a, **kw)

    subprocess.Popen = GuardedPopen


# ----------------------------------------------------------------- capa 1: CDP

def _install_cdp_block() -> None:
    try:
        from g4f.requests import cdp
    except Exception:
        return

    def _no_chrome(*a, **kw):
        # `_probing` distingue "g4f está buscando Chrome para lanzarlo" de
        # "MAGI se está comprobando a sí misma". Sin esa distinción, cada
        # `self_test()` —que Naoko ejecuta al arrancar y cada 3 minutos—
        # contaba como intento de abrir navegador: el log se llenaba de
        # WARNING y Naoko informaba de intentos que nunca ocurrieron. Avisar
        # de algo que no ha pasado gasta la credibilidad del aviso que sí
        # importa, que es justo lo contrario de para lo que está este módulo.
        if _probing:
            return None
        _record("cdp.find_chrome_path", "búsqueda de binario de navegador")
        return None

    def _no_port(*a, **kw):
        # Devolver None evita que g4f secuestre un Chrome del usuario que ya
        # estuviera abierto con --remote-debugging-port.
        return None

    def _blocked(*a, **kw):
        _record("cdp.get_shared_browser", "lanzamiento de Chrome compartido")
        raise BrowserBlocked("MAGI no abre navegadores (§I.3)")

    async def _blocked_async(*a, **kw):
        _record("CDPSession.start", "arranque de sesión CDP")
        raise BrowserBlocked("MAGI no abre navegadores (§I.3)")

    def _blocked_sync(*a, **kw):
        _record("SyncCDPSession.start_chrome", "arranque de Chrome síncrono")
        raise BrowserBlocked("MAGI no abre navegadores (§I.3)")

    for name, repl in (("find_chrome_path", _no_chrome),
                       ("find_running_cdp_port", _no_port),
                       ("get_shared_browser", _blocked)):
        if hasattr(cdp, name):
            setattr(cdp, name, repl)

    if hasattr(cdp, "CDPSession"):
        cdp.CDPSession.start = _blocked_async  # type: ignore[method-assign]
        if hasattr(cdp.CDPSession, "start_chrome"):
            cdp.CDPSession.start_chrome = _blocked_sync  # type: ignore[method-assign]
    _sync_cls = getattr(cdp, "SyncCDPSession", None)
    if _sync_cls is not None:
        _sync_cls.start_chrome = _blocked_sync
        if hasattr(_sync_cls, "start"):
            _sync_cls.start = _blocked_sync


# ------------------------------------------------- capa 2: nodriver / webview

_BROWSER_FUNCS = (
    "get_nodriver", "get_nodriver_session", "get_args_from_nodriver",
    "get_args_from_browser", "get_args_from_webview", "get_args_from_cdp",
)
_BROWSER_FLAGS = ("has_webview", "has_nodriver", "has_cdp")


def _install_nodriver_block() -> None:
    async def _blocked(*a, **kw):
        _record("g4f.requests", "ruta nodriver/webview")
        raise BrowserBlocked("MAGI no abre navegadores (§I.3)")

    try:
        from g4f import requests as g4f_req
    except Exception:
        return

    for flag in _BROWSER_FLAGS:
        if hasattr(g4f_req, flag):
            setattr(g4f_req, flag, False)
    for fn in _BROWSER_FUNCS:
        if hasattr(g4f_req, fn):
            setattr(g4f_req, fn, _blocked)

    # CLAVE: los módulos de provider hacen `from ..requests import get_nodriver`
    # y `from ..requests import has_nodriver`, que copian el objeto/valor en su
    # propio espacio de nombres al importarse. Parchear g4f.requests DESPUÉS no
    # cambia esas copias — ese fue el fallo del intento 2. Aquí se recorren los
    # módulos g4f ya cargados y se reescriben sus copias una por una.
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith("g4f.") or mod is None:
            continue
        for flag in _BROWSER_FLAGS:
            if getattr(mod, flag, None) is True:
                setattr(mod, flag, False)
        for fn in _BROWSER_FUNCS:
            if callable(getattr(mod, fn, None)):
                setattr(mod, fn, _blocked)


# ---------------------------------------------------------- capa 3: webbrowser

def _install_webbrowser_block() -> None:
    import webbrowser

    if getattr(webbrowser.open, "_magi_guarded", False):
        return

    def _blocked_open(url, *a, **kw):
        _record("webbrowser.open", str(url))
        return False

    _blocked_open._magi_guarded = True  # type: ignore[attr-defined]
    webbrowser.open = _blocked_open
    webbrowser.open_new = _blocked_open
    webbrowser.open_new_tab = _blocked_open


# ---------------------------------------------------------------------- público

def install() -> None:
    """
    Instala el cortafuegos. Idempotente y seguro de llamar antes de que g4f
    esté importado: las capas que dependen de g4f se reaplican en cada llamada,
    así que basta con volver a llamar tras importar g4f (lo hace el backend).
    """
    global _installed
    _install_popen_killswitch()      # no depende de g4f: se instala siempre
    _install_webbrowser_block()
    _install_cdp_block()             # no-op si g4f aún no está importado
    _install_nodriver_block()
    if not _installed:
        _installed = True
        logger.info("[no_browser] cortafuegos §I.3 activo "
                    "(CDP, nodriver, webview, webbrowser, Popen)")


def self_test() -> dict:
    """
    Comprueba que las cuatro capas están puestas. Lo usa Naoko para saber si
    la invariante §I.3 sigue viva en el proceso en marcha.
    """
    global _probing
    report: dict[str, bool] = {"popen": getattr(subprocess.Popen, "_magi_guarded", False)}
    import webbrowser
    report["webbrowser"] = getattr(webbrowser.open, "_magi_guarded", False)
    _probing = True                   # ver `_no_chrome`: esto no es un intento
    try:
        try:
            from g4f.requests import cdp
            report["cdp"] = cdp.find_chrome_path() is None
        except Exception:
            report["cdp"] = True      # g4f/cdp no cargado = nada que abrir
    finally:
        _probing = False
    try:
        from g4f import requests as g4f_req
        report["nodriver"] = not any(getattr(g4f_req, f, False) for f in _BROWSER_FLAGS)
    except Exception:
        report["nodriver"] = True
    report["ok"] = all(v for k, v in report.items() if isinstance(v, bool))
    report["violations"] = violation_count()  # type: ignore[assignment]
    return report
