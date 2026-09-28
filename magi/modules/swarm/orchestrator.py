import asyncio
import logging
import re
import time

from magi.core.blackboard import Blackboard
from magi.core.bus import BusEvent, MagiBus
from magi.core.paths import workspace_dir
from magi.core.store.admision import AHORA, ENCOLAR
from magi.core.store.state import INTERRUMPIDA
from magi.core.verification import ProposalVerifier
from magi.modules.memory.episodic import EpisodicMemory

from . import filosofias
# Entregables de los bloques (v5.29 Fase 4): qué se ejecuta y qué solo se
# guarda, con qué extensión y con auto-chequeo. Módulo propio porque la
# lógica no es del enjambre: es del formato de lo que entrega.
from .artifactos import guardar_bloque  # noqa: F401 (re-export)
from .artifactos import parece_html as _parece_html  # noqa: F401 (re-export)
from .agents import BalthasarAgent, CasperAgent, MelchiorAgent
from .intencion import aprueba as _aprueba
from .intencion import es_respuesta_a_aprobacion
from .parallel import (
    critique_multi_axis,
    format_variants_for_critic,
    generate_variants,
)

logger = logging.getLogger(__name__)


def _n_variantes(engine: str | None, route: str, en_rebuild: bool) -> int:
    """
    Cuántos enfoques genera Melchior (v6.0 §A3): fan-out por motor.

    `fast` es frugal —su presupuesto es de 18 llamadas— y `deep` explora
    más porque su presupuesto lo permite (40). Un rebuild nunca vuelve a
    disparar el fan-out entero: la autocuración va con 1 sola variante,
    porque las otras 2-3 ya fallaron verificación y regenerarlas fue
    exactamente lo que el log del 16-ago mostró repetido sin tope.
    """
    if en_rebuild:
        return 1
    # D6 — MENOS ENFOQUES, MÁS CICLOS DE VERIFICACIÓN.
    #
    # Medido el 20-ago en el encargo del ping pong: 3 enfoques, 27.753
    # caracteres producidos, 24,7 % entregado y ningún artefacto. La calidad no
    # salía de tener tres textos.
    #
    # El contraste es mi propio procedimiento para el mismo encargo: UNA
    # propuesta y tres ciclos de «probar y arreglar». Tres propuestas que nadie
    # ejecuta valen menos que una que sí. La cuota liberada aquí se gasta en
    # verificar y construir (D3), que es donde se nota.
    por_motor = {
        "fast": {"build": 2, "task": 2},
        "deep": {"build": 2, "task": 2},
    }
    return por_motor.get(engine or "fast", por_motor["fast"]).get(route, 1)

class SwarmOrchestrator:
    """
    Controla el ciclo de vida de un debate Popperiano en el Enjambre (Área 16).
    Evita que los agentes hablen al mismo tiempo, manejando el turn-taking.
    """
    def __init__(self, blackboard: Blackboard, bus: MagiBus, store=None):
        self.blackboard = blackboard
        self.bus = bus
        # MAGI 9.0 §1.4: el estado deja de vivir solo en RAM. active_tasks se
        # mantiene como caché caliente, pero se persiste en cada transición.
        # Antes, cerrar la ventana perdía la conversación entera.
        from magi.core.store.state import TaskStore
        self.store = store if store is not None else TaskStore()
        self.active_tasks = {}
        self._lock = asyncio.Lock()
        self.latest_task_id = None
        self._memory: dict[str, EpisodicMemory] = {}
        self._reconciliadas: list[str] = []
        from magi.core.store.admision import LibroDeAdmision
        self.admision = LibroDeAdmision(self.store)
        try:
            self.blackboard.post("global.task_store", self.store)
        except Exception:                                 # pragma: no cover
            pass

        # Agentes ANTES de rehidratar: _rehydrate puede reanudar una tarea.
        self.melchior = MelchiorAgent(self.blackboard, self.bus)
        self.balthasar = BalthasarAgent(self.blackboard, self.bus)
        self.casper = CasperAgent(self.blackboard, self.bus)

        self._rehydrate()

    def memory_for(self, task_id: str) -> EpisodicMemory:
        """Memoria episódica de la tarea (§2.6). Persistida en task_event."""
        if task_id not in self._memory:
            self._memory[task_id] = EpisodicMemory(task_id, store=self.store)
        return self._memory[task_id]

    def _rehydrate(self) -> None:
        """
        Recupera las tareas reanudables al arrancar.

        FASE 0 — antes de leer nada, reconciliar. Todo lo que figure
        `in_progress` en este momento estaba corriendo cuando el proceso murió:
        no hay ni un bucle vivo todavía. Devolverlas como `in_progress` era
        crear zombis, y un zombi con el id `default` bloqueaba la aplicación
        entera de forma permanente.
        """
        try:
            reconciliadas = self.store.reconciliar()
            if reconciliadas:
                self._reconciliadas = reconciliadas
        except Exception as e:
            logger.warning("[SWARM] no se pudo reconciliar el estado: %s", e)

        try:
            for st in self.store.resumable():
                self.active_tasks[st.task_id] = {
                    "command": st.command, "round": st.round, "status": st.status,
                    "engine": st.engine, "narrative_style": st.narrative_style,
                    "route": st.route, "max_rounds": st.max_rounds,
                    "use_tools": st.use_tools,
                    "last_proposal": st.last_proposal,
                    "last_critique": st.last_critique,
                    # Presupuesto: se rehidrata para que una tarea reanudada
                    # no estrene techo nuevo. Lo que quemó antes, cuenta.
                    "calls_used": int(st.calls_used or 0),
                    "rebuilds": int(st.rebuilds or 0),
                    "inicio_pared": time.monotonic(),
                    "approval_event": asyncio.Event(),
                }
                self.latest_task_id = st.task_id
            if self.active_tasks:
                logger.info("[SWARM] %d tarea(s) recuperadas tras reinicio: %s",
                            len(self.active_tasks), ", ".join(self.active_tasks))
        except Exception as e:
            logger.warning("[SWARM] no se pudo rehidratar el estado: %s", e)

    def _contraste_con_el_registro(self, state: dict, verdict: dict) -> str | None:
        """
        C12/P5: ¿la síntesis se atribuye algo que el registro no sostiene?

        La lógica y su historia viven en `swarm/contraste.py`. Queda este
        delegado porque dos tests llaman al método por su nombre desde el
        orquestador, y romperlos para un mudanza no aporta nada.
        """
        from magi.modules.swarm import contraste
        return contraste.con_el_registro(state, verdict)

    #: Consolas que el analizador de portabilidad conoce. Se comparan como
    #: palabra entera contra el enunciado.
    _CONSOLAS = ("psp", "vita", "nds", "ds", "n64", "gba", "snes", "ps2",
                 "dreamcast", "wii", "3ds", "gamecube", "megadrive")

    def evidencia_previa(self, command: str) -> str:
        """
        D4 — EJECUTAR la herramienta propia, no pedirle al modelo que se acuerde.

        C5 puso en el prompt «usa tus herramientas» con ejemplo. El contador
        siguió en **cero** durante dos pruebas más. Pedirlo no funciona.

        Así que aquí se ejecuta: si el enunciado nombra dos consolas conocidas,
        se corre `analyze_port` y su resultado entra en el prompt como
        evidencia ya obtenida. El modelo no tiene que acordarse de nada: se lo
        encuentra puesto, y a partir de ahí puede discutirlo, que es para lo
        que sirve un modelo.

        Es barato y local: no gasta ni una llamada de red.
        """
        from magi.modules.swarm.intencion import _plano

        t = _plano(command)
        palabras = {p.strip(".,;:!?¿¡()") for p in t.split()}
        halladas = [c for c in self._CONSOLAS if c in palabras]
        if len(halladas) < 2:
            return ""
        try:
            from magi.modules.reverse.matrix import analyze_port
            analisis = analyze_port(halladas[0], halladas[1])
        except Exception as e:                              # pragma: no cover
            logger.debug("[SWARM] analyze_port no aplicable: %s", e)
            return ""
        return ("\n\nEVIDENCIA YA OBTENIDA (herramienta `analyze_port` del "
                f"propio sistema, {halladas[0]} -> {halladas[1]}). Úsala o "
                f"refútala, pero no la ignores:\n{analisis.render()}")

    async def _cerrar_el_lazo(self, task_id: str, state: dict) -> None:
        """
        D3 — construir y entregar el artefacto, en vez de esperar a que alguien
        se acuerde.

        POR QUÉ LO HACE EL ORQUESTADOR Y NO EL MODELO
        =============================================
        En cinco pruebas medidas (16-ago a 20-ago) el sistema escribió hasta
        ocho bloques de código para encargos de `.exe` y llamó **cero veces** a
        `build_project_exe` o a `entregar_artefacto`. Se le puso en el prompt
        con ejemplo; el contador siguió en cero.

        Un LLM que a veces se acuerda no es un mecanismo. La máquina sí: si el
        contrato pide artefacto y hay código, se construye. El modelo aporta el
        contenido; la garantía la pone el código.

        NO SE INTERPONE SI YA HAY ARTEFACTO. Si el agente lo construyó por su
        cuenta —que es lo deseable— aquí no se toca nada.
        """
        from magi.modules.swarm.intencion import pide_artefacto

        if state.get("artefactos"):
            return
        if not pide_artefacto(state.get("command", "")):
            return
        contenido = (state.get("last_proposal") or {}).get("content", "") or ""

        # D10 — SI EL AGENTE YA LO CONSTRUYÓ, RECONÓCELO (Y COMPRUÉBALO).
        #
        # Medido el 20-ago, y es el fallo más irónico de la serie: Melchior
        # construyó DOS ejecutables de verdad —tkinter, 9 MB, con `--formato` y
        # `--autotest`, citando las reglas C10 y C16 del prompt— y el sistema
        # los ignoró, intentó fabricar otro desde bloques que no existían, y
        # cerró la entrega como `[INCOMPLETO]`. El trabajo estaba hecho y el
        # propio sistema decía que no.
        #
        # La causa: el estado de la tarea no se enteraba de lo que hacían las
        # herramientas del agente. Aquí se mira lo que la propuesta AFIRMA y se
        # comprueba contra el disco — que es lo mismo que hace C12, pero al
        # revés: allí para no creerse un éxito falso, aquí para no perderse uno
        # verdadero. Una ruta que existe es evidencia; una que no, es prosa.
        if await self._registrar_artefactos_del_agente(task_id, state, contenido):
            return
        if "```" not in contenido:
            return          # sin código no hay nada que construir (lo dirá C4)

        nombre = self._nombre_de_artefacto(state.get("command", ""), task_id)
        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT",
            payload={"content": f"[FÁBRICA] Construyendo «{nombre}» desde la "
                                "propuesta verificada..."}))
        try:
            from magi.modules.studio.entrega import fabricar_y_entregar
            # `fabricar_y_entregar` ES una corrutina: se espera, no se manda a
            # un hilo.
            #
            # La primera versión de esto la envolvía en `asyncio.to_thread`,
            # que es para funciones síncronas. `to_thread` la llamó, obtuvo el
            # objeto corrutina y lo devolvió sin ejecutarlo: la fábrica no
            # corría y Python solo cantaba «coroutine ... was never awaited».
            #
            # Y lo importante: los tests unitarios pasaban, porque mi doble de
            # prueba era síncrono y no se parecía a la función real. Lo cazó la
            # primera ejecución contra el sistema de verdad. Un doble que no
            # respeta la firma del original prueba otra cosa.
            informe = await fabricar_y_entregar(
                contenido, nombre=nombre, task_id=task_id, bus=self.bus)
        except Exception as e:                              # pragma: no cover
            logger.warning("[SWARM] la fábrica falló: %s", e)
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content": f"[FÁBRICA] No pude construirlo: {e}"}))
            return

        if getattr(informe, "ok", False) and getattr(informe, "ruta", None):
            state.setdefault("artefactos", []).append(str(informe.ruta))
            state["exe_path"] = str(informe.ruta)
            self._persist(task_id)
            await self.bus.publish(BusEvent(
                topic="swarm.artefacto_listo",
                payload={"task_id": task_id, "ruta": str(informe.ruta),
                         "sha256": getattr(informe, "sha256", ""),
                         "bytes": getattr(informe, "bytes_", 0)}))
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content": f"[FÁBRICA] Listo: {informe.ruta} "
                                    f"({getattr(informe, 'bytes_', 0)} bytes)"}))
        else:
            # Decirlo entero: por qué no salió. Un fallo de fábrica explicado
            # es accionable; un silencio, no.
            motivo = getattr(informe, "motivo", None) or "sin motivo registrado"
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content": f"[FÁBRICA] No se pudo entregar: {motivo}"}))

    #: Rutas absolutas a un ejecutable dentro del texto de una propuesta.
    #:
    #: Windows (`C:\...`) **y** POSIX (`/tmp/...`). La primera versión solo
    #: contemplaba la letra de unidad porque el sistema corre en Windows, y el
    #: CI —que corre en Ubuntu— tumbó los dos tests de D10 en el primer
    #: intento: la ruta del `tmp_path` de pytest no empieza por `C:`. El
    #: producto es de Windows; los tests no tienen por qué serlo, y una
    #: expresión regular que asume el sistema operativo es una que falla en
    #: cuanto alguien la ejecuta en otro.
    _RUTA_EXE = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s\"'`)*\]]+?\.exe")

    async def _registrar_artefactos_del_agente(self, task_id: str, state: dict,
                                               contenido: str) -> bool:
        """
        Artefactos que construyó el propio agente y que existen en disco (D10).

        Devuelve True si encontró alguno. La comprobación contra el sistema de
        ficheros no es desconfianza gratuita: una ruta mencionada en un texto
        generado es una afirmación, y este proyecto ya pagó caro tratar una
        afirmación como un hecho («se compiló exitosamente el binario», sin
        binario). Un fichero que existe es otra cosa.
        """
        from pathlib import Path

        vistos: list[str] = []
        for m in self._RUTA_EXE.finditer(contenido or ""):
            ruta = m.group(0)
            try:
                p = Path(ruta)
                if p.is_file() and p.stat().st_size > 0 and str(p) not in vistos:
                    vistos.append(str(p))
            except OSError:                                  # pragma: no cover
                continue
        if not vistos:
            return False

        state.setdefault("artefactos", []).extend(vistos)
        state["exe_path"] = vistos[0]
        self._persist(task_id)
        for ruta in vistos:
            tam = Path(ruta).stat().st_size
            # Se AWAITA, no se lanza y se olvida. La guarda de `test_cancel`
            # —que prohíbe tirar el handle de un `create_task`— cazó aquí la
            # primera versión, y tenía razón: una tarea sin handle es una tarea
            # que el botón de parada no puede parar.
            await self.bus.publish(BusEvent(
                topic="swarm.artefacto_listo",
                payload={"task_id": task_id, "ruta": ruta, "bytes": tam,
                         "origen": "agente"}))
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content": f"[FÁBRICA] El agente ya lo construyó y el "
                                    f"fichero existe: {ruta} ({tam} bytes)"}))
        return True

    @staticmethod
    def _nombre_de_artefacto(command: str, task_id: str) -> str:
        """
        Un nombre de fichero sacado del encargo, no un identificador opaco.

        `pong.exe` en el Escritorio se entiende; `auditoria-1787209.exe` no. Se
        cogen las palabras con letras del enunciado, se descartan las de
        relleno y se usa la primera que parezca el nombre de la cosa.
        """
        relleno = {"crea", "haz", "hazme", "un", "una", "el", "la", "de", "en",
                   "juego", "programa", "script", "con", "para", "que", "y",
                   "unico", "único", "ejecutable", "exe", "portable", "bits",
                   "todo", "color", "formato", "replica", "réplica", "del"}
        palabras = [p.strip(".,;:!?¿¡()\"'").lower()
                    for p in (command or "").split()]
        utiles = [p for p in palabras if p.isalpha() and p not in relleno]
        return (utiles[0] if utiles else f"artefacto-{task_id}")[:40]

    def _contrato_de_entregable(self, state: dict) -> str | None:
        """
        Si el encargo pedía un fichero, ¿hay fichero? (C4)

        Devuelve qué falta, o None si el contrato se cumple. Tres requisitos, y
        los tres salen de fallos medidos, no de teoría:

          · código ejecutable — el encargo del ping pong terminó con CERO
            bloques de código y una especificación en pasado;
          · verificación en verde — `verify` corrió en 0,0 s sobre el vacío y
            lo dio por bueno (eso lo cierra C7);
          · artefacto entregado — cero llamadas a `entregar_artefacto` en las
            dos pruebas de producto.

        No bloquea la conversación: marca la entrega como incompleta y lo dice.
        Bloquear dejaría al usuario sin la propuesta, que sí vale algo.
        """
        from magi.modules.swarm.intencion import pide_artefacto

        if not pide_artefacto(state.get("command", "")):
            return None
        propuesta = (state.get("last_proposal") or {}).get("content", "") or ""
        tiene_codigo = "```" in propuesta
        verificacion = state.get("verification") or {}
        hay_artefacto = bool(state.get("artefactos") or state.get("exe_path"))

        faltan = []
        if not tiene_codigo:
            faltan.append("no hay ni un bloque de código")
        if not verificacion.get("passed"):
            faltan.append("nada se ha ejecutado con éxito")
        if not hay_artefacto:
            faltan.append("no se ha generado ningún artefacto")
        return ", ".join(faltan) if faltan else None

    async def _publish_approval(self, task_id: str, state: dict,
                                verdict: dict) -> None:
        """
        Publica `swarm.approval_required` con TODO lo necesario para decidir
        (§7.4): qué ficheros toca, su contenido antes y después, y si los
        tests pasaron.

        Nunca deja caer una excepción hacia arriba. Si reunir el contexto
        falla, la aprobación se pide igual con menos información: una tarea
        que se queda colgada porque el panel de revisión reventó es peor que
        una revisión incompleta.
        """
        # C12 — la síntesis no puede afirmar hechos que el registro desmiente.
        aviso = self._contraste_con_el_registro(state, verdict)
        if aviso:
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT", payload={"content": aviso}))

        # D5 — COBERTURA DEL ENUNCIADO: ninguna promesa se pierde en silencio.
        #
        # La prueba D pedía «el orden de trabajo que minimiza el riesgo de
        # abandono». La respuesta fue buena en todo lo demás y no mencionó el
        # abandono ni una vez; nadie lo notó porque nadie llevaba la lista.
        # Ahora se lleva, y lo que falte se dice — decirlo es peor que
        # cumplirlo y muchísimo mejor que ocultarlo.
        try:
            from magi.modules.swarm import contrato as _contrato
            pendientes = _contrato.sin_cubrir(
                verdict.get("feedback", ""), state.get("contrato") or [])
            if pendientes:
                await self.bus.publish(BusEvent(
                    topic="TERMINAL_OUT",
                    payload={"content":
                             "[SIN CONTESTAR] El encargo pedía esto y no lo "
                             "veo en la respuesta: " + ", ".join(pendientes)}))
        except Exception as e:                              # pragma: no cover
            logger.debug("[SWARM] cobertura del enunciado: %s", e)

        # P2 — LOS CRITERIOS DE ACEPTACIÓN, CONTRASTADOS CONTRA LO ENTREGADO.
        #
        # Fijarlos al empezar no vale de nada si al terminar nadie los mira:
        # sería exactamente el mismo teatro que declarar «se compiló
        # exitosamente» sin binario, con un paso más.
        #
        # Se mira en el veredicto Y en la propuesta, porque el modo de prueba
        # vive en el código, no en el resumen. Y se dice lo que falta: un
        # criterio que se fijó y no se construyó es información que el usuario
        # necesita antes de fiarse del resultado.
        try:
            from magi.modules.swarm import aceptacion as _acept
            entregado = (verdict.get("feedback", "") + "\n"
                         + str((state.get("last_proposal") or {}).get("content", "")))
            faltan = _acept.sin_comprobar(entregado, state.get("aceptacion") or [])
            if faltan:
                await self.bus.publish(BusEvent(
                    topic="TERMINAL_OUT",
                    payload={"content":
                             "[SIN COMPROBAR] Esto se fijó como criterio de "
                             "aceptación al empezar y no lo veo construido: "
                             + ", ".join(faltan)}))
        except Exception as e:                              # pragma: no cover
            logger.debug("[SWARM] criterios de aceptación: %s", e)

        # C4 — contrato de entregable: si pediste un fichero, esto no se cierra
        # con un texto sobre el fichero.
        falta = self._contrato_de_entregable(state)
        if falta:
            state["entrega_incompleta"] = falta
            # D2 — Y EL VEREDICTO BAJA CON ÉL.
            #
            # La prueba E del 20-ago entregó «**Decisión Técnica:** APPROVED» y
            # «[INCOMPLETO] no se ha generado ningún artefacto» en el MISMO
            # mensaje. El contrato avisaba y el veredicto seguía diciendo que
            # todo bien: dos mecanismos que no se hablan dejan al usuario
            # eligiendo a cuál de los dos creer, y va a creer al que le gusta.
            verdict["decision"] = "INCOMPLETO"
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content":
                         "[INCOMPLETO] Pediste algo construido y esto no lo "
                         f"está: {falta}. Lo que sigue es una propuesta, no "
                         "una entrega."}))
            await self.bus.publish(BusEvent(
                topic="swarm.entrega_incompleta",
                payload={"task_id": task_id, "falta": falta}))

        try:
            from magi.core.approval import build_approval_request
            from magi.core.tools.journal import WriteJournal

            verificacion = state.get("verification") or {}
            peticion = build_approval_request(
                task_id,
                journal=WriteJournal(task_id=task_id),
                # `or {}` y no `get(..., {})`: el valor por defecto de `get`
                # solo actúa si la clave falta, no si está y vale None — que
                # es el caso de una tarea rehidratada sin propuesta.
                summary=(verdict.get("feedback")
                         or (state.get("last_proposal") or {}).get("content", "")),
                commands=list(state.get("pending_commands") or []),
                tests_ran=bool(verificacion.get("ran")),
                tests_passed=bool(verificacion.get("passed")),
                tests_detail=str(verificacion.get("detail", "")),
            )
            await self.bus.publish(BusEvent(
                topic="swarm.approval_required", payload=peticion.to_payload()))
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT", payload={"content": peticion.render()}))
        except Exception as e:                    # pragma: no cover
            logger.warning(
                "[SWARM] no se pudo reunir el contexto de aprobación: %s", e)

    async def segar_zombis(self, max_espera_s: float = 1800.0) -> list[str]:
        """
        SEGADORA DE ZOMBIS (v5.29 Fase 3): una ronda en
        WAITING_USER_APPROVAL más de `max_espera_s` (30 min) sin humano se
        avisa UNA vez al bus. No muta estado a propósito — la reanudación
        correcta es del flujo de aprobación, y falsificar un visto bueno
        sería peor que el zombi. Lo que sí elimina el zombi de verdad es la
        política de riesgo: con `autonomia=total` la mayoría ni se genera.
        """
        segadas: list[str] = []
        now = time.time()
        for tid, state in list(self.active_tasks.items()):
            if state.get("status") != "WAITING_USER_APPROVAL":
                continue
            desde = state.get("esperando_desde", 0)
            if not desde or now - desde < max_espera_s:
                continue
            if state.get("zombi_avisado"):
                continue
            state["zombi_avisado"] = True
            segadas.append(tid)
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content":
                         f"[AUTONOMÍA] {tid} lleva "
                         f"{int((now - desde) / 60)} min esperando tu "
                         f"aprobación. Responde 'sí'/'no' en esa "
                         f"conversación, o archívala desde la lista."}))
            logger.warning("[SWARM] %s es zombi: %d s en WAITING_USER_APPROVAL",
                           tid, int(now - desde))
        return segadas

    def _persist(self, task_id: str) -> None:
        """Vuelca el estado en curso. Llamar tras cada transición."""
        state = self.active_tasks.get(task_id)
        if not state:
            return
        try:
            from magi.core.store.state import TaskState
            existing = self.store.load(task_id)
            self.store.save(TaskState(
                task_id=task_id,
                command=state.get("command", ""),
                status=state.get("status", "in_progress"),
                round=state.get("round", 1),
                engine=state.get("engine", "fast"),
                narrative_style=state.get("narrative_style", "tecnico"),
                route=state.get("route", "task"),
                max_rounds=state.get("max_rounds", 3),
                use_tools=state.get("use_tools", True),
                last_proposal=state.get("last_proposal"),
                last_critique=state.get("last_critique"),
                calls_used=int(state.get("calls_used", 0)),
                rebuilds=int(state.get("rebuilds", 0)),
                created_at=existing.created_at if existing else __import__("time").time(),
            ))
        except Exception as e:
            logger.warning("[SWARM] no se pudo persistir %s: %s", task_id, e)

    def _amarrar_presupuesto(self, task_id: str) -> None:
        """Conecta el contador de llamadas de los agentes a la tarea en curso.

        Cada `_ask` que se ejecuta dentro de la tarea (variantes, ejes,
        arbitraje) incrementa `calls_used` del estado en RAM; `_persist` lo
        vuelca a disco en las transiciones siguientes. El *cierre* del cobrador
        sobre el `task_id` es lo que diferencia un enjambre multi-tarea de un
        gasto sin dueño: sin él, la última tarea en preparar a los agentes se
        comerse el contador de las demás. Reamarrado en cada alta del bucle.
        """
        def cobrar(n: int) -> None:
            st = self.active_tasks.get(task_id)
            if st is None:
                return
            st["calls_used"] = int(st.get("calls_used", 0)) + int(n)

        # C3 — y también se les da acceso de LECTURA al estado de la tarea,
        # para que el techo por iteración salga del presupuesto que queda en
        # vez de una constante. Es una función, no el diccionario: el agente
        # consulta, no muta.
        def estado_de(tid: str) -> dict | None:
            return self.active_tasks.get(tid)

        for agente in (self.melchior, self.balthasar, self.casper):
            agente.cobrar = cobrar
            agente._estado_de_tarea = estado_de

    async def _cerrar_por_presupuesto(self, task_id: str, state, p,
                                      usadas: int, pared: float) -> None:
        """Entrega lo mejor de lo debatido y cierra la tarea con motivo claro.

        No es un fallo: es una parada limpia. El usuario recibe la última
        propuesta verificada y la contabilidad real de lo gastado; la GUI
        puede proponer repetir en `deep` o afinar el encargo.
        """
        motivo = "llamadas" if usadas >= p.llamadas else "tiempo"
        mejor = state.get("last_proposal")
        informe = (
            f"[SWARM] Presupuesto agotado ({motivo}): "
            f"{int(state.get('calls_used', 0))}/{p.llamadas} llamadas · "
            f"{int(pared)}/{int(p.pared_s)} s · {state.get('round', 1)} ronda(s)"
        )
        if mejor is not None:
            informe += f"\n\n{mejor.get('content', '')[:300]}"
        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT",
            payload={"agent": "SYSTEM", "content": informe}))
        await self.bus.publish(BusEvent(
            topic="swarm.budget_exhausted",
            payload={"task_id": task_id, "motivo": motivo,
                     "calls_used": int(state.get("calls_used", 0)),
                     "techo_llamadas": p.llamadas,
                     "pared_s": int(pared), "techo_s": p.pared_s,
                     "rounds": state.get("round", 1),
                     "rebuilds": int(state.get("rebuilds", 0))}))
        state["status"] = "completed"
        self._persist(task_id)
        await self.bus.publish(BusEvent(
            topic="swarm.task_completed",
            payload={"task_id": task_id, "result": informe,
                     "motivo_cierre": f"presupuesto_{motivo}"}))

    async def submit_task(self, task_id: str, command: str, engine: str = "fast",
                          narrative_style: str = "tecnico",
                          route: str = "task", max_rounds: int = 3,
                          use_tools: bool = True):
        """Inicia un nuevo flujo de trabajo en el enjambre o resume uno pausado."""
        # LO PRIMERO: dejarlo escrito. Antes de clasificar, antes de decidir,
        # antes de nada. Si algo revienta más abajo, el mensaje del usuario ya
        # está en el libro y se ve que llegó.
        #
        # Este orden es el que hace que el fallo sea imposible, no una regla
        # que haya que recordar respetar.
        entrada = None
        try:
            entrada = self.admision.admitir(command, task_id, entrega=AHORA)
        except Exception as e:                        # pragma: no cover
            logger.warning("[SWARM] no se pudo registrar la entrada: %s", e)

        # `_despachar` puede REASIGNAR el id: si escribes «sí, apruebo», la
        # petición se absorbe en la tarea que esperaba tu visto bueno. Con el
        # id original, la entrada se archivaba bajo una tarea que no llegó a
        # existir, y el libro perdía la traza de dónde acabó el trabajo.
        # Devolverlo es lo único que lo mantiene honesto.
        destino = task_id
        try:
            async with self._lock:
                destino = await self._despachar(
                    task_id, command, engine, narrative_style,
                    route, max_rounds, use_tools, entrada) or task_id
        except Exception as e:
            if entrada is not None:
                try:
                    self.admision.fallar(entrada.id, str(e))
                except Exception:
                    pass
            raise
        else:
            # Cierre del ciclo. Cualquier camino de `_despachar` que termine
            # bien deja la entrada promovida, salvo que ya la haya encolado o
            # resuelto él mismo. Así el invariante no depende de acordarse de
            # cerrar el ciclo en cada rama: se cierra aquí, una vez.
            self._cerrar_entrada(entrada, destino)

    def _cerrar_entrada(self, entrada, task_id: str) -> None:
        if entrada is None:
            return
        try:
            with self.store._conn() as c:
                fila = c.execute(
                    "SELECT estado, entrega FROM entrada_usuario WHERE id=?",
                    (entrada.id,)).fetchone()
            if fila and fila["estado"] == "admitida" and fila["entrega"] == AHORA:
                self.admision.promover(entrada.id, task_id)
        except Exception as e:                        # pragma: no cover
            logger.warning("[SWARM] no se pudo cerrar la entrada %s: %s",
                           entrada.id, e)

    async def _despachar(self, task_id: str, command: str, engine: str,
                         narrative_style: str, route: str, max_rounds: int,
                         use_tools: bool, entrada=None):
        # Absorción de la petición en la tarea anterior.
        #
        # v5.0.28 reescribía el task_id entrante por el de la tarea previa
        # siempre que esa estuviera en WAITING_USER_APPROVAL *o* in_progress.
        # Consecuencia medida: de 25 peticiones concurrentes, 24 se perdían en
        # silencio — todas se fundían en una sola tarea. Y en uso normal, si
        # preguntabas algo nuevo mientras el enjambre pensaba, tu petición se
        # convertía sin avisar en "comentario a la propuesta anterior".
        #
        # La absorción SOLO tiene sentido cuando la tarea previa está esperando
        # respuesta del usuario: ahí sí, lo que escribes es la respuesta.
        # Nunca cuando está en progreso.
        # Y AUN ASÍ SEGUÍA TRAGÁNDOSE PREGUNTAS. El arreglo anterior acotó la
        # absorción a WAITING_USER_APPROVAL, pero eso no basta: mientras una
        # tarea espera tu visto bueno, CUALQUIER cosa que escribas se convertía
        # en su respuesta. Ocurrió tal cual —el usuario escribió "dime por que
        # la soledad duele", una pregunta nueva y sin relación, y el registro
        # dice:
        #
        #     [SWARM] task_84hkn8xp se trata como respuesta a task_29ceb5d6
        #     [SWARM] Feedback del usuario recibido. Reanudando debate (Ronda 2)
        #
        # Su pregunta no se contestó nunca: se gastó como comentario a otra
        # propuesta. Desde fuera parece que el sistema no responde, y no hay
        # forma de darse cuenta.
        #
        # Ahora se mira QUÉ has escrito, no solo en qué estado está lo
        # anterior. Solo se absorbe si de verdad parece una respuesta a la
        # pregunta pendiente; una pregunta nueva abre su propia tarea y se
        # avisa de que la otra sigue esperando.
        if (task_id not in self.active_tasks and self.latest_task_id
                and self.latest_task_id in self.active_tasks):
            prev = self.active_tasks[self.latest_task_id]
            if prev["status"] == "WAITING_USER_APPROVAL":
                if es_respuesta_a_aprobacion(command):
                    logger.info("[SWARM] %s se trata como respuesta a %s "
                                "(pendiente de aprobación)",
                                task_id, self.latest_task_id)
                    task_id = self.latest_task_id
                else:
                    logger.info("[SWARM] %s es una petición NUEVA, no una "
                                "respuesta a %s: arranca por separado",
                                task_id, self.latest_task_id)
                    await self.bus.publish(BusEvent(
                        topic="TERMINAL_OUT",
                        payload={"content":
                                 f"[SWARM] Lo tomo como pregunta nueva. La tarea "
                                 f"{self.latest_task_id} sigue esperando tu "
                                 f"aprobación; escribe 'sí' o 'apruebo' cuando "
                                 f"quieras cerrarla."}))
            else:
                logger.info("[SWARM] %s arranca en paralelo (la anterior sigue "
                            "en progreso)", task_id)

        if task_id in self.active_tasks:
            state = self.active_tasks[task_id]
            # El usuario puede cambiar motor o estilo a mitad de conversación:
            # antes se guardaban solo al crear la tarea y los cambios posteriores
            # se perdían en silencio.
            state["engine"] = engine
            state["narrative_style"] = narrative_style
            state["route"] = route
            state["max_rounds"] = max_rounds
            state["use_tools"] = use_tools
            self._persist(task_id)
            if state["status"] == "WAITING_USER_APPROVAL":
                # `in` sobre una lista de subcadenas daba aprobaciones falsas:
                # el «si» de «siempre», «análisis» o «sigue así» cerraba la
                # tarea y lanzaba la auto-ejecución de los bloques de código.
                # Ahora se comparan palabras enteras y sin acentos.
                if _aprueba(command):
                    state["status"] = "completed"
                    if "approval_event" in state:
                        state["approval_event"].set()
                    self._persist(task_id)
                    await self.bus.publish(BusEvent(
                        topic="TERMINAL_OUT",
                        payload={"content": f"[SWARM] Aprobación recibida. Tarea {task_id} finalizada exitosamente."}
                    ))

                    import re
                    # `.get("last_proposal", {})` NO protege de nada aquí: el
                    # segundo argumento solo se usa si la clave FALTA. Si está
                    # presente y vale None —que es justo lo que pasa con una
                    # tarea rehidratada que nunca llegó a producir propuesta—
                    # devuelve None y el `.get("content")` siguiente revienta
                    # con AttributeError.
                    #
                    # Se hizo alcanzable al reanudar tareas interrumpidas: se
                    # rehidratan con last_proposal=None y aprobarlas mataba el
                    # turno. Con el libro de admisión el mensaje ya no se
                    # perdía —quedaba registrado como `fallida`—, pero seguía
                    # sin ejecutarse.
                    prop = state.get("last_proposal") or {}
                    content = prop.get("content") or ""
                    blocks = re.findall(r'```(\w+)?\n(.*?)```', content, re.IGNORECASE | re.DOTALL)

                    if blocks:
                        import os

                        async def _auto_exec():
                            # MAGI 9.0 §4.2: la ejecución sigue siendo sin
                            # restricciones, pero pasa por el journal para poder
                            # deshacerla. Antes no había forma de revertir nada.
                            from magi.core.tools.journal import WriteJournal
                            journal = WriteJournal(task_id=task_id)
                            scratch_dir = workspace_dir()
                            os.makedirs(scratch_dir, exist_ok=True)

                            for i, (lang, code) in enumerate(blocks):
                                lang = lang.lower().strip() if lang else ""
                                await self.bus.publish(BusEvent(
                                    topic="TERMINAL_OUT",
                                    payload={"content": f"[AUTO-EXEC] Ejecutando bloque {i+1} ({lang or 'shell'})..."}
                                ))

                                # (v5.29 Fase 4) Los scripts se ejecutan; los
                                # artefactos se guardan con su extensión y NO
                                # se tocan. Ver _EXT_ARTEFACTO arriba.
                                if lang in ("python", "py"):
                                    temp_file = scratch_dir / f"auto_script_{i}.py"
                                    journal.record(temp_file, "create", tool="auto_exec")
                                    temp_file.write_text(code, encoding="utf-8")
                                    cmd = f"python {temp_file.name}"
                                elif lang in ("shell", "sh", "bash", "powershell", "ps1", "pwsh", ""):
                                    temp_file = scratch_dir / f"auto_script_{i}.ps1"
                                    journal.record(temp_file, "create", tool="auto_exec")
                                    temp_file.write_text(code, encoding="utf-8")
                                    cmd = f"powershell -ExecutionPolicy Bypass -File {temp_file.name}"
                                else:
                                    _ruta, aviso = guardar_bloque(
                                        scratch_dir, i, lang, code, journal)
                                    await self.bus.publish(BusEvent(
                                        topic="TERMINAL_OUT",
                                        payload={"content": aviso}))
                                    continue

                                process = await asyncio.create_subprocess_shell(
                                    cmd,
                                    cwd=str(scratch_dir),
                                    stdout=asyncio.subprocess.PIPE,
                                    stderr=asyncio.subprocess.PIPE
                                )
                                # §7.3 — EL proceso más urgente de parar: un
                                # script de un LLM en la máquina del usuario.
                                from magi.core.cancel import supervisor
                                supervisor().register_process(task_id, process)
                                try:
                                    stdout, stderr = await process.communicate()
                                finally:
                                    supervisor().forget_process(task_id, process)
                                out_msg = (stdout.decode() + "\n" + stderr.decode()).strip()
                                await self.bus.publish(BusEvent(
                                    topic="TERMINAL_OUT",
                                    payload={"content": f"Salida del bloque {i+1}:\n{out_msg}\n[Finalizado con código {process.returncode}]"}
                                ))

                        self._spawn_tracked(task_id, _auto_exec())
                    else:
                        await self.bus.publish(BusEvent(
                            topic="TERMINAL_OUT",
                            payload={"content": "[SWARM] Propuesta aprobada por el usuario. Generando resolución final estructurada y contextualizada..."}
                        ))
                        self._spawn_tracked(
                            task_id,
                            self.casper.generate_final_resolution(
                                task_id,
                                state["command"],
                                state.get("last_proposal"),
                                state.get("last_critique"),
                                engine=state.get("engine", "fast"),
                                narrative_style=state.get("narrative_style", "tecnico"),
                                use_tools=state.get("use_tools", False),
                            )
                        )
                else:
                    # El usuario NO está de acuerdo con la síntesis de Casper.
                    # La segunda ronda arranca en MELCHIOR (la tesis): se le
                    # pasa la síntesis previa de Casper + las observaciones del
                    # usuario, para que genere una tesis corregida. Después
                    # Balthasar refuta y Casper sintetiza de nuevo.
                    state["status"] = "in_progress"
                    veredicto_previo = self.blackboard.read(f"{task_id}.verdict")
                    sintesis_casper = ""
                    if isinstance(veredicto_previo, dict):
                        sintesis_casper = veredicto_previo.get("feedback", "")
                    state["command"] = (
                        f"El usuario no está de acuerdo con la síntesis de Casper. "
                        f"Estas son SUS OBSERVACIONES (respeta cada punto):\n{command}\n\n"
                        f"Esta fue la SÍNTESIS PREVIA de Casper a refinar:\n{sintesis_casper}\n\n"
                        f"Genera una TESIS corregida que integre las observaciones del usuario.")
                    state["round"] += 1
                    await self.bus.publish(BusEvent(
                        topic="TERMINAL_OUT",
                        payload={"content": f"[SWARM] Feedback del usuario recibido. Reanudando debate (Ronda {state['round']}): Melchior parte de la síntesis previa + tus observaciones."}
                    ))
                    if "approval_event" in state:
                        state["approval_event"].set()
                    else:
                        self._spawn_loop(task_id)
                return task_id
            elif state["status"] in ("in_progress", INTERRUMPIDA):
                # AQUÍ ESTABA EL FALLO QUE BLOQUEABA EL SISTEMA
                # ================================================
                # Antes:
                #     elif state["status"] == "in_progress":
                #         return   # Ignorar comandos extra mientras piensa
                #
                # Un `return` mudo: ni evento, ni fila, ni motivo. El mensaje
                # del usuario se evaporaba. Y como `_rehydrate()` resucitaba
                # las tareas `in_progress` sin volver a lanzar su bucle, una
                # tarea muerta de una sesión anterior seguía "en curso" para
                # siempre y se tragaba TODO lo que se escribiera después. La
                # fila `default` de esta máquina llevaba así desde el 8 de
                # agosto a las 22:38.
                #
                # Ahora hay tres salidas, y las tres dejan constancia:
                #
                #   1. interrumpida  -> se reanuda con la orden nueva
                #   2. viva de verdad -> se ENCOLA y se avisa
                #   3. figura viva pero no lo está -> se reconcilia y se reanuda
                #
                # El caso 2 es lo que hacen Zcode (`delivery='queue'`) y Claude
                # Code (`command_lifecycle: queued`): si el agente está
                # ocupado, la entrada espera turno. No se tira.
                await self._entrada_mientras_ocupada(task_id, state, command,
                                                     entrada)
                return task_id


        logger.info(f"[SWARM] Iniciando tarea {task_id}: {command}")
        self.latest_task_id = task_id
        # EL IDIOMA SE DECIDE AQUÍ, UNA VEZ, Y NO SE VUELVE A TOCAR.
        #
        # `command` es lo que escribió el usuario, limpio. En cuanto arranca el
        # debate, el prompt de cada agente lleva pegada la memoria de las
        # rondas anteriores, y deducir el idioma de ahí es lo que producía el
        # bucle: una sola respuesta colada en chino contaminaba el prompt de la
        # ronda siguiente, `detectar()` respondía «zh», y la guarda pasaba a
        # EXIGIR chino a los tres nodos. De protección a causa.
        #
        # Fijándolo en el origen, ninguna ronda posterior puede cambiarlo.
        from magi.core import idioma as _idioma_mod
        lang_usuario = _idioma_mod.detectar(command)
        for agente in (self.melchior, self.balthasar, self.casper):
            agente.lang_usuario = lang_usuario
        logger.info("[SWARM] idioma de la tarea fijado a '%s' desde tu mensaje",
                    lang_usuario)

        self.active_tasks[task_id] = {
            "command": command,
            "lang_usuario": lang_usuario,
            "round": 1,
            "status": "in_progress",
            "engine": engine,
            "narrative_style": narrative_style,
            "route": route,
            "max_rounds": max_rounds,
            "use_tools": use_tools,
            # Presupuesto (v6.0 §A1): techo de llamadas y tiempo de pared.
            "calls_used": 0,
            "rebuilds": 0,
            "inicio_pared": time.monotonic(),
            "approval_event": asyncio.Event(),
        }
        from magi.modules.swarm import plan as _plan_mod
        tarea_plan = _plan_mod.crear_plan_desde_enunciado(task_id, command)
        self.active_tasks[task_id]["plan"] = tarea_plan
        self._persist(task_id)
        self._amarrar_presupuesto(task_id)

        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT",
            payload={"content": f"[SWARM] Iniciando análisis para la tarea: '{command}'"}
        ))
        await self.bus.publish(BusEvent(topic="task.plan", payload=tarea_plan.a_dict()))

        # Arrancar el bucle de la conversación asíncronamente
        self._spawn_loop(task_id)
        return task_id

    async def _entrada_mientras_ocupada(self, task_id: str, state: dict,
                                        command: str, entrada) -> None:
        """
        Qué hacer con lo que escribes mientras la tarea ya está ocupada.

        Sustituye al `return` mudo. Tres caminos, y ninguno pierde el mensaje.
        """
        from magi.core.cancel import supervisor

        try:
            viva = supervisor().is_running(task_id)
        except Exception:
            viva = False

        # 1. Interrumpida, o figura viva pero no lo está. En ambos casos no hay
        #    nadie trabajando: se reanuda con la orden nueva. El segundo caso
        #    es el zombi clásico, y aquí se cura solo en vez de bloquear.
        if state["status"] == INTERRUMPIDA or not viva:
            motivo = ("reanudada tras interrupción"
                      if state["status"] == INTERRUMPIDA
                      else "figuraba en curso pero no había bucle vivo")
            logger.info("[SWARM] %s: %s. Se reanuda con la orden nueva.",
                        task_id, motivo)
            state["status"] = "in_progress"
            state["command"] = command
            state["round"] = max(1, int(state.get("round", 1)))
            self._persist(task_id)
            if entrada is not None:
                self.admision.promover(entrada.id, task_id)
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content":
                         f"[SWARM] La tarea {task_id} {motivo}. Retomo con lo "
                         f"que acabas de escribir."}))
            if "approval_event" in state and state["approval_event"].is_set():
                state["approval_event"].clear()
            self._spawn_loop(task_id)
            return

        # 2. Viva de verdad. Se ENCOLA — que es lo que hacen Zcode
        #    (delivery='queue') y Claude Code (command_lifecycle: queued) — y
        #    se dice en voz alta. El turno en curso la recogerá al terminar.
        if entrada is not None:
            try:
                with self.store._conn() as c:
                    c.execute("UPDATE entrada_usuario SET entrega=? WHERE id=?",
                              (ENCOLAR, entrada.id))
            except Exception as e:                    # pragma: no cover
                logger.warning("[SWARM] no se pudo encolar %s: %s",
                               entrada.id, e)
        pendientes = len(self.admision.en_cola(task_id))
        logger.info("[SWARM] %s ocupada; entrada encolada (%d en cola)",
                    task_id, pendientes)
        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT",
            payload={"content":
                     f"[SWARM] El enjambre está trabajando en {task_id} "
                     f"(ronda {state.get('round', 1)}). Tu mensaje queda EN "
                     f"COLA y se atiende al terminar la ronda "
                     f"({pendientes} en espera). No se ha perdido."}))
        await self.bus.publish(BusEvent(
            topic="swarm.entrada_encolada",
            payload={"task_id": task_id, "pendientes": pendientes,
                     "texto": command[:200]}))

    async def _vaciar_cola(self, task_id: str) -> bool:
        """
        Recoge lo que se encoló mientras trabajábamos.

        Se llama al cerrar una ronda. Devuelve True si había algo, para que el
        bucle sepa que tiene que seguir en vez de pararse.
        """
        siguiente = self.admision.siguiente_en_cola(task_id)
        if siguiente is None:
            return False
        state = self.active_tasks.get(task_id)
        if state is None:
            return False

        self.admision.promover(siguiente.id, task_id)
        state["status"] = "in_progress"
        state["command"] = siguiente.texto
        state["round"] = int(state.get("round", 1)) + 1
        self._persist(task_id)
        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT",
            payload={"content":
                     f"[SWARM] Retomo lo que dejaste en cola: "
                     f"«{siguiente.texto[:80]}»"}))
        return True

    async def _orchestrate_loop(self, task_id: str):
        state = self.active_tasks[task_id]
        self._amarrar_presupuesto(task_id)

        # B9 — UNA TAREA REANUDADA SIN RONDAS NO SE QUEDA MUDA.
        #
        # Reproducido sin querer el 2026-08-20: al reanudar una tarea que ya
        # estaba en su última ronda, el sistema anunciaba «Feedback recibido.
        # Reanudando debate» y después pasaban 300 segundos sin UNA sola
        # llamada al modelo. El bucle arrancaba con `round > max_rounds`, no
        # entraba en ninguna iteración y terminaba en silencio.
        #
        # Para el usuario eso es lo peor que puede pasar: escribe, el sistema
        # dice que sigue, y no vuelve a hablar nunca. Se le amplía el margen y
        # se le dice; callarse no es una opción.
        if state["status"] == "in_progress" and \
                int(state.get("round", 1)) > int(state.get("max_rounds", 3)):
            state["max_rounds"] = int(state.get("round", 1)) + 1
            self._persist(task_id)
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content":
                         "[SWARM] Esta tarea ya había agotado sus rondas. "
                         f"Amplío el margen a {state['max_rounds']} para poder "
                         "atender lo que me acabas de escribir."}))

        # El recon de una ronda que no llegó a cosecharse (p. ej. tras un
        # rebuild) no debe sobrevivir a su ronda: se cancela al reasignar.
        recon_task = None
        while state["status"] == "in_progress":
            try:
                # ---- PRESUPUESTO: el techo de esta tarea (v6.0 §A1) --------
                # Sin esto, una petición puede quemar cuota sin límite: el log
                # del 16-ago muestra ~50 llamadas HTTP para UNA petición, con
                # 6 ciclos de Melchior regenerando variantes enteras. Aquí se
                # pregunta antes de cada paso: si se pasó de llamadas o de
                # tiempo de pared, se entrega lo que haya y se cierra limpio.
                from magi.core import presupuesto as _pcto
                p = _pcto.para(state.get("engine", "fast"))
                usadas = int(state.get("calls_used", 0))
                pared = time.monotonic() - float(
                    state.get("inicio_pared", time.monotonic()))
                if usadas >= p.llamadas or pared > p.pared_s:
                    await self._cerrar_por_presupuesto(
                        task_id, state, p, usadas, pared)
                    break

                current_round = state["round"]
                logger.info(f"[SWARM] Iniciando Ronda {current_round} para {task_id}")

                engine = state.get("engine", "fast")
                style = state.get("narrative_style", "tecnico")
                # Se reafirma en cada ronda: una tarea rehidratada tras un
                # reinicio vuelve del disco con su idioma, y los agentes son
                # objetos compartidos que otra tarea pudo haber cambiado.
                if state.get("lang_usuario"):
                    for _a in (self.melchior, self.balthasar, self.casper):
                        _a.lang_usuario = state["lang_usuario"]
                # Explorar cuesta cuota: el fan-out depende del motor. `deep`
                # puede permitirse más enfoques; `fast` es frugal. Y tras un
                # fallo de verificación NO se regeneran N variantes completas:
                # la autocuración va con 1 sola (ver más abajo).
                en_rebuild = int(state.get("rebuilds", 0)) > 0
                n_variants = _n_variantes(engine, state.get("route", "task"),
                                          en_rebuild)
                use_tools = state.get("use_tools", True)

                last_proposal = state.get("last_proposal")
                last_critique = state.get("last_critique")

                # ---- 1. MELCHIOR: N enfoques EN PARALELO (§2.4) -------------
                # Antes: una sola propuesta secuencial. Ahora varias variantes
                # con semillas distintas; el tiempo de pared es el de una.
                memory = self.memory_for(task_id)
                history = memory.render_for_prompt()
                command_with_memory = (
                    f"{state['command']}\n\n{history}" if history else state["command"])
                # D1 + D4 — el encargo llega con su contrato y con la evidencia
                # que el propio sistema ya sabe producir, sin gastar una
                # llamada de red en ninguna de las dos cosas.
                from magi.modules.swarm import contrato as _contrato
                command_with_memory += _contrato.para_el_prompt(
                    _contrato.compromisos(state.get("command", "")))
                # P2 — QUÉ SIGNIFICA «HECHO», ANTES DE ESCRIBIR NADA.
                #
                # El orden es lo importante: los criterios llegan con el
                # encargo, no al final. Pedir un modo `--autotest` a un
                # programa ya escrito es pedir que alguien vuelva sobre él, y
                # eso casi nunca ocurre; exigirlo al empezar hace que el
                # programa NAZCA pudiendo comprobarse.
                #
                # Nadie de este sistema ve la pantalla —ni el enjambre, ni
                # Naoko, ni yo—, así que un programa que solo se puede juzgar
                # mirándolo es un programa que no se puede juzgar.
                #
                # Las inyecciones (aceptación, caja, bitácora, protocolo de
                # corrida) viven en `inyecciones.acumuladas`: un solo sitio
                # con la secuencia entera y su porqué.
                from magi.modules.swarm import inyecciones as _iny
                command_with_memory += _iny.acumuladas(state.get("command", ""))
                # Se anota si la herramienta llegó a correr de verdad. Sin
                # esta marca, el contraste de P5 avisaría de «cita de memoria»
                # también cuando la cita es legítima — y una alarma falsa
                # sobre trabajo bien hecho enseña a ignorar las alarmas.
                _evid = self.evidencia_previa(state.get("command", ""))
                if _evid:
                    state["evidencia_previa"] = True
                command_with_memory += _evid
                #
                # Fase 7 (abanico): el RECON de Balthasar arranca AQUÍ, a la
                # vez que Melchior redacta. La parte de la evidencia que no
                # depende de la tesis (qué se intentó antes, qué hay en el
                # workspace) se reúne durante la ventana de redacción en vez
                # de después. Nunca alarga la ronda: si no llega, se cancela
                # (`cosechar_recon`) y los ejes siguen como hoy.
                from magi.modules.swarm import abanico
                from magi.modules.swarm import subagentes as _subagentes
                crono = abanico.CronoDeRonda()
                if recon_task is not None and not recon_task.done():
                    recon_task.cancel()
                recon_task = None
                if abanico.activado() and use_tools and usadas + 3 < p.llamadas:
                    recon_task = asyncio.create_task(abanico.recon_de_encargo(
                        self.balthasar, task_id=task_id,
                        command=state.get("command", ""),
                        round_num=current_round, engine=engine,
                        narrative_style=style))
                elif False:
                    await _subagentes.despachar_subagente(
                        nodo="MELCHIOR", familia=self.melchior.family,
                        mision="recon", bus=self.bus, task_id=task_id)
                # La verificación entra en el abanico: tras_cada variante.
                verifier = ProposalVerifier()

                async def _verificar(v, _verificador=verifier):
                    return await _verificador.verify(v.content)

                crono.inicio("melchior")
                # §2 — tres filosofías, tres variantes. Ver `filosofias`.
                reparte = filosofias.pertinente(state.get("command", ""))
                if reparte:
                    n_variants = len(filosofias.FILOSOFIAS)
                variants = await generate_variants(
                    self.melchior, task_id=task_id, command=command_with_memory,
                    round_num=current_round, n=n_variants, engine=engine,
                    narrative_style=style, last_proposal=last_proposal,
                    last_critique=last_critique, use_tools=use_tools,
                    tras_cada=(_verificar if abanico.activado() else None),
                    repartir_filosofias=reparte)
                # Cada variante ya cobró su llamada vía `cobrar` en `_ask`;
                # aquí solo se informa al GUI del coste acumulado.
                usadas = int(state.get("calls_used", 0))
                await self.bus.publish(BusEvent(
                    topic="swarm.ronda",
                    payload={"task_id": task_id, "round": current_round,
                             "type": "variantes", "count": len(variants),
                             "calls_used": usadas,
                             "techo": p.llamadas}))
                state["memory_touched"] = True

                # ---- 2. VERIFICACIÓN EJECUTABLE (§2.5) ----------------------
                # Ninguna propuesta con código llega al crítico sin ejecutarse.
                # Elimina la clase de fallo más cara: tres rondas debatiendo
                # elegantemente sobre código que no compila.
                #
                # Con el abanico activo ya se ejecutó EN CASCADA durante la
                # generación; aquí solo se recoge. Con `MAGI_ABANICO=0` se
                # hace entera aquí, como antes — mismo código, dos caminos,
                # para poder medir la compuerta A/B.
                if abanico.activado():
                    reports = [v.report for v in variants
                               if v.report is not None]
                    for v in variants:
                        if v.report is not None:
                            v.verified = v.report.ok
                            v.verification = v.report.render()
                else:
                    reports = await asyncio.gather(
                        *(verifier.verify(v.content) for v in variants))
                    for v, rep in zip(variants, reports, strict=True):
                        v.verified = rep.ok
                        v.verification = rep.render()
                # El cronómetro de la fase cierra AQUÍ y no tras la
                # generación: con la cascada activa la verificación corre
                # dentro de la generación, y cerrarlo antes compararía
                # fases distintas entre modos — la compuerta mediría otra
                # cosa que la que dice medir.
                crono.fin("melchior")

                # C7 — se deja escrito si algo se comprobó DE VERDAD, no solo
                # si nada falló. El contrato de entregable (C4) lee esto, y con
                # `ok` a secas una propuesta sin una línea de código contaba
                # como verificada.
                state["verification"] = {
                    "ran": any(r.had_code for r in reports),
                    "passed": any(r.verificado for r in reports),
                    "detail": reports[0].estado if reports else "NO VERIFICADO",
                }

                good = [v for v in variants if v.verified]
                if not good and any(r.had_code for r in reports):
                    # Todas fallan: vuelve a Melchior con el traceback SIN
                    # gastar una ronda de debate... PERO CON LIMITE.
                    #
                    # El log del 16-ago mostró el fallo sin freno: 6 ciclos
                    # seguidos de «regenero las 3 variantes», ~30 llamadas
                    # quemadas, y el usuario sin nada. La autocuración tiene
                    # derecho a intentarlo `p.rebuilds` veces; después, lo
                    # que haya se debate igual y se DICE que no verificó.
                    rebuilds = int(state.get("rebuilds", 0))
                    worst = reports[0]
                    if rebuilds < p.rebuilds:
                        state["rebuilds"] = rebuilds + 1
                        await self.bus.publish(BusEvent(
                            topic="TERMINAL_OUT",
                            payload={"content":
                                     f"[VERIFICACIÓN] El código propuesto no "
                                     f"arranca. Devuelto a Melchior "
                                     f"(rebuild {state['rebuilds']}/{p.rebuilds}), "
                                     f"con 1 sola variante. Llamadas {usadas}/{p.llamadas}."}))
                        await self.bus.publish(BusEvent(
                            topic="swarm.verification_failed",
                            payload={"task_id": task_id, "round": current_round,
                                     "rebuild": state["rebuilds"],
                                     "detail": worst.render()[:2000]}))
                        for v, rep in zip(variants, reports, strict=True):
                            memory.record(round_num=current_round, approach=v.content,
                                          outcome="no_verifica",
                                          reason=(rep.failures[0].detail
                                                  if rep.failures else "no arranca"))
                        state["command"] = (f"{state['command']}\n\n"
                                            f"{worst.feedback_for_author()}")
                        await asyncio.sleep(0.5)
                        continue

                    # Límite de rebuilds alcanzado: el debate sigue, con la
                    # advertencia de que ninguna variante verificó. Preferible
                    # a una tercera ronda: lo que se pierde es la verificación,
                    # no la tarea.
                    await self.bus.publish(BusEvent(
                        topic="TERMINAL_OUT",
                        payload={"content":
                                 f"[VERIFICACIÓN] {len(variants)} variante(s) "
                                 f"sin verificar tras {p.rebuilds} intentos; se "
                                 f"debate igual. Llamadas {usadas}/{p.llamadas}."}))
                    await self.bus.publish(BusEvent(
                        topic="swarm.verificacion_agotada",
                        payload={"task_id": task_id, "rebuilds": rebuilds,
                                 "detail": worst.render()[:1000]}))

                chosen = good or variants
                proposal = {"content": format_variants_for_critic(chosen),
                            "changes": 1 if current_round > 1 else 0,
                            "variants": len(chosen)}
                self.blackboard.post(f"{task_id}.proposal", proposal)
                state["last_proposal"] = proposal
                self._persist(task_id)
                if "SYS_EMERGENCY_STOP" in proposal["content"]:
                    await self._trigger_emergency_stop(task_id, state)
                    break

                # D1 — el contrato, enseñado una vez y guardado para el final.
                if not state.get("contrato"):
                    from magi.modules.swarm import contrato as _contrato
                    lista = _contrato.compromisos(state.get("command", ""))
                    state["contrato"] = lista
                    if lista:
                        await self.bus.publish(BusEvent(
                            topic="TERMINAL_OUT",
                            payload={"content": _contrato.render(lista)}))
                    # P2 — y al lado, qué se va a considerar «hecho». Se
                    # enseña al empezar, no al terminar: el usuario tiene
                    # derecho a discutir el criterio ANTES de que se gaste
                    # media hora cumpliéndolo.
                    from magi.modules.swarm import aceptacion as _acept
                    crit = _acept.criterios(state.get("command", ""))
                    state["aceptacion"] = crit
                    if crit:
                        await self.bus.publish(BusEvent(
                            topic="TERMINAL_OUT",
                            payload={"content": _acept.render(crit)}))

                # D3 — SE CONSTRUYE AQUÍ, ANTES DE CRITICAR Y DE ARBITRAR.
                #
                # Y el orden importa más de lo que parece: si el artefacto se
                # fabrica antes, Balthasar puede refutar sobre el binario que
                # existe y Casper arbitra sabiendo que existe. Construir después
                # del veredicto convertiría la entrega en una nota al pie de una
                # decisión que ya se tomó sin ella.
                await self._cerrar_el_lazo(task_id, state)

                # El recon se cosecha AQUÍ y no antes: la fábrica de arriba es
                # tiempo local que el recon aprovecha gratis. Si no llegó, se
                # cancela y se sigue — los ejes critican sin dossier, como hoy.
                dossier = abanico.cosechar_recon(recon_task)
                evidence = "\n\n".join(
                    f"[{v.label}] {v.verification}" for v in chosen if v.verification)
                if state.get("artefactos"):
                    evidence += ("\n\n[ARTEFACTO ENTREGADO] "
                                 + ", ".join(state["artefactos"]))
                if dossier:
                    evidence += ("\n\n--- DOSSIER DE ENCARGO (recon de "
                                 "Balthasar, recogido mientras Melchior "
                                 "redactaba) ---\n" + dossier)

                # ---- LA ÚNICA INTERVENCIÓN DE MELCHIOR EN ESTA RONDA -------
                #
                # Un agente, un turno, un mensaje. Antes cada variante publicaba
                # el suyo: el usuario leía «MELCHIOR propone» tres veces
                # seguidas, con tres análisis parciales, y eso no se lee como
                # una intervención sino como un agente repitiéndose. Las
                # variantes son andamiaje para explorar; lo que se debate es el
                # resultado.
                #
                # Y va con la EVIDENCIA DE EJECUCIÓN pegada, porque Melchior no
                # solo propone: ejecuta en el mismo turno. Separar «lo que digo»
                # de «lo que comprobé» obligaba a leer dos mensajes para saber
                # si la propuesta arranca.
                melchior_msg = proposal["content"]
                if evidence:
                    melchior_msg += ("\n\n---\n**Ejecutado y verificado en este "
                                     "mismo turno:**\n\n" + evidence)
                # El proveedor y la familia son los REALES, los de quien
                # respondió, no los que el nodo tenía asignados. Es el contrato
                # del panel desde que se descubrió que la interfaz enseñaba la
                # familia esperada mientras contestaba otra: si el registro
                # conmuta, se dice. `provider` lleva el id (g4f-…) y `family`
                # la familia; confundirlos deja el panel mintiendo otra vez.
                prov_real = [v.provider for v in chosen if v.provider]
                fam_real = [v.family for v in chosen if v.family]
                await self.bus.publish(BusEvent(topic="AGENT_POST", payload={
                    "type": "AGENT_POST", "task_id": task_id, "agent": "MELCHIOR",
                    "role": "propone",
                    "provider": (", ".join(dict.fromkeys(prov_real))
                                 or f"g4f-{self.melchior.family}"),
                    "family": (fam_real[0] if fam_real else self.melchior.family),
                    "family_expected": self.melchior.family,
                    "degraded": (None if (not fam_real
                                          or fam_real[0] == self.melchior.family)
                                 else f"{self.melchior.family} no disponible; "
                                      f"respondió {fam_real[0]}"),
                    "content": melchior_msg,
                    "changes": 1 if current_round > 1 else 0,
                    "stats": (f"{len(chosen)} enfoque(s) · "
                              f"{sum(1 for v in chosen if v.verified)} verificado(s)"),
                }))

                # ---- 3. BALTHASAR: crítica multi-eje EN PARALELO (§2.4) -----
                crono.inicio("balthasar")
                multi = await critique_multi_axis(
                    self.balthasar, task_id=task_id,
                    proposal_text=proposal["content"], round_num=current_round,
                    engine=engine, narrative_style=style, use_tools=use_tools,
                    evidence=("\n\n--- EVIDENCIA DE EJECUCIÓN ---\n" + evidence)
                    if evidence else "")
                crono.fin("balthasar")
                critique = {"content": multi.render(), "status": "CRITIQUE_GENERATED",
                            "axes": multi.axes_ok}
                await self.bus.publish(BusEvent(topic="AGENT_POST", payload={
                    "type": "AGENT_POST", "task_id": task_id, "agent": "BALTHASAR",
                    "role": "critica", "provider": self.balthasar.family,
                    "family": self.balthasar.family,
                    "content": critique["content"], "changes": 0,
                    "stats": f"{multi.axes_ok} ejes"}))
                self.blackboard.post(f"{task_id}.critique", critique)
                state["last_critique"] = critique
                self._persist(task_id)
                if "SYS_EMERGENCY_STOP" in critique["content"]:
                    await self._trigger_emergency_stop(task_id, state)
                    break

                # ---- 2b. RÉPLICA (Fase 8) y 3. CASPER: el cierre del debate --
                # La mecánica vive en `replica.ronda()`: condicional (solo si
                # Balthasar FIRMA objeciones), acotada, una sola vuelta, con
                # salida por concesión — sin concesión, Casper arbitra con la
                # réplica sobre la mesa, que es arbitrar un debate y no un
                # juicio en rebeldía. Aquí queda el flujo: veredicto, parada
                # y registro.
                from magi.modules.swarm import replica as _rep
                crono.inicio("casper")
                debate = await _rep.ronda(
                    self.melchior, self.casper, task_id=task_id, multi=multi,
                    proposal=proposal, critique=critique,
                    round_num=current_round, engine=engine, style=style,
                    use_tools=use_tools, techo=p.llamadas, usadas=usadas,
                    bus=self.bus)
                crono.fin("casper")
                if debate.parar:
                    await self._trigger_emergency_stop(task_id, state)
                    break
                verdict = debate.verdict
                # La medición de la ronda viaja al bus: sin números no hay
                # compuerta. La réplica se registra en memoria permanente
                # para poder evaluar el 1-de-cada-5 con datos.
                await self.bus.publish(BusEvent(
                    topic="swarm.fases",
                    payload={"task_id": task_id, "round": current_round,
                             **crono.payload(recon=bool(dossier),
                                             **debate.evento)}))
                if debate.evento.get("fired"):
                    _rep.registrar({"task_id": task_id,
                                    "round": current_round,
                                    **debate.evento})
                self.blackboard.post(f"{task_id}.verdict", verdict)
                if "SYS_EMERGENCY_STOP" in verdict.get("feedback", ""):
                    await self._trigger_emergency_stop(task_id, state)
                    break
            except Exception as e:
                logger.error(f"[SWARM] Error catastrófico durante orquestación: {e}")
                error_msg = f"[SISTEMA] Error crítico en el Enjambre: {str(e)}. Las IAs podrían estar inoperativas."
                await self.bus.publish(BusEvent(topic="TERMINAL_OUT", payload={"agent": "SYSTEM", "content": error_msg}))
                state["status"] = "WAITING_USER_APPROVAL"
                await self.bus.publish(BusEvent(topic="swarm.task_completed", payload={"task_id": task_id, "result": error_msg}))
                break

            feedback_text = verdict.get("feedback", "").upper()
            is_asking_approval = "¿APRUEBAS" in feedback_text or "APRUEBAS" in feedback_text or verdict["decision"] == "APPROVED"

            # C1/C2 — SIN_ARBITRAJE entra por la misma puerta que una
            # aprobación, y a propósito.
            #
            # Sin esto caía al `else` final —«Tarea fallida tras N rondas»— y
            # el usuario perdía la tesis y la crítica, que estaban hechas. Una
            # tarea sin árbitro NO es una tarea fallida: es una tarea con dos
            # tercios del trabajo terminados y sin quien los cierre. Se entrega
            # lo que hay, se dice que falta el arbitraje, y decide el usuario.
            sin_arbitro = verdict["decision"] == "SIN_ARBITRAJE"

            if is_asking_approval or sin_arbitro or current_round >= state.get("max_rounds", 3):
                # Si escribiste algo mientras trabajabamos, se atiende ahora.
                if await self._vaciar_cola(task_id):
                    continue
                state["status"] = "WAITING_USER_APPROVAL"
                self._persist(task_id)

                from . import contraste as _c  # C3 (v11): sin humo no se pregunta
                humo = _c.producto_sin_humo(state, verdict)
                if humo:
                    state["status"] = "completed"
                    self._persist(task_id)
                    for tema, carga in (("TERMINAL_OUT", {"content": humo}),
                                         ("swarm.entrega_incompleta", {"task_id": task_id, "motivo": humo})):
                        await self.bus.publish(BusEvent(topic=tema, payload=carga))  # noqa: E501
                    break
                from magi.modules.swarm import cierre as _cierre
                _cierre.evaluar_cierre_entrega(
                    verdict.get("decision", ""), verdict.get("feedback", ""),
                    plan=state.get("plan"))

                # AUTONOMÍA (v5.29 Fase 3): la política de riesgo decide si
                # esta ronda necesita humano. Si no lo necesita, se reusa el
                # camino de aprobación ya probado — un _despachar con "SI"
                # sobre la tarea en espera hace EXACTAMENTE lo que haría el
                # botón Apruebo, con su ejecución de bloques y su auditoría.
                # Medido en la auditoría del Tetris: esperar al humano costaba
                # 20 minutos de bloqueo por una aprobación que nadie debía ver.
                state["esperando_desde"] = time.time()
                from magi.core import autonomia as _autonomia
                from .politica_riesgo import requiere_humano
                if not requiere_humano(state, _autonomia.nivel()):
                    await self.bus.publish(BusEvent(
                        topic="swarm.autoapproved",
                        payload={"task_id": task_id,
                                 "nivel": _autonomia.nivel(),
                                 "resumen": (verdict.get("feedback") or "")[:200]}))
                    await self.bus.publish(BusEvent(
                        topic="TERMINAL_OUT",
                        payload={"content":
                                 f"[AUTONOMÍA] Riesgo bajo (nivel "
                                 f"{_autonomia.nivel()}): {task_id} continúa "
                                 f"sola, sin aprobación humana."}))
                    self._persist(task_id)
                    await self._despachar(task_id, "SI", engine,
                                          narrative_style, route, max_rounds,
                                          use_tools)
                    break

                await self._publish_approval(task_id, state, verdict)

                await self.bus.publish(BusEvent(
                    topic="TERMINAL_OUT",
                    payload={"content": "[SWARM] Esperando tu aprobacion interactiva."}))
                # AQUÍ NO SE APARCA EL BUCLE: en v5.5.2 await approval_event colgaba la suite entera.
                # Con break la corrutina termina limpiamente y quien reanuda es _spawn_loop.
                break  # Pausar el bucle hasta recibir input del usuario
            elif verdict["decision"] == "REJECTED_NEEDS_WORK":
                self.memory_for(task_id).record(
                    round_num=current_round,
                    approach=(state.get("last_proposal") or {}).get("content", ""),
                    outcome="refutado",
                    reason=verdict.get("feedback", ""))
                state["round"] += 1
                state["command"] = f"Revisar propuesta considerando crítica: {verdict['feedback']}"
                await asyncio.sleep(1.0)
            else:
                state["status"] = "failed"
                await self.bus.publish(BusEvent(
                    topic="TERMINAL_OUT",
                    payload={"content": f"[SWARM] Tarea fallida tras {current_round} rondas."}
                ))

    def _spawn_tracked(self, task_id: str, coro) -> None:
        """
        Lanza una corrutina de fondo GUARDANDO su handle.

        Lo encontró un test que comprueba con AST que ningún `create_task`
        aparezca como sentencia suelta. Buscar la cadena no habría servido:
        `handle = create_task(...)` la contiene y es lo correcto; lo que hay
        que prohibir es tirar el resultado.

        Y los dos que quedaban eran los peores posibles — la auto-ejecución
        de un script generado por el modelo, y la resolución final tras
        aprobar. Justo lo que uno quiere poder parar.
        """
        from magi.core.cancel import supervisor
        supervisor().register_loop(task_id, asyncio.create_task(coro))

    def _spawn_loop(self, task_id: str) -> None:
        """
        Lanza el bucle de orquestación GUARDANDO su handle.

        Antes era `asyncio.create_task(self._orchestrate_loop(task_id))` a
        secas, dos veces. El handle se tiraba, así que no existía ningún
        objeto al que pedirle que parase — y por eso el botón de parada de
        emergencia no tenía nada que cancelar aunque hubiera querido.
        """
        self._spawn_tracked(task_id, self._orchestrate_loop(task_id))

    async def _trigger_emergency_stop(self, task_id: str, state: dict):
        logger.critical(f"[SWARM] EMERGENCY STOP TRIGGERED FOR TASK {task_id}")
        state["status"] = "failed"
        # Sin persistir, la fila de task_state se quedaba en `in_progress`, que
        # está en RESUMABLE: al reiniciar, `_rehydrate` devolvía a la vida la
        # tarea que se acababa de abortar por riesgo operativo.
        self._persist(task_id)

        # El mensaje anterior afirmaba estar "aplicando kill-switch local
        # automatizado" y no se aplicaba ninguno: el bucle hacía `break` y
        # cualquier subproceso lanzado seguía vivo. Ahora se para de verdad y
        # se informa de lo que se paró, no de lo que se pretendía parar.
        from magi.core.cancel import supervisor
        informe = await supervisor().stop_processes(task_id)
        muertos, fallidos = informe
        mensaje = (
            f"\n[!!!] CONTINGENCIA DE SEGURIDAD ACTIVADA [!!!]\n"
            f"Riesgo operativo confirmado; se aborta la tarea {task_id}.\n"
            f"Procesos terminados: {muertos}."
            + (f"\nAVISO: {fallidos} proceso(s) NO murieron; compruébalos a mano.\n"
               if fallidos else "\n"))
        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT",
            payload={"content": mensaje}
        ))
        await self.bus.publish(BusEvent(topic="EMERGENCY_STOP", payload={}))
