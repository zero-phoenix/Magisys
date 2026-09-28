"""
Backend g4f con FAMILIA FIJADA — el arreglo del bug central de v5.0.28.

EL BUG
======
magi/core/providers/cloud.py:117-123 (v5.0.28):

    async def generate(self, system_prompt, user_prompt, model="gpt-4o"):
        if model in ["claude-3.5-sonnet", "qwen-2.5", "deepseek"]:
            model = "gpt-4o"

y luego, en _fetch_from_provider(), la llamada iba SIN parámetro `provider`:

    response = await self.client.chat.completions.create(model=cand, messages=[...])

Resultado: los tres nodos del enjambre pedían familias distintas, las tres se
reescribían a gpt-4o, y g4f auto-ruteaba las tres al mismo sitio. Melchior,
Balthasar y Casper eran EL MISMO MODELO con tres prompts. Por eso las críticas
de Balthasar sonaban genéricas: un modelo criticándose a sí mismo encuentra poco.

EL ARREGLO
==========
g4f sí permite fijar proveedor: create(..., provider=g4f.Provider.Qwen).
Cada instancia de G4FProvider representa UNA familia con una cadena de
candidatos (proveedor, modelo). Si un candidato cae, se prueba el siguiente
DENTRO DE LA MISMA FAMILIA — nunca se salta a otra familia en silencio, porque
eso es justo lo que rompía la diversidad.

Todo gratuito, sin claves de API, sin modelos locales (§I.3 del documento de
arquitectura).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator, Iterable
from typing import Any

from ...no_browser import install as install_browser_guard
from ...obs.metrics import emit_provider_metric
from ..base import (
    BaseProvider,
    CompletionRequest,
    CompletionResponse,
    Delta,
    ProviderError,
    ProviderUnavailable,
    Usage,
)
from ..cache import TTLCache
from ..circuit import CalidadBreaker

logger = logging.getLogger(__name__)

# (nombre de proveedor g4f, modelo o None para el por defecto del proveedor)
Candidate = tuple[str, str | None]

# Catálogo por familia.
#
# VERIFICADO EMPÍRICAMENTE, no leído de los metadatos de g4f. El catálogo
# anterior se construyó filtrando por `working=True and needs_auth=False`, que
# es lo que g4f DICE de sí mismo. Al probar los 44 candidatos uno a uno contra
# la red real (2026-08-06, g4f 7.9.4, cortafuegos §I.3 puesto) respondieron 11:
#
#   HuggingSpace              default              890ms   OK
#   Groq                      default              922ms   OK
#   CohereForAI_C4AI_Command  command-a-03-2025   1078ms   OK
#   CopilotApp                default             1156ms   OK
#   AnyProvider               gpt-4o              1671ms   OK
#   Yqcloud                   gpt-4               2000ms   OK
#   WeWordle                  gpt-4o              2389ms   OK
#   Gemini                    gemini-3.5-flash    3421ms   OK
#   Perplexity                auto                7921ms   OK (respuesta pobre)
#   AnyProvider               default             8014ms   OK
#   Ollama                    default             8139ms   EXCLUIDO: es local (§I.3)
#
# Y fallaron, con su motivo real: PhindAi (timeout), Qwen (error de sesión),
# Claude (pide browser_cookie3), LMArena (pide fichero de auth), OpenaiChat y
# Copilot (piden .har), Pollinations (402), GLM (captcha), MetaAI (403),
# GeminiPro (429), Cloudflare y DeepInfra (INTENTARON ABRIR CHROME, bloqueados).
#
# Las familias que se quedaron sin ningún candidato vivo (deepseek, qwen,
# claude, glm) se conservan a propósito: son el mapa de lo que existe, y
# `complete()` las reporta como agotadas en vez de fingir. El reparto del
# enjambre apunta ahora a las tres familias verificadas.
_FAMILY_SPECS_BASE: dict[str, list[Candidate]] = {
    # --- verificadas con medida FECHADA ------------------------------------
    "command": [
        ("CohereForAI_C4AI_Command", "command-a-03-2025"),   # 1809 ms 13-ago
        ("CohereForAI_C4AI_Command", "command-r-plus-08-2024"),
        ("CohereForAI_C4AI_Command", "command-r-plus"),
        ("HuggingSpace", None),                              # 16535 ms 13-ago
    ],
    "claude": [
        # Perplexity sirve Claude sin cuenta ni cookies. Ver compat_g4f.py.
        ("Perplexity", "claude45sonnet"),                    # 3723 ms 13-ago
        ("Perplexity", "claude40opus"),                      # 2832 ms 13-ago
        ("Perplexity", "claude2"),                           # 5072 ms 13-ago
        ("Perplexity", "claude45sonnetthinking"),
        ("Perplexity", "claude37sonnetthinking"),
        ("Perplexity", "claude35haiku"),
    ],
    "gemini": [
        ("Gemini", "gemini-3.5-flash"),                      # 4877 ms 13-ago
        ("Gemini", "gemini-3.1-pro"),
        ("Gemini", "gemini-2.5-flash"),
        ("Perplexity", "gemini2flash"),                      # ruta alternativa
    ],
    "gpt": [
        # MELCHIOR ya NO usa esta familia: su único candidato propio vivo
        # (Yqcloud) responde a veces en chino, y el chino está prohibido.
        # Perplexity la revive por otra puerta, con modelos mejores.
        ("Perplexity", "gpt5"),
        ("Perplexity", "gpt41"),
        ("Perplexity", "gpt4o"),
        ("Yqcloud", "gpt-4"),                                # 11795 ms, ojo chino
        # Vuelve tras el 429 del 13-ago: un limite de ritmo no es una rotura, y
        # ahora hay quien lo gestione (cortacircuitos + hedge por presupuesto).
        # Medido: ~2,4 s, castellano.
        ("WeWordle", "gpt-4o"),
    ],
    "razonamiento": [
        # Familia NUEVA. Los modelos de razonamiento explícito no estaban en el
        # catálogo y son justo lo que BALTHASAR necesita para refutar.
        ("Perplexity", "o3"),
        ("Perplexity", "o4mini"),
        ("Perplexity", "pplx_reasoning"),
    ],
    "grok": [
        ("Perplexity", "grok4"),
        ("Perplexity", "grok"),                              # 4279 ms 13-ago
    ],
    "perplexity": [
        ("Perplexity", "auto"),                              # 5964 ms 13-ago
        ("Perplexity", "turbo"),
    ],
    "llama": [("Perplexity", "llama_x_large")],
    "mistral": [("Perplexity", "mistral")],
    "deepseek": [("Perplexity", "r1")],
    "hf": [("HuggingSpace", None)],                          # 16535 ms 13-ago

    # Último recurso: auto-router de g4f. Familia "auto" para que el registro
    # sepa que NO garantiza diversidad y lo declare en la GUI.
    "auto": [("AnyProvider", "gpt-4o"), ("AnyProvider", "default")],
}

# Familias que RESPONDIERON en el entorno que se publica, medidas de nuevo el
# 2026-08-13 por la tarde. Y la lista cambió respecto a la de por la mañana,
# que es justo lo que hay que aprender de esto.
#
# LA MEDIDA SE HIZO EN UN VENV CONSTRUIDO DESDE `requirements.lock`
# ================================================================
# O sea, con las versiones que lleva el .exe, no con las del equipo de
# desarrollo. La diferencia no era teórica: con el pin viejo
# (`curl_cffi==0.5.10`) Perplexity ni siquiera conectaba —«impersonate chrome
# is not supported»— y ese proveedor sirve TODA la familia `claude`. El binario
# publicado habría llevado esa familia muerta y aquí nadie lo habría notado.
#
# LO MEDIDO, con `curl_cffi>=0.16` ya corregido:
#
#   gemini  Gemini                   3 677 ms  OK, castellano
#   gpt     Yqcloud                  6 592 ms  OK, castellano (esta vez)
#   hf      HuggingSpace            14 323 ms  OK, castellano
#   claude  Perplexity               7 887 ms  INSERVIBLE: 4 caracteres, 'tud.'
#   grok    Perplexity               7 886 ms  INSERVIBLE: idem
#   command CohereForAI                486 ms  HTTP 500
#
# `claude` y `command` estaban las dos entre las verificadas ESTA MISMA MAÑANA.
# Ocho horas. Esa es la vida útil real de una lista escrita a mano, y por eso
# la lista de aquí es solo el arranque en frío: `sonda.medias_por_familia` +
# `ProviderRegistry.aplicar_medidas` la corrigen en caliente con datos
# fechados. Lo que no se puede hacer es fiarse de este literal.
_VERIFICADAS_BASE = ("gemini", "gpt", "hf")

#: Proveedores que NO pueden responder en este entorno, con el motivo medido.
#:
#: No es una lista de «va lento» ni de «a veces falla»: es de «no existe forma
#: de que conteste, y comprobarlo cuesta un turno». El registro del usuario
#: mostraba esto en CADA ronda del enjambre:
#:
#:   PhindAi: BaseSession.__init__() got an unexpected keyword argument 'proxy'
#:   Claude:  MissingRequirementsError: Install "browser_cookie3" package
#:   LMArena: No auth file found and nodriver is not available
#:   Cloudflare: BrowserBlocked: MAGI no abre navegadores (§I.3)
#:
#: Seis intentos condenados antes de llegar a uno vivo. Saltárselos no pierde
#: nada —ninguno podía contestar— y `complete()` sigue reportando la familia
#: como agotada, que es la información verdadera.
#:
#: Cada entrada lleva su motivo para que se pueda revisar. Y se revisa: PhindAi
#: y Qwen estuvieron aquí por «incompatible con la versión instalada de
#: curl_cffi (no acepta 'proxy')», y resultó que no estaban caídos — g4f pasaba
#: el argumento al constructor cuando esa versión ya solo lo admite en
#: `request`. Dos familias enterradas por un argumento de más. Las revive
#: `providers/compat_curl.py` leyendo la firma real de lo que hay instalado.
#:
#: Salir de esta lista NO es lo mismo que estar verificado: significa «ya no
#: revienta antes de intentarlo». Si responde o no lo dirá la sonda.
_ROTOS_BASE: dict[str, str] = {
    # ---- DESCARTADOS: exigen TU CUENTA. Imposibles por diseño, no pendientes.
    #
    # La regla del proyecto es que aquí no se inicia sesión en ningún sitio.
    # Estos cuatro no son «difíciles»: no hay forma honesta de usarlos sin una
    # cuenta tuya, así que salen del catálogo en vez de quedarse como deuda que
    # alguien reintentará cada seis meses.
    "Claude": "DESCARTADO: exige tu cuenta (browser_cookie3 + cookies). Los modelos Claude los sirve Perplexity sin cuenta.",
    "OpenaiChat": "DESCARTADO: exige tu cuenta (.har de sesión). Los modelos GPT los sirve Perplexity.",
    "Copilot": "DESCARTADO: exige tu cuenta (.har de sesión).",
    "LMArena": "DESCARTADO: exige tu cuenta (fichero de autenticación) y nodriver.",

    # ---- DESCARTADOS: solo pueden funcionar abriendo un navegador.
    #
    # Medido el 2026-08-13 con el cortafuegos puesto: los tres devuelven
    # `BrowserBlocked: MAGI no abre navegadores (§I.3)` en 2-5 ms. No es que
    # vayan lentos: es que su única vía está cerrada por la invariante que
    # define este proyecto.
    "Cloudflare": "DESCARTADO: su única vía es CDPSession (abrir Chrome). BrowserBlocked en 2 ms (13-ago).",
    "DeepInfra": "DESCARTADO: SyncCDPSession + token Turnstile (captcha). BrowserBlocked en 3 ms (13-ago).",
    "Pi": "DESCARTADO: abre navegador. BrowserBlocked en 5 ms (13-ago).",

    # ---- DESCARTADOS: captcha. Mismo criterio para todos, sin excepciones.
    "GLM": "DESCARTADO: responde captcha (_CaptchaRequired, medido 13-ago).",

    # ---- DESCARTADOS: piden dinero o están sin cuota.
    "Groq": "DESCARTADO: HTTP 402 «No cake credits» (medido 13-ago).",
    "Pollinations": "DESCARTADO: HTTP 402 Payment Required (medido 13-ago).",
    "GeminiPro": "DESCARTADO: HTTP 429, cuota agotada. Los modelos Gemini los sirve `Gemini`.",

    # ---- DESCARTADOS: bloqueo del servicio, sin vía conocida.
    "MetaAI": "DESCARTADO: HTTP 403 «Fetch home failed» desde esta red (medido 13-ago).",
    "PhindAi": "DESCARTADO: HTTP 403 Security (medido 13-ago). compat_curl arregló la firma de curl_cffi, no el acceso.",
    "Qwen": "DESCARTADO: success=false del servidor (medido 13-ago). Mismo caso que PhindAi.",
    "CopilotApp": "DESCARTADO: WSServerHandshakeError 460 al abrir el websocket (medido 13-ago).",

    # ---- FUERA POR REGLA, no por fallo.
    "Ollama": "DESCARTADO: es un motor LOCAL y §I.3 solo admite nube.",

    # ---- WeWordle VOLVIÓ a la familia `gpt` (v5.5.2). Su 429 del 13-ago era
    # un límite de ritmo, no una rotura, y desde entonces hay quien lo
    # gestione: cortacircuitos por herramienta y hedge con presupuesto por
    # tarea. Se deja escrito aquí, y no borrado, porque la razón por la que
    # algo salió y volvió a entrar es justo lo que nadie recuerda medio año
    # después.
}

#: Margen antes de cubrir una petición lenta con el siguiente candidato.
#: 4 s sale de las latencias medidas: los candidatos sanos contestan entre
#: 0,9 y 3,4 s, así que a los 4 s ya no es "va lento", es "algo pasa".
_HEDGE_AFTER_S_BASE = 4.0

#: Tope de llamadas simultáneas por familia.
#:
#: Antes 2: con un candidato colgado, el hedge lanzaba UN segundo candidato en
#: paralelo y no más. En el registro real del usuario, Yqcloud (familia gpt)
#: tardaba 53-75 s mientras el único cobertura también podía ir lento, así que
#: la etapa entera de Melchior se arrastraba. Con 3, un candidato rápido
#: verificado (CopilotApp ~1,1 s, WeWordle ~2,4 s) puede ganar la carrera
#: mientras los lentos siguen su curso. Sigue siendo gratuito y sin claves
#: (§I.3); el coste extra es una llamada más SOLO cuando el primero no responde
#: en `_HEDGE_AFTER_S_BASE`, no en cada petición.
_HEDGE_MAX_BASE = 3

# Reparto por defecto del enjambre.
#
# Antes: MELCHIOR=deepseek, BALTHASAR=claude, CASPER=qwen. Esas tres familias
# NO tienen hoy ni un candidato vivo (deepseek solo respondía vía Cloudflare,
# o sea abriendo una ventana de Chrome; claude pide cookies de navegador; qwen
# devuelve error de sesión). El enjambre quedaba sin proveedor y caía al
# clasificador por defecto, que es justo lo que se ve en el log del usuario.
#
# Ahora apunta a tres familias verificadas y de linajes realmente distintos
# —OpenAI, Google y Cohere—, que es lo que §1.1 pide de verdad: que el crítico
# tenga sesgos distintos al proponente.
# MEDIDO el 2026-08-13 POR LA TARDE, en el entorno del release. Reglas del
# usuario: la mejor para BALTHASAR, la segunda para CASPER, familia distinta
# cada uno.
#
#   gemini-3.5-flash   3,7 s   -> BALTHASAR
#   Yqcloud gpt-4      6,6 s   -> CASPER
#   HuggingSpace      14,3 s   -> MELCHIOR
#
# Dos avisos honestos sobre esto:
#
# 1. `claude` sale del enjambre pese a ser la prioridad del usuario. No por
#    decisión: Perplexity devuelve cuatro caracteres ('tud.') para cualquier
#    modelo, y `_por_que_es_inservible` lo rechaza. Vuelve en cuanto responda.
#
# 2. `gpt` entra y su candidato vivo es Yqcloud, que A VECES contesta en chino.
#    Esta vez respondió en castellano. Es aceptable únicamente porque la guarda
#    de idioma lo caza y rota (`agents.py`), no porque sea buen candidato.
#
# MELCHIOR se lleva el más lento, que es lo contrario de lo deseable —propone y
# ejecuta en el mismo turno—, y aun así es lo correcto: la regla del usuario
# sobre BALTHASAR y CASPER es explícita y no se salta por conveniencia. Se
# arregla cuando la sonda encuentre algo mejor, no reinterpretando la regla.
_REPARTO_BASE = {
    "BALTHASAR": "gemini",
    "CASPER": "gpt",
    "MELCHIOR": "hf",
}


# ---------------------------------------------------------------------------
# Los nombres públicos salen del CATÁLOGO, no de las constantes de arriba.
#
# Las constantes se conservan íntegras como `_*_BASE` y actúan de respaldo: si
# el JSON falta, no valida o trae un esquema desconocido, estos valores salen
# de ellas y todo funciona exactamente igual que antes.
#
# Lo que se gana: arreglar un proveedor caído pasa de recompilar 158 MB de
# ejecutable a editar `%LOCALAPPDATA%\MagiSystem\catalogo_proveedores.json`.
# Ver `core/providers/catalogo.py`.
# ---------------------------------------------------------------------------
from magi.core.providers.catalogo import catalogo as _catalogo  # noqa: E402
from magi.core.providers.rate_limit import RateLimiterManager  # noqa: E402

_CAT = _catalogo()

FAMILY_SPECS: dict[str, list[Candidate]] = _CAT.family_specs
VERIFIED_FAMILIES: tuple[str, ...] = _CAT.verificadas
ROTOS: dict[str, str] = _CAT.rotos
HEDGE_AFTER_S: float = _CAT.hedge_tras_s
HEDGE_MAX: int = _CAT.hedge_max
DEFAULT_SWARM_FAMILIES: dict[str, str] = _CAT.reparto

# Cortesía de tasa (v6.0 §C7): espaciar las ráfagas hacia los proveedores
# gratuitos. El log del 16-ago disparó ~50 llamadas HTTP seguidas y varios
# endpoints responden después con 429; el bucket por candidato deja pasar el
# burst (capacity) y luego espacia (rate) sin esperar más que unos pocos
# milisegundos por llamada. `rate_limit.py` ya existía y nadie lo usaba.
TASA_RATE: float = _CAT.tasa_rate
TASA_CAPACITY: int = _CAT.tasa_capacity
_MAX_ESPERA_TASA_S = 2.0
_tasa_manager = RateLimiterManager()

#: Tope de caracteres del prompt. ANTES NO HABÍA NINGUNO: se mandaba lo que
#: hiciera falta y, si no cabía, el error se leía como "proveedor roto" y se
#: rotaba a otro que fallaba por lo mismo.
VENTANA_CONTEXTO: int = _CAT.ventana_contexto


def recargar_catalogo() -> dict:
    """
    Relee el catálogo sin reiniciar MAGI. Para el botón de la pestaña
    Configuración: editas el JSON, pulsas, y ya está.
    """
    global _CAT, FAMILY_SPECS, VERIFIED_FAMILIES, ROTOS
    global HEDGE_AFTER_S, HEDGE_MAX, DEFAULT_SWARM_FAMILIES, VENTANA_CONTEXTO
    global TASA_RATE, TASA_CAPACITY
    _CAT = _catalogo(recargar=True)
    FAMILY_SPECS = _CAT.family_specs
    VERIFIED_FAMILIES = _CAT.verificadas
    ROTOS = _CAT.rotos
    HEDGE_AFTER_S = _CAT.hedge_tras_s
    HEDGE_MAX = _CAT.hedge_max
    DEFAULT_SWARM_FAMILIES = _CAT.reparto
    VENTANA_CONTEXTO = _CAT.ventana_contexto
    TASA_RATE = _CAT.tasa_rate
    TASA_CAPACITY = _CAT.tasa_capacity
    return _CAT.informe()


def informe_catalogo() -> dict:
    return _CAT.informe()


# Marcadores de código que delatan a un provider capaz de lanzar un navegador.
# Se buscan en el FUENTE del módulo, no en lo que el provider declara de sí
# mismo: Cloudflare y DeepInfra declaran `use_nodriver = False` y aun así abren
# Chrome con CDPSession(headless=False). Fiarse de la declaración fue lo que
# dejó pasar el bug durante tres intentos de arreglo.
_BROWSER_MARKERS = (
    "CDPSession", "SyncCDPSession", "get_shared_browser",
    "get_nodriver", "get_args_from_nodriver", "get_args_from_webview",
    "webview.create_window", "import webbrowser",
)

# Respaldo para el .exe. `inspect.getsource` no funciona dentro de un bundle
# de PyInstaller: el fuente .py no viaja, solo el .pyc. Sin esta lista, el
# binario publicado intentaría Cloudflare antes que a un candidato limpio (el
# cortafuegos lo cortaría igual, así que no hay ventana, pero se gasta un
# intento y el orden deja de coincidir con el de desarrollo). Se mantiene
# corta y explícita: es un respaldo, no la defensa.
_BROWSER_PROVIDERS_CONOCIDOS = frozenset({
    "Cloudflare", "DeepInfra", "Gemini", "OpenaiChat", "Copilot",
    "CopilotSession", "CopilotAccount", "LMArena", "Grok", "Pi",
    "GoogleSearch", "HuggingChat", "HailuoAI", "MicrosoftDesigner",
    "OpenaiAccount", "GLM",
})

# Veredicto "este provider abre navegador" por clase. Es estable en runtime
# (leer el fuente de un módulo no cambia), pero se mantiene en una caché
# ACOTADA: un dict global sin tope crecía toda la sesión. TTL largo porque la
# detección es determinista por proceso; maxsize holgado para el catálogo.
_browser_cache: TTLCache[bool] = TTLCache(maxsize=256, ttl_s=3600.0)


def _uses_browser(cls) -> bool:
    """
    True si este proveedor de g4f puede abrir un navegador real.

    REGLA DEL PROYECTO (§I.3): la inferencia es de nube gratuita y SIN abrir
    nada visible al usuario.

    La detección tiene dos niveles:

    1. Lo que el provider declara (`use_nodriver`, `webdriver`). Pilla a
       Gemini y OpenaiChat.
    2. Lo que el provider HACE, leyendo el fuente de su módulo. Pilla a
       Cloudflare y DeepInfra, que declaran `use_nodriver = False` y aun así
       llaman `CDPSession(headless=False)` -> subprocess.Popen(chrome.exe) sin
       `--headless`, o sea una ventana visible. Ese era el bug real: Cloudflare
       es justo el provider que respondía en todos los logs del usuario.

    Leer el fuente en vez de mantener una lista negra hace que la defensa
    siga valiendo cuando g4f añada providers nuevos.
    """
    if getattr(cls, "use_nodriver", False):
        return True
    if getattr(cls, "webdriver", None):
        return True

    key = f"{getattr(cls, '__module__', '')}.{getattr(cls, '__name__', '')}"
    cached = _browser_cache.get(key)
    if cached is not None:
        return cached

    nombre = getattr(cls, "__name__", "")
    try:
        import inspect
        src = inspect.getsource(sys.modules[cls.__module__])
        verdict = any(m in src for m in _BROWSER_MARKERS)
    except Exception:
        # Congelado en el .exe: no hay fuente. Cae al respaldo estático.
        verdict = nombre in _BROWSER_PROVIDERS_CONOCIDOS
    _browser_cache.set(key, verdict)
    return verdict


def _resolve(name: str):
    """
    Obtiene la clase de proveedor g4f por nombre, o None si no existe.

    NO descarta a los proveedores capaces de abrir navegador; los DEGRADA al
    final de la cola (ver `_ordered`). El cambio es deliberado:

    quien impide que se abra una ventana es el cortafuegos de
    magi/core/no_browser.py, que corta subprocess.Popen a nivel de proceso. Con
    esa garantía puesta, descartar por precaución solo hacía perder proveedores
    buenos: `Gemini` declara `use_nodriver=True` y sin embargo responde por
    HTTP en 3.4s usando cookies en caché. Descartarlo dejaba la familia gemini
    entera sin candidatos a cambio de nada.

    Con el orden degradado, un proveedor así se intenta el último; si de verdad
    trata de abrir Chrome, el cortafuegos lo corta en 0ms y la familia salta al
    siguiente. Se gana disponibilidad sin ceder ni un pixel de §I.3.
    """
    try:
        import g4f.Provider as P
    except ImportError:
        return None
    return getattr(P, name, None)


def _disable_g4f_browser() -> None:
    """
    Activa el cortafuegos de navegador de MAGI (magi/core/no_browser.py).

    Se conserva el nombre porque es el punto de enganche que ya llamaba el
    backend, pero la lógica vive ahora en un módulo propio, con test y con
    `self_test()` para que Naoko pueda comprobar la invariante §I.3 en vivo.

    Se llama en cada `_get_client()` a propósito: `install()` es idempotente y
    reaplica las capas que dependen de g4f, así que da igual si el módulo se
    importó antes o después de que g4f estuviera cargado.
    """
    install_browser_guard()

    # Adaptador de firma de curl_cffi, aquí y no al importar el módulo.
    #
    # `proxy` se movió del constructor al método `request`, g4f lo sigue
    # pasando al constructor, y eso enterró a PhindAi y Qwen entre los
    # proveedores «rotos»: dos familias por un argumento de más. Lo revive
    # `providers/compat_curl.py` leyendo la firma real.
    #
    # POR QUÉ AQUÍ. Aplicarlo al importar obligaba a importar curl_cffi en el
    # arranque, y `test_arranque_ligero` lo cazó al instante: es exactamente la
    # regresión que ese test existe para impedir —el sistema hace lo mismo,
    # solo que más tarde—. Aquí se está a punto de usar g4f de verdad, así que
    # el coste lo paga quien lo usa. `aplicar()` es idempotente, igual que el
    # cortafuegos de la línea de arriba.
    from ..compat_curl import aplicar as aplicar_compat_curl
    aplicar_compat_curl()

    # Y el segundo parche aguas arriba, por la misma puerta y por el mismo
    # motivo: `Perplexity.py` lee `conversation.thread_title` sin haberlo
    # asignado y pierde la respuesta ENTERA al final, después de recibirla.
    #
    # Eso mantenía fuera la familia `claude` —Perplexity sirve claude45sonnet
    # y claude40opus, sin cuenta ni cookies—, que es justo la que más se
    # quería. Ver `providers/compat_g4f.py`: el muro no era un muro.
    from ..compat_g4f import aplicar as aplicar_compat_g4f
    aplicar_compat_g4f()


#: Umbral por debajo del cual una respuesta no puede ser una respuesta.
#:
#: 12 caracteres, y el número sale de la medida, no del gusto. El caso real
#: fueron 4 (`'tud.'`). Se deja margen para las respuestas legítimamente
#: cortas que este sistema sí produce —«Sí.», «Correcto.», «APROBADO»— sin
#: llegar a admitir un fragmento de palabra.
#:
#: Se queda deliberadamente BAJO: la comprobación tiene que cazar el fallo
#: evidente sin convertirse en un juez de calidad. Rechazar una respuesta
#: corta pero válida cuesta una llamada de red y confunde el log.
MINIMO_UTIL = 12


def _por_que_es_inservible(content: str, minimo: int = MINIMO_UTIL) -> str | None:
    """
    ¿Por qué esta respuesta no sirve? `None` si sirve.

    Devuelve el motivo en texto y no un booleano a propósito: acaba en el log
    y en el mensaje de error de la familia agotada, y «Perplexity: 4 caracteres
    ('tud.')» se entiende sin abrir el código, mientras que «False» no.
    """
    if content is None:
        return "None en vez de texto"
    limpio = content.strip()
    if not limpio:
        return "vacía"
    if len(limpio) < minimo:
        # Un fragmento como 'tud.' empieza en minúscula y termina en punto: es
        # el final de una frase cuyo principio se perdió. Pero no se exige eso
        # para rechazar —sería adivinar—: por debajo del mínimo no sirve, y el
        # motivo dice exactamente qué llegó.
        return f"{len(limpio)} caracteres ({limpio!r})"
    return None


class G4FProvider(BaseProvider):
    """Un backend = UNA familia, con cadena de candidatos dentro de ella."""

    supports_tools = False      # g4f no expone tool-calling fiable; MAGI lo
                                # emula con protocolo de texto (ver tools/protocol.py)
    supports_vision = True
    supports_stream = True
    is_local = False

    def __init__(self, family: str = "auto",
                 candidates: Iterable[Candidate] | None = None,
                 provider_id: str | None = None):
        if family not in FAMILY_SPECS and candidates is None:
            raise ValueError(f"familia desconocida: {family}")
        self.family = family
        self.id = provider_id or f"g4f-{family}"
        self.candidates: list[Candidate] = list(candidates or FAMILY_SPECS[family])
        self.default_model = self.candidates[0][1] or "default"
        self._client = None
        self._live: Candidate | None = None   # candidato que funcionó la última vez
        #: Latencia media medida por candidato, en ms. Se llena sola con cada
        #: respuesta y ordena los intentos: el catálogo dice quién PUEDE
        #: contestar, esto dice quién contesta RÁPIDO hoy.
        self._latencia: dict[Candidate, float] = {}
        #: Breaker de CALIDAD por candidato upstream (v5.29 Fase 2): el que
        #: contesta basura dos veces seguidas deja de recibir tráfico.
        self._calidad = CalidadBreaker()

    # ------------------------------------------------------------------ setup

    def _get_client(self):
        if self._client is None:
            _disable_g4f_browser()
            try:
                from g4f.client import AsyncClient
            except ImportError as e:
                raise ProviderUnavailable("g4f no instalado: pip install -U g4f") from e
            self._client = AsyncClient()
        return self._client

    async def available(self) -> bool:
        """Disponible si al menos un candidato de la familia existe en g4f."""
        try:
            self._get_client()
        except ProviderUnavailable:
            return False
        return any(_resolve(name) is not None for name, _ in self.candidates)

    def _ordered(self) -> list[Candidate]:
        """
        Orden de intento: los rápidos primero, capaces-de-navegador al final.

        1. Los que solo hablan HTTP, ORDENADOS POR LATENCIA MEDIDA. Los que
           aún no se han probado van justo detrás del más rápido conocido, para
           que se les dé una oportunidad sin castigar al que ya va bien.
        2. Los que podrían intentar abrir un navegador. El cortafuegos los
           corta en 0 ms si lo intentan, así que estar en la lista no cuesta
           nada; ponerlos al final evita gastar ese intento cuando hay una
           alternativa limpia.

        Antes mandaba la afinidad a secas: el último que funcionó iba primero
        para siempre. En el registro del usuario eso dejaba a `Yqcloud` en
        cabeza de la familia gpt aunque una de sus respuestas tardara 13,9 s
        —el pico que arrastraba la etapa entera— habiendo alternativas de 2 s
        en la misma familia. Con la latencia medida el orden se corrige solo.
        """
        def puede_abrir_navegador(c: Candidate) -> bool:
            cls = _resolve(c[0])
            return cls is not None and _uses_browser(cls)

        conocidas = [v for v in self._latencia.values()]
        sin_medir = min(conocidas) if conocidas else 0.0

        def coste(c: Candidate) -> float:
            return self._latencia.get(c, sin_medir)

        # Los ROTOS no entran: no pueden contestar, y comprobarlo cuesta un
        # turno. Cada ronda del enjambre gastaba seis llamadas condenadas de
        # antemano. Si al descartarlos la familia se queda sin nadie,
        # `complete()` la reporta agotada — que es lo que pasaba, solo que sin
        # esperar a comprobarlo seis veces.
        #
        # Los capaces-de-navegador SÍ entran, pero al final. Excluirlos del
        # todo fue un error que este mismo cambio introdujo y que cazó
        # `test_las_familias_verificadas_si_tienen_candidatos`: `Gemini`
        # declara `use_nodriver=True` y sin embargo responde por HTTP en 3,4 s
        # —está en el registro del usuario contestando una y otra vez—, así que
        # descartarlo dejaba la familia gemini ENTERA sin candidatos. Los que
        # de verdad solo saben abrir Chrome (Cloudflare, DeepInfra) están en
        # ROTOS, que es donde les corresponde.
        #
        # (v5.29 Fase 2) Tampoco entran los ABIERTOS por calidad: dos basuras
        # seguidas y el candidato espera su ventana de semapertura. El estado
        # del breaker es consultable en snapshot()/sys.config.
        vivos = [c for c in self.candidates
                 if c[0] not in ROTOS and self._calidad.permite(c[0])]
        limpios = sorted((c for c in vivos if not puede_abrir_navegador(c)),
                         key=coste)
        degradados = sorted((c for c in vivos if puede_abrir_navegador(c)),
                            key=coste)
        return limpios + degradados

    def motivos_descartados(self) -> dict[str, str]:
        """
        Por qué cada candidato NO se intenta. Lo enseña Configuración.

        Solo lista lo que de verdad queda fuera de `_ordered()`. La primera
        versión también marcaba «abriría un navegador» a candidatos que sí se
        usan —`Gemini` lo declara y responde por HTTP—, y una pantalla de
        diagnóstico que dice que algo está descartado cuando se está usando es
        peor que no tener pantalla.
        """
        en_cola = {n for n, _ in self._ordered()}
        fuera: dict[str, str] = {}
        for nombre, _ in self.candidates:
            if nombre in en_cola:
                continue
            if nombre in ROTOS:
                fuera[nombre] = ROTOS[nombre]
            elif _resolve(nombre) is None:
                fuera[nombre] = "no existe en esta versión de g4f"
            else:
                fuera[nombre] = "descartado por el cortafuegos §I.3"
        return fuera

    def _anota_latencia(self, cand: Candidate, ms: float) -> None:
        """Media móvil: una respuesta lenta suelta no destierra a un candidato."""
        previa = self._latencia.get(cand)
        self._latencia[cand] = ms if previa is None else previa * 0.7 + ms * 0.3

    def mejor_latencia_ms(self) -> float | None:
        """
        La respuesta más rápida que tenemos medida de esta familia.

        El techo dinámico del registry (v6.0 §A7) usa esto para no dejar que
        un candidato que ya demostró responder en 2 s se lleve 24 s de espera:
        el log del 16-ago tiene colas de latencia enteras pagadas por un solo
        proveedor lento con alternativas de 2 s esperando turno.
        """
        conocidas = [v for v in self._latencia.values() if v and v > 0]
        return min(conocidas) if conocidas else None

    # ------------------------------------------------------------- inferencia

    async def _pedir(self, cand: Candidate, messages: list, req: CompletionRequest
                     ) -> tuple[Candidate, str]:
        """Una llamada a un candidato. Devuelve (candidato, texto) o lanza."""
        name, model = cand
        cls = _resolve(name)
        if cls is None:
            raise ProviderError(f"{name}: no existe en g4f")

        # Yqcloud es rápido pero suele contestar en chino. Forzamos el idioma
        # inyectando la directiva a nivel de sistema.
        if name == "Yqcloud":
            lang_prompt = {"role": "system", "content": "You are a helpful assistant. You must always answer in Spanish language. Do NOT use Chinese language."}
            if not messages or messages[0]["role"] != "system":
                messages = [lang_prompt] + messages
            else:
                messages = list(messages)
                messages[0] = {"role": "system", "content": messages[0]["content"] + "\n\n" + lang_prompt["content"]}

        await self._esperar_tasa(name)
        t0 = time.monotonic()
        kwargs: dict[str, Any] = {"model": model or "", "messages": messages,
                                  "provider": cls}
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        resp = await self._get_client().chat.completions.create(**kwargs)
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        if not content.strip():
            raise ProviderError(f"{name}: respuesta vacía")
        self._anota_latencia(cand, (time.monotonic() - t0) * 1000)
        return cand, content

    def _hedge_max_politica(self, req: CompletionRequest) -> int:
        """
        Cuántos candidatos simultáneos lanza esta petición.

            req.hedge == True  -> HEDGE_MAX (el que pide cobertura la tiene)
            req.hedge == False -> 1 (quien llama ya tiene redundancia)
            None (auto)        -> HEDGE_MAX solo si la familia está sin medir o
                                  su mejor latencia conocida supera 8 s. Si la
                                  familia ya responde rápido, la cubierta solo
                                  multiplica llamadas: medido el 16-ago, una
                                  sola petición pasó de ~16 llamadas lógicas a
                                  ~50 HTTP por el hedge global de 3.
        """
        if req.hedge is True:
            return HEDGE_MAX
        if req.hedge is False:
            return 1
        conocidas = list(self._latencia.values())
        if not conocidas:
            return HEDGE_MAX          # sin medida: no se puede saber si es lenta
        mejor = min(conocidas)
        return HEDGE_MAX if mejor > 8000.0 else 1

    async def _esperar_tasa(self, name: str) -> None:
        """
        Cortesía por candidato: no bombardear al proveedor (v6.0 §C7).

        Si el bucket está vacío, se espera solo hasta que se recargue un
        token o `_MAX_ESPERA_TASA_S`, y se procede igual aunque no llegue:
        la cortesía espacia ráfagas, pero una llamada NUNCA se bloquea por
        ella. Sin esto, una petición en `fast` puede lanzar ~18 llamadas
        seguidas a un endpoint gratuito; con esto, las primeras `capacity`
        pasan y el resto entra a ritmo `rate`.
        """
        bucket = _tasa_manager.get_bucket(name, TASA_RATE, TASA_CAPACITY)
        if bucket.consume(1.0):
            return
        fin = time.monotonic() + _MAX_ESPERA_TASA_S
        while time.monotonic() < fin:
            await asyncio.sleep(0.05)
            if bucket.consume(1.0):
                return

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        """
        Pide a la familia, con PETICIÓN CUBIERTA.

        Antes se probaban los candidatos en serie: si el primero tardaba 14 s,
        se esperaban los 14 s. Y así fue en el registro del usuario — una sola
        respuesta de `Yqcloud` a 13.953 ms arrastró la etapa entera de
        Melchior, con alternativas de 2 s esperando su turno en la misma
        familia.

        Ahora, si el primer candidato no ha contestado en `HEDGE_AFTER_S`, se
        lanza el siguiente EN PARALELO sin cancelar al primero, y gana el que
        conteste antes. El caso bueno no cambia (una sola llamada, mismo
        coste); el caso malo deja de pagar la cola de latencia entera. Es la
        misma respuesta, antes: no se recorta nada.

        Si un candidato falla en firme, entra el siguiente de inmediato en vez
        de esperar el margen, que es lo que ya hacía la versión en serie.
        """
        started = time.monotonic()
        self._get_client()
        messages = [m.to_wire() for m in req.messages]
        errors: list[str] = []
        cola = [c for c in self._ordered() if _resolve(c[0]) is not None]
        if not cola:
            raise ProviderError(f"familia '{self.family}': ningún candidato existe")

        # Tope simultáneo de ESTA petición: ahora el hedge es por llamada, no
        # una constante global. Las variantes y ejes en paralelo no piden
        # cubierta (ya se cubren entre sí); solo las llamadas únicas la
        # conservan cuando la familia no está medida o va lenta.
        hedge_max = self._hedge_max_politica(req)
        pendientes: dict[asyncio.Task, Candidate] = {}
        siguiente = 0
        try:
            while pendientes or siguiente < len(cola):
                if siguiente < len(cola) and len(pendientes) < hedge_max:
                    cand = cola[siguiente]
                    siguiente += 1
                    pendientes[asyncio.ensure_future(
                        self._pedir(cand, messages, req))] = cand

                if not pendientes:
                    break
                espera = HEDGE_AFTER_S if siguiente < len(cola) else req.timeout_s
                hechas, _ = await asyncio.wait(
                    pendientes, timeout=espera,
                    return_when=asyncio.FIRST_COMPLETED)

                if not hechas:
                    continue          # nadie ha contestado: se cubre con otro

                for t in hechas:
                    cand = pendientes.pop(t)
                    try:
                        ganador, content = t.result()
                    except Exception as e:
                        errors.append(f"{cand[0]}: {type(e).__name__}: {e}")
                        logger.debug("[%s] candidato %s falló: %s",
                                     self.id, cand[0], e)
                        # (v5.29 Fase 1+2) el fallo se mide y cuenta: si este
                        # candidato lleva dos seguidos, el breaker lo saca.
                        estado = self._calidad.fallo(cand[0])
                        if estado == "ABIERTO":
                            logger.warning(
                                "[%s] %s ABIERTO por calidad: %d fallos "
                                "seguidos; fuera de rotación %ds",
                                self.id, cand[0],
                                self._calidad.umbral,
                                int(self._calidad.ventana_s))
                        emit_provider_metric(cand[0], 0.0, False,
                                             family=self.id)
                        continue

                    # ¿ES UNA RESPUESTA O SOLO PARECE UNA?
                    #
                    # Medido el 2026-08-13: tras unas veinte peticiones seguidas,
                    # `Perplexity` empezó a devolver esto, para CUALQUIER modelo
                    # y cualquier pregunta:
                    #
                    #     len=4   'tud.'
                    #
                    # Es el final de una frase. Ese proveedor manda la respuesta
                    # por parches JSON y g4f solo acumula los que vienen en un
                    # campo concreto; cuando el formato cambia —o cuando les
                    # limitan a uno— llega el último trozo y nada más.
                    #
                    # Lo grave no es el fallo de g4f: es que MAGI lo daba por
                    # bueno. `'tud.'` habría llegado a la interfaz como la
                    # antítesis de BALTHASAR, con su latencia, su proveedor y su
                    # aspecto de respuesta legítima. Un fallo que se disfraza de
                    # éxito no se detecta nunca.
                    #
                    # Tratarlo como error hace que la maquinaria de failover que
                    # ya existe pruebe al siguiente candidato, que es justo lo
                    # que hay que hacer.
                    inservible = _por_que_es_inservible(
                        content, minimo=1 if req.probe else MINIMO_UTIL)
                    if inservible:
                        errors.append(f"{cand[0]}: {inservible}")
                        logger.warning("[%s] %s devolvió una respuesta "
                                       "inservible (%s); probando otro",
                                       self.id, cand[0], inservible)
                        # (v5.29 Fase 1+2) la basura se mide como fallo: era
                        # EL bug del router a ciegas — Perplexity devolvía
                        # 'tud.' y seguíamos rotándolo como si nada.
                        estado = self._calidad.fallo(cand[0])
                        if estado == "ABIERTO":
                            logger.warning(
                                "[%s] %s ABIERTO por calidad (%s); fuera de "
                                "rotación %ds", self.id, cand[0], inservible,
                                int(self._calidad.ventana_s))
                        emit_provider_metric(cand[0], 0.0, False,
                                             family=self.id)
                        continue

                    self._calidad.exito(ganador)
                    emit_provider_metric(
                        ganador[0],
                        self._latencia.get(ganador,
                                           (time.monotonic() - started) * 1000),
                        True, family=self.id)
                    self._live = ganador
                    usage = Usage(
                        prompt_tokens=sum(self.estimate_tokens(str(m["content"]))
                                          for m in messages),
                        completion_tokens=self.estimate_tokens(content),
                    )
                    nombre, modelo = ganador
                    etiqueta = f" ({req.tag})" if req.tag else ""
                    logger.info("[%s]%s respondió %s/%s en %.0fms%s",
                                self.id, etiqueta, nombre, modelo or "default",
                                (time.monotonic() - started) * 1000,
                                f" (cubierto x{len(pendientes) + 1})"
                                if pendientes else "")
                    return self._mk_response(
                        content, f"{nombre}/{modelo or 'default'}", started, usage)
        finally:
            # Las llamadas cubiertas que perdieron la carrera se cancelan: la
            # respuesta ya está, seguir esperándolas solo gastaría cuota.
            for t in pendientes:
                t.cancel()

        raise ProviderError(
            f"familia '{self.family}' agotada ({len(self.candidates)} candidatos): "
            + " | ".join(errors[:4]))

    async def stream(self, req: CompletionRequest) -> AsyncIterator[Delta]:
        client = self._get_client()
        messages = [m.to_wire() for m in req.messages]
        errors: list[str] = []

        for name, model in self._ordered():
            cls = _resolve(name)
            if cls is None:
                continue
            await self._esperar_tasa(name)
            seq, emitted = 0, False
            try:
                stream = client.chat.completions.stream(
                    model=model or "", messages=messages, provider=cls)  # type: ignore[arg-type]
                async for chunk in stream:
                    piece = ""
                    if getattr(chunk, "choices", None):
                        delta = getattr(chunk.choices[0], "delta", None)
                        piece = getattr(delta, "content", "") or ""
                    if piece:
                        emitted = True
                        yield Delta(text=piece, seq=seq, provider_id=self.id)
                        seq += 1
                if emitted:
                    self._live = (name, model)
                    yield Delta(text="", seq=seq, done=True, provider_id=self.id)
                    return
                errors.append(f"{name}: stream vacío")
            except Exception as e:
                errors.append(f"{name}: {e}")
                if emitted:
                    # Ya salió texto a pantalla; reintentar duplicaría.
                    yield Delta(text="", seq=seq, done=True, provider_id=self.id)
                    return
                continue

        raise ProviderError(f"streaming agotado en familia '{self.family}': "
                            + " | ".join(errors[:3]))

    async def complete_vision(self, req: CompletionRequest,
                              image_data_url: str) -> CompletionResponse:
        """Multimodal (lo que Naoko usa para leer capturas de pantalla)."""
        started = time.monotonic()
        client = self._get_client()
        base = [m.to_wire() for m in req.messages[:-1]]
        last = req.messages[-1]
        base.append({
            "role": "user",
            "content": [
                {"type": "text", "text": last.content if isinstance(last.content, str)
                 else str(last.content)},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        })
        for name, model in self._ordered():
            cls = _resolve(name)
            if cls is None:
                continue
            try:
                resp = await client.chat.completions.create(
                    model=model or "", messages=base, provider=cls)  # type: ignore[arg-type]
                content = (resp.choices[0].message.content or "") if resp.choices else ""
                if content.strip():
                    return self._mk_response(content, f"{name}(vision)", started)
            except Exception:
                continue
        raise ProviderError(f"visión no disponible en familia '{self.family}'")


def build_swarm_providers(
    families: dict[str, str] | None = None,
) -> dict[str, G4FProvider]:
    """Un proveedor por nodo del enjambre, cada uno en su familia."""
    fam = families or DEFAULT_SWARM_FAMILIES
    return {role: G4FProvider(family=f, provider_id=f"g4f-{f}")
            for role, f in fam.items()}


# ------------------------------------------------- el adaptador de la sonda

def candidatos_para_sondear() -> list[tuple[str, str, str]]:
    """
    `(familia, proveedor, modelo)` de todo lo que tiene sentido medir.

    Se saltan los que están en `ROTOS`: medir a quien ya sabemos que no puede
    contestar gasta cuota para confirmar lo que el catálogo ya dice, y encima
    ensucia la media con ceros.
    """
    fuera: list[tuple[str, str, str]] = []
    for familia, candidatos in FAMILY_SPECS.items():
        for nombre, modelo in candidatos:
            if nombre in ROTOS:
                continue
            fuera.append((familia, nombre, modelo or ""))
    return fuera


class LlmDeSonda:
    """
    Adaptador mínimo para `sonda.medir_candidato`.

    POR QUÉ HACÍA FALTA, Y POR QUÉ LA SONDA NO SE USABA
    ===================================================
    `sonda.medir_candidato` llama a `llm.generate(..., family=, proveedor=,
    modelo=)`. `FreeCloudLLM.generate` no acepta `proveedor` ni `modelo`: elige
    él dentro de la familia. O sea, la sonda estaba escrita contra un interfaz
    que **no existía**, y por eso nunca la llamó nadie: no era que faltara el
    disparador, es que no había con qué disparar.

    Medir un candidato CONCRETO es todo el sentido de la sonda. Si midiera «lo
    que la familia elija», la media saldría del que respondió, nunca del que
    falla, y el panel diría que todo va bien.

    Va aquí y no en `sonda.py` a propósito: `sonda.py` no debe importar g4f —
    `test_arranque_ligero` lo prohíbe y con razón, porque arrastra medio mundo
    al arranque—.
    """

    async def generate(self, system_prompt: str, user_prompt: str, *,
                       family: str = "", proveedor: str = "", modelo: str = "",
                       temperature: float = 0.0, **_kw) -> tuple[str, str]:
        _disable_g4f_browser()          # el cortafuegos, siempre antes
        cls = _resolve(proveedor)
        if cls is None:
            raise ProviderUnavailable(f"proveedor desconocido: {proveedor}")

        try:
            from g4f.client import AsyncClient
        except ImportError as e:                              # pragma: no cover
            raise ProviderUnavailable("g4f no instalado") from e

        mensajes = [{"role": "user", "content": user_prompt}]
        if system_prompt:
            mensajes.insert(0, {"role": "system", "content": system_prompt})

        respuesta = await AsyncClient().chat.completions.create(
            model=modelo or "", provider=cls, messages=mensajes)  # type: ignore[arg-type]
        texto = (respuesta.choices[0].message.content or "")

        # Aquí SOLO se rechaza lo vacío: el juicio del contenido lo pone
        # `sonda.medir_candidato` con sus SEÑALES_ESPERADAS. Aplicar el mínimo
        # del tráfico real aquí era medir la salud del proveedor con la regla
        # del tráfico y matar al paciente en el chequeo: la respuesta de un
        # canario es corta POR DISEÑO.
        motivo = _por_que_es_inservible(texto, minimo=1)
        if motivo:
            raise ProviderError(f"respuesta inservible: {motivo}")
        return texto, f"{proveedor}/{modelo or 'default'}"
