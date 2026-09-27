import argparse
import asyncio
import logging
import os
import signal
import sys
import threading

# §I.3 — ANTES QUE NADA. El cortafuegos de navegador se instala en la primera
# línea ejecutable del proceso, antes de importar webview, g4f o cualquier
# módulo que pueda arrastrarlos. La capa de subprocess.Popen no depende de g4f,
# así que instalarla aquí garantiza que ninguna ruta —conocida o futura— pueda
# lanzar una ventana de navegador durante el arranque ni durante la inferencia.
from magi.core.no_browser import install as _install_browser_guard

_install_browser_guard()

# Y la consola en UTF-8, por el mismo motivo de orden: escribir un acento en la
# consola cp1252 de Windows lanza UnicodeEncodeError, y esa excepción subía por
# el bucle de streaming y lo abortaba a mitad de respuesta —"streaming falló
# ('charmap' codec can't encode characters...)"—, forzando pedir la respuesta
# entera otra vez. Este proyecto habla español: no era un caso raro.
from magi.core.consola import configurar as _configurar_consola

_configurar_consola()

import webview

from magi.gui_server import GUIServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from magi.core.kernel import Kernel
from magi.modules.memory.composer import Composer
from magi.modules.resilience.selector import CloudSelector
from magi.modules.route.gateway import Gateway

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("MagiSystem")

class MagiSystem:
    """
    Orquestador principal del Magisys (Área 0 y Centro de Control).
    Amarra el bus de eventos, la pasarela de UI, la resiliencia cloud y los módulos operativos.
    """
    def __init__(self, host="127.0.0.1", port=20128, debug=False):
        self.host = host
        self.port = port
        self.debug = debug
        if self.debug:
            logging.getLogger().setLevel(logging.DEBUG)

        self.kernel = Kernel(host=self.host, port=self.port)
        self.bus = self.kernel.bus
        # Inicialización de Resiliencia Cloud-Only (Área 6)
        self.cloud_selector = CloudSelector(["cloud-openai-gpt4", "cloud-anthropic-claude", "cloud-google-gemini", "cloud-mistral", "cloud-cohere"])
        self._shutdown_event = asyncio.Event()

    async def _setup_signal_handlers(self):
        """Maneja el apagado limpio (Graceful Shutdown)"""
        if sys.platform != 'win32':
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._shutdown_event.set)
        else:
            # En Windows no podemos usar add_signal_handler directamente de la misma forma,
            # pero asyncio.run se encarga de KeyboardInterrupt.
            pass

    async def start(self):
        logger.info("Iniciando Magisys...")

        # 1. Setup de señales
        await self._setup_signal_handlers()

        # Los suscriptores registrados en constructores síncronos (colector de
        # métricas, logger del bus) tienen su worker pendiente hasta aquí.
        self.bus.start_pending_workers()

        # 2. Levantar el Kernel (Área 0)
        await self.kernel.start()
        # El perro guardián de la ronda dormida: tareas vivas + bus mudo
        # = hallazgo, no silencio. Nació de una tarea real colgada 20 min
        # entre réplica y síntesis sin que nada lo dijera (2-sep-2026).
        from magi.modules.infrastructure.guardian import vigilar
        asyncio.get_event_loop().create_task(
            vigilar(self.bus, self.kernel.swarm))

        # 3. Levantar otros módulos base
        self.gateway = Gateway()
        from magi.modules.memory.record import MemoryRecord
        self.record = MemoryRecord("main_session")
        self.composer = Composer(self.record)

        # ---------------------------------------------------------------
        # MAGI 9.0 — Regla "conecta o borra".
        #
        # v5.0.28 instanciaba aquí doce subsistemas (MagiHive, SemanticRAG,
        # HierarchicalMemory, SymbolicVerifier, PromptCompiler, EvolverAgent,
        # CognitiveCore, QuantumOracle, HyperdimensionalMemory, SkinMembrane,
        # MarketDigitalTwin...). Conteo real de sitios de llamada:
        #
        #     self.semantic_rag.*        -> 0     self.cognitive_core.*  -> 0
        #     self.hierarchical_memory.* -> 0     self.quantum_oracle.*  -> 0
        #     self.prompt_compiler.*     -> 0     self.hdc_memory.*      -> 0
        #     self.evolver.*             -> 0     self.quant_simulator.* -> 0
        #     self.hive.*                -> 1  (solo .shutdown())
        #     self.cellular_router.*     -> 1  (solo .shutdown())
        #
        # Los dos únicos que se usaban, se usaban para APAGARLOS. Existían para
        # que se imprimieran las líneas "MAGI 5.0 Bio-Quantum", "MAGI 7.0
        # Predictive Twin", etc.
        #
        # Se retiran. Lo que queda está conectado y tiene tests.
        # ---------------------------------------------------------------
        from magi.core.context import refresh_context
        from magi.core.providers.cloud import get_registry

        self.provider_registry = await get_registry()
        self.exec_context = refresh_context(
            provider_health=self.provider_registry.telemetry())

        assignment = self.provider_registry.select_for_swarm()
        logger.info("Enjambre: %s", " · ".join(
            f"{r}={assignment.families.get(r, 'n/d')}" for r in assignment.by_role))
        if assignment.diversity != "full":
            logger.warning("Diversidad %s: %s", assignment.diversity, assignment.note)

        fams = self.provider_registry.families_available()
        logger.info("Inferencia: %d familias de nube gratuita sanas -> %s",
                    len(fams), ", ".join(fams) or "NINGUNA")
        logger.info("Datos en: %s", __import__("magi.core.paths", fromlist=["x"]).data_dir())

        logger.info("SISTEMA MAGI OPERATIVO Y ESPERANDO CONEXIONES.")

        # 4. Mantener vivo hasta apagado
        try:
            if sys.platform == 'win32':
                # Bucle de espera compatible con Windows
                while not self._shutdown_event.is_set():
                    await asyncio.sleep(1)
            else:
                await self._shutdown_event.wait()
        except KeyboardInterrupt:
            logger.info("Interrupción por teclado detectada.")

        await self.stop()

    async def stop(self):
        logger.info("Deteniendo el sistema MAGI...")
        if hasattr(self, 'bus') and self.bus:
            await self.bus.shutdown()
        if hasattr(self, 'kernel') and self.kernel:
            await self.kernel.shutdown()
            logger.info("Sistema apagado correctamente.")

def _start_magi_background(magi, loop):
    """Ejecuta el loop asyncio en un hilo secundario"""
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(magi.start())
    except Exception as e:
        logger.error(f"Error fatal en el loop secundario: {e}")


def _selftest() -> int:
    """
    Prueba de humo del BINARIO, no del repo (v5.28.0 — la compuerta del
    release la ejecuta sobre dist/Magisys.exe en el runner de Windows).

    Lo que comprueba, en orden de lo que más duele cuando falla:
      1. Los módulos del núcleo importan DENTRO del congelado.
      2. El catálogo de proveedores viajó y es legible.
      3. El registro del enjambre arma sin excepciones y SIN Groq (el
         enjambre es g4f puro; sin red — `available()` de g4f no toca red).
      4. El registro de SUBAGENTES arma; con clave, Groq está disponible.
      5. SOLO EN EL BINARIO: el Python embebido ejecuta un subproceso real.
         Es la prueba de «funciona en cualquier PC»: un exe sin intérprete
         interno arranca bien y muere a mitad, que es lo peor que puede
         publicarse.
    Sale 0 si todo bien; 1 con el primer fallo, diciendo cuál.
    """
    print("[selftest] 1/5 imports del núcleo…")
    import magi.core.agent_loop  # noqa: F401 — importar ES la prueba
    import magi.core.context  # noqa: F401
    import magi.core.paths  # noqa: F401
    import magi.core.prompts  # noqa: F401
    import magi.core.providers.registry  # noqa: F401
    import magi.core.router  # noqa: F401
    print("[selftest]   núcleo importado")

    print("[selftest] 2/5 catálogo de proveedores…")
    # `cargar_bruto()` ya sabe distinguir el catálogo del usuario
    # (%LOCALAPPDATA%) del empaquetado (repo / _MEIPASS): resolverlo a mano
    # aquí era repetir esa lógica — y mal, como acaba de demostrar el fallo.
    from magi.core.providers.catalogo import cargar_bruto
    catalogo, origen_cat = cargar_bruto()
    assert catalogo, "el catálogo de proveedores no se pudo cargar"
    assert catalogo.get("schemaVersion") == 1, "schemaVersion del catálogo != 1"
    assert catalogo.get("familias"), "catálogo sin familias"
    print(f"[selftest]   catálogo ok "
          f"({len(catalogo['familias'])} familias, {origen_cat.name})")

    async def _registros():
        from magi.core.providers.cloud import get_registry, get_subagent_registry
        return await get_registry(), await get_subagent_registry()

    print("[selftest] 3/5 registro del enjambre (g4f, sin Groq)…")
    enjambre, subagentes = asyncio.run(_registros())
    fams = enjambre.families_available()
    assert fams, "el registro del enjambre no tiene NI UNA familia sana"
    groq_en_enjambre = [f for f in fams if f.startswith("groq-")]
    assert not groq_en_enjambre, (
        f"Groq coló en el ENJAMBRE ({groq_en_enjambre}): es solo-subagente")
    print(f"[selftest]   enjambre ok ({', '.join(fams)})")

    print("[selftest] 4/5 registro de subagentes (Groq primero)…")
    from magi.core.providers.backends.groq_backend import _clave_de_fichero
    hay_clave = bool(os.environ.get("GROQ_API_KEY") or _clave_de_fichero())
    fams_sub = subagentes.families_available()
    assert fams_sub, "registro de subagentes sin ninguna familia sana"
    motor = [f for f in fams_sub if f.startswith("groq-")]
    print(f"[selftest]   subagentes ok (clave Groq: "
          f"{'sí' if hay_clave else 'NO — caen a g4f'}; "
          f"motor: {', '.join(motor) or 'g4f'})")

    print("[selftest] 5/5 Python embebido…")
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.abspath("."))
        py = os.path.join(base, "assets", "python-embed", "extracted",
                          "python.exe")
        if not os.path.isfile(py):
            print(f"[selftest]   FALTA el intérprete embebido: {py}")
            return 1
        import subprocess
        prueba = subprocess.run(
            [py, "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True, text=True, timeout=60)
        if prueba.returncode != 0:
            print(f"[selftest]   el intérprete embebido falló: "
                  f"{prueba.stderr.strip()[:200]}")
            return 1
        print(f"[selftest]   embebido ok "
              f"({prueba.stdout.strip()}): el exe funciona sin Python instalado")
    else:
        print("[selftest]   modo desarrollo: el embebido se prueba en el binario")

    print("SELFTEST OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Magisys Bootstrapper")
    parser.add_argument("--host", default="127.0.0.1", help="Host para el GUI Server (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=20128, help="Puerto para el GUI Server (default: 20128)")
    parser.add_argument("--gui-port", type=int, default=1420, help="Puerto HTTP local para el Frontend (default: 1420)")
    parser.add_argument("--debug", action="store_true", help="Habilitar logs de depuración")
    parser.add_argument("--selftest", action="store_true",
                        help="Prueba de humo del binario y sale. Sin ventana, sin red real: "
                             "lo ejecuta el release antes de publicar el .exe.")

    args = parser.parse_args()

    if args.selftest:
        try:
            sys.exit(_selftest())
        except Exception as e:  # noqa: BLE001 — el selftest REPORTA, no propaga
            print(f"SELFTEST FALLO: {type(e).__name__}: {e}")
            sys.exit(1)

    magi = MagiSystem(host=args.host, port=args.port, debug=args.debug)

    # 1. Iniciar Servidor GUI Estático
    gui = GUIServer(port=args.gui_port)
    gui.start()

    # 2. Iniciar el Kernel MAGI en un Hilo Secundario
    magi_loop = asyncio.new_event_loop()
    magi_thread = threading.Thread(target=_start_magi_background, args=(magi, magi_loop), daemon=True)
    magi_thread.start()

    # `os` y `sys` ya son imports del módulo: redeclararlos aquí convertía
    # `sys` en variable LOCAL de main() para todo el ámbito, y el
    # `sys.exit(_selftest())` de arriba reventaba con UnboundLocalError.
    def get_resource_path(relative_path):
        """ Get absolute path to resource, works for dev and for PyInstaller """
        try:
            # PyInstaller creates a temp folder and stores path in _MEIPASS
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    logger.info("Iniciando ventana nativa de MAGI...")
    webview.create_window(
        title="Magisys",
        url=f"http://127.0.0.1:{args.gui_port}",
        width=1280,
        height=800,
        frameless=False,
        easy_drag=False
    )

    # Esto bloqueará hasta que el usuario cierre la ventana
    webview.start(debug=args.debug)

    # 4. Apagado Limpio al cerrar la ventana
    logger.info("Ventana cerrada. Apagando sistemas...")
    gui.stop()

    # Señalizar al loop que se detenga
    if sys.platform != 'win32':
        magi_loop.call_soon_threadsafe(magi._shutdown_event.set)
    else:
        # Hack simple para despertar y apagar en Windows
        magi_loop.call_soon_threadsafe(magi._shutdown_event.set)

    magi_thread.join(timeout=3)
    logger.info("MAGI cerrado por completo. Adiós.")
    sys.exit(0)

if __name__ == "__main__":
    main()
