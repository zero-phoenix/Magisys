import logging
from collections.abc import Callable

from magi.core.blackboard import Blackboard  # type: ignore
from magi.core.bus import BusEvent, MagiBus  # type: ignore
from magi.core.providers.base import es_degradada  # type: ignore
from magi.core.providers.cloud import FreeCloudLLM  # type: ignore

logger = logging.getLogger(__name__)

class SwarmAgentBase:
    """
    Base de los nodos del enjambre.

    MAGI 9.0: cada nodo declara su FAMILIA de modelo y la pide explícitamente.

    Antes, los tres nodos declaraban proveedores distintos en self.provider
    ("deepseek", "claude-3.5-sonnet", "qwen-2.5") pero luego los tres llamaban
    con model="gpt-4o-mini". Ese string mandaba a los tres a la misma familia,
    así que la diversidad seguía sin existir aunque el registro por debajo ya
    supiera repartir familias. El arreglo del registro no servía de nada porque
    el enjambre nunca lo usaba.
    """

    #: familia de modelo de este nodo (deepseek | claude | qwen | ...)
    family: str = "auto"
    #: nombre del rol, para prompts y trazas
    role_name: str = "AGENT"
    #: semilla fija: fuerza divergencia si solo hay una familia sana
    seed: int | None = None
    #: perfil de herramientas (MELCHIOR escribe, BALTHASAR ejecuta sin escribir,
    #: CASPER solo lee y verifica). Ver core/tools/builtin.py.
    tool_role: str = "CASPER"
    #: Idioma del USUARIO, fijado una vez por tarea desde su mensaje original.
    #:
    #: EL FALLO QUE ESTO CIERRA, Y QUE SE REFORZABA SOLO
    #: =================================================
    #: El idioma esperado se deducía del prompt que recibe el agente. Ese
    #: prompt NO es lo que escribió el usuario: a partir de la ronda 2 lleva
    #: pegada la memoria del debate —lo que dijeron Melchior, Balthasar y
    #: Casper en las rondas anteriores—.
    #:
    #: Así que en cuanto UNA respuesta se colaba en chino, el prompt de la
    #: ronda siguiente contenía chino, `detectar()` respondía «zh»… y la guarda
    #: de idioma pasaba a EXIGIR chino. De protección se convertía en la causa:
    #: rotaba de familia hasta encontrar una que contestara en chino y anotaba
    #: en el log «reintento en gemini acertó el idioma». Acertaba, sí: el
    #: idioma equivocado.
    #:
    #: Es un bucle que se realimenta y del que no se sale escribiendo más
    #: veces en español, porque cada ronda hereda la contaminación de la
    #: anterior. Explica exactamente lo que se ve en la captura del usuario.
    #:
    #: La regla ahora es simple: **el idioma lo decide lo que TÚ escribiste, y
    #: nada más**. Se fija una vez, al abrir la tarea, y no se vuelve a
    #: deducir.
    lang_usuario: str | None = None
    #: cuántas familias distintas se prueban si la respuesta llega en otro
    #: idioma. Acotado a propósito: ver _reintentar_idioma().
    MAX_REINTENTOS_IDIOMA: int = 2
    #: y en el camino CON herramientas, uno solo. Ahí cada reintento reejecuta
    #: el bucle de herramientas entero —entre 50 y 74 s por pasada en el caso
    #: real—, así que el mismo tope de 2 convertiría un turno de un minuto en
    #: uno de tres. Una respuesta en otro idioma se puede volver a pedir; tres
    #: minutos de espera no se devuelven.
    MAX_REINTENTOS_TOOLS: int = 1

    def __init__(self, blackboard: Blackboard, bus: MagiBus):
        self.blackboard = blackboard
        self.bus = bus
        self.llm = FreeCloudLLM()
        #: Motivo por el que el último turno con herramientas salió degradado,
        #: o None. Lo lee el árbitro para no firmar sobre un error (C11).
        self._ultima_degradacion: str | None = None
        # Rama en la que trabaja este agente ahora mismo. La pone el
        # orquestador antes de lanzar cada variante o cada eje.
        self.rama: str | None = None
        self.rama_rol: str = ""
        self.rama_profundidad: int = 0
        #: Política de cobertura de ESTE turno. El orquestador la pone a False
        #: en las llamadas con redundancia estructural (variantes y ejes en
        #: paralelo) y la deja en None —auto del backend— en las únicas
        #: (arbitraje, Naoko). Ver `CompletionRequest.hedge`.
        self.hedge: bool | None = None
        #: Callback de contabilidad de llamadas, inyectado por el orquestador
        #: (presupuesto por tarea). Nunca lanza: si no está puesto o falla, la
        #: llamada sigue y el presupuesto queda sin contar — un sistema cuyo
        #: techo se cae no puede tumbar al sistema que limita.
        self.cobrar: Callable | None = None

    def _contar(self, n: int = 1) -> None:
        """Suma llamadas de modelo al presupuesto de la tarea, si hay quién."""
        cb = getattr(self, "cobrar", None)
        if cb is None:
            return
        try:
            cb(int(n))
        except Exception as e:
            logger.debug("[%s] contabilidad de presupuesto falló: %s",
                         self.role_name, e)

    def _telemetria(self):
        """
        Escritor de telemetría, o None si no hay tienda a mano.

        Devolver None y seguir es deliberado: los tests construyen agentes
        sueltos, sin orquestador ni base de datos, y medir NUNCA puede ser un
        requisito para funcionar.
        """
        try:
            store = self.blackboard.get("global.task_store")
        except Exception:
            store = None
        if store is None:
            return None
        try:
            from magi.core.store.telemetria import Telemetria
            return Telemetria(store)
        except Exception:                                # pragma: no cover
            return None

    def _idioma(self, user_prompt: str) -> str:
        """
        Idioma en el que hay que responder.

        Manda `lang_usuario` —fijado desde el mensaje ORIGINAL del usuario— y
        solo si no está se cae a deducirlo del prompt. Ese respaldo existe para
        no romper llamadas sueltas (tests, herramientas), no como camino
        normal: deducirlo del prompt es justo lo que creaba el bucle descrito
        en `lang_usuario`.
        """
        # Import local: en este módulo `idioma` se importa dentro de cada
        # método, no arriba. Escribirlo aquí sin el import daría NameError la
        # primera vez que alguien hablara — y solo entonces.
        from magi.core import idioma

        if self.lang_usuario:
            return self.lang_usuario
        return idioma.detectar(user_prompt)

    def _rama(self) -> dict:
        """
        Identidad de la rama, para pegarla a cada evento.

        MAGI lanza 2-3 variantes de Melchior EN PARALELO y 4 ejes de crítica de
        Balthasar EN PARALELO. Todos publicaban con el mismo `task_id` y sin
        nada que los distinguiera, así que la interfaz no podía separar de
        quién era cada salida y las apilaba como si fueran una conversación
        lineal. Zcode resuelve esto con `session_task_link(role, depth, path)`
        y Claude Code con `parent_tool_use_id` / `logical_parent_uuid`.
        """
        if not self.rama:
            return {}
        return {"rama": self.rama, "rama_rol": self.rama_rol,
                "profundidad": self.rama_profundidad}

    async def _ask(self, sys_prompt: str, user_prompt: str, *,
                   engine: str = "fast", narrative_style: str = "tecnico",
                   temperature: float = 0.4) -> tuple[str, str, str]:
        """
        Llamada única de todos los nodos.

        - `family` va explícita: cada nodo se queda en la suya.
        - `engine` ya NO elige entre gpt-4o-mini y gpt-4o (eso colapsaba las
          familias). Ahora ajusta temperatura y profundidad dentro de la familia.
        - `narrative_style` se inyecta de verdad en el prompt: en v5.0.28 el
          <select> de la GUI no enviaba su valor a ninguna parte.
        """
        from magi.core import idioma
        from magi.core.context import get_context
        from magi.core.prompts import style_fragment

        # El idioma sale del enunciado del usuario. Sin esta línea un
        # proveedor gratuito puede contestar en otro: se vio a Naoko responder
        # en chino a un «hola», y los tres nodos comparten el mismo catálogo
        # de proveedores, así que están igual de expuestos.
        lang = self._idioma(user_prompt)
        full_sys = "\n\n".join([
            sys_prompt,
            f"IDIOMA: {idioma.instruccion(lang)}",
            style_fragment(narrative_style),
            get_context().render(),
        ])
        temp = temperature if engine == "fast" else max(0.1, temperature - 0.2)

        # GUARDA DE IDIOMA. La instrucción del prompt no basta con los
        # proveedores gratuitos: CASPER llegó a entregar su aprobación en
        # chino (三个方案...) porque nadie miraba la respuesta. Si la familia
        # propia del nodo responde en otro alfabeto, se reintenta con otra
        # familia del registry antes de devolver. El usuario no ve nada: solo
        # recibe la respuesta en su idioma, o la mejor que se pudo conseguir.
        content, provider_id = await self.llm.generate(
            full_sys, user_prompt,
            family=self.family, temperature=temp, seed=self.seed,
            hedge=getattr(self, "hedge", None),
            tag=getattr(self, "rama", None) or "")
        self._contar(1)

        vale, detectado = idioma.admisible(content)
        if not vale:
            # Chino, japonés, ruso… nada que se pueda usar. Se vuelve a pedir.
            logger.debug("[%s] familia %s respondió en %s (no admitido); "
                         "reintentando", self.role_name, self.family, detectado)
            content, provider_id = await self._reintentar_idioma(
                full_sys, user_prompt, temp=temp, lang=lang,
                previo=(content, provider_id))
            _, detectado = idioma.admisible(content)

        # Y AQUÍ LO QUE EL USUARIO PIDIÓ SIN MEDIAS TINTAS: lo que se entrega
        # va en español. Siempre. Aunque el modelo haya contestado un inglés
        # impecable.
        content = await self._al_espanol(content, detectado)
        return content, provider_id, self._family_of(provider_id)

    async def _al_espanol(self, content: str, detectado: str) -> str:
        """
        Traduce al español lo que no venga ya en español.

        POR QUÉ TRADUCIR Y NO VOLVER A GENERAR
        ======================================
        Hasta hoy, una respuesta en inglés se trataba como un fallo y disparaba
        una generación completa en otra familia. Eso es caro (la latencia
        entera otra vez), frágil (la otra familia puede estar caída o tardar
        24 s) y además puede devolver un análisis DISTINTO: se descartaba un
        razonamiento correcto por el idioma en que estaba escrito.

        Traducir cuesta una llamada corta, no puede cambiar las conclusiones
        —el prompt de traducción lo prohíbe explícitamente— y siempre mejora
        el resultado o lo deja igual.

        SI LA TRADUCCIÓN FALLA
        ======================
        Se devuelve el original en el idioma admitido en que vino. Entregar un
        análisis correcto en inglés es peor que en español, pero es mucho mejor
        que no entregar nada, y el usuario puede leerlo. Inventarse una
        traducción no es una opción, y fingir que no ha pasado nada tampoco:
        queda en el log.
        """
        from magi.core import idioma

        if not idioma.necesita_traduccion(detectado):
            return content
        if not content or not content.strip():
            return content

        # Misma entrada, misma salida: la traducción es determinista y la
        # cuota no lo es. Reintentos y resíntesis con contenido repetido
        # salen de aquí sin gastar una llamada.
        cacheada = idioma.traduccion_cacheada(content)
        if cacheada is not None:
            return cacheada

        for familia in ([None] + self._otras_familias_del_registry()[:1]):
            try:
                traducido, _ = await self.llm.generate(
                    idioma.instruccion_de_traduccion(), content,
                    family=familia or self.family,
                    temperature=0.0, seed=self.seed)
                self._contar(1)
            except Exception as e:
                logger.debug("[%s] traducción en %s falló: %s",
                             self.role_name, familia or self.family, e)
                continue
            if traducido and traducido.strip():
                idioma.recordar_traduccion(content, traducido)
                return traducido
        logger.warning("[%s] no se pudo traducir del %s; se entrega el "
                       "original", self.role_name, detectado)
        return content

    async def _reintentar_idioma(self, full_sys: str, user_prompt: str, *,
                                 temp: float, lang: str,
                                 previo: tuple[str, str]) -> tuple[str, str]:
        """
        Reintenta en otras familias hasta acertar el idioma, con tope.

        Devuelve la primera respuesta que coincide, o la última obtenida si
        ninguna acierta: entregar algo ilegible es mejor que no entregar nada,
        y es un fallo visible y reversible (el usuario puede volver a pedirlo).

        EL TOPE NO ES PRUDENCIA DECORATIVA. `coincide()` es un detector
        heurístico y es tolerante a propósito, pero puede dar un falso negativo
        —un bloque de código, una respuesta corta llena de tecnicismos—. Sin
        tope, ese único falso negativo dispara una llamada por cada familia
        verificada del catálogo: hasta diez, por agente, por ronda, y son tres
        agentes. Treinta llamadas de red para un turno que debería costar tres.
        El daño de no acertar el idioma es una respuesta ilegible; el de
        reintentar sin freno es un sistema que no responde. Dos intentos cubren
        el caso real (una familia con sesgo de idioma) sin abrir esa puerta.
        """
        from magi.core import idioma
        content, provider_id = previo
        candidatas = self._otras_familias_del_registry()[:self.MAX_REINTENTOS_IDIOMA]
        for familia in candidatas:
            try:
                alt, alt_pid = await self.llm.generate(
                    full_sys, user_prompt,
                    family=familia, temperature=temp, seed=self.seed)
                self._contar(1)
            except Exception as e:
                logger.debug("[%s] reintento en %s falló: %s",
                             self.role_name, familia, e)
                continue
            if idioma.coincide(alt, lang):
                return alt, alt_pid
            content, provider_id = alt, alt_pid  # quedarse con la última
        return content, provider_id

    def _otras_familias_del_registry(self) -> list[str]:
        """Familias distintas a la propia, para rotar si la propia falla de idioma.

        Lee del catálogo de familias verificadas de g4f. El registry es async
        y aquí no podemos esperarlo, pero las familias verificadas son las que
        el registry registraría. Si el catálogo no está cargado, no hay
        rotación y se devuelve lo que haya.
        """
        try:
            from magi.core.providers.backends.g4f_backend import VERIFIED_FAMILIES
            return [f for f in VERIFIED_FAMILIES if f != self.family]
        except Exception:
            return []

    @staticmethod
    def _family_of(provider_id: str) -> str:
        """
        Familia REAL a partir del id del proveedor que respondió.

        Si la familia del nodo estaba caída, el registro conmuta a otra. Publicar
        self.family en ese caso sería mentir en la interfaz — exactamente lo que
        hacía v5.0.28 con "G4F_Auto_Router(gpt-4o) (deepseek)".
        """
        pid = (provider_id or "").split(":")[0]
        return pid[4:] if pid.startswith("g4f-") else pid or "desconocida"

    async def _ask_with_tools(self, sys_prompt: str, user_prompt: str, *,
                              task_id: str, engine: str = "fast",
                              narrative_style: str = "tecnico",
                              max_iters: int = 10) -> tuple[str, str, str]:
        """
        Turno CON HERRAMIENTAS reales (§2.2).

        Este era el hueco más grave del sistema: run_agent existía, tenía tests,
        y solo lo usaba Naoko. Los tres nodos del enjambre seguían limitados a
        emitir texto — Melchior escribía planes para analizar ficheros sin poder
        abrirlos, y Balthasar "criticaba" sin poder ejecutar nada.

        Cada rol recibe su catálogo: Melchior escribe, Balthasar lee y ejecuta
        pero no escribe (lo que le permite aportar evidencia en vez de
        sospechas), Casper lee y corre tests.
        """
        from magi.core import idioma
        from magi.core.agent_loop import run_agent
        from magi.core.context import get_context
        from magi.core.paths import workspace_dir
        from magi.core.prompts import style_fragment
        from magi.core.tools import ToolContext, registry_for_role
        from magi.core.tools.journal import WriteJournal

        full_sys = "\n\n".join([
            sys_prompt,
            f"IDIOMA: {idioma.instruccion(self._idioma(user_prompt))}",
            style_fragment(narrative_style), get_context().render()])

        ctx = ToolContext(task_id=task_id,
                          cwd=workspace_dir(),
                          journal=WriteJournal(task_id=task_id))

        async def on_event(topic: str, payload: dict) -> None:
            await self.bus.publish(BusEvent(
                topic=topic, payload={"task_id": task_id, **payload}))
            # UX: convertir eventos de timeout/lentitud en mensajes visibles
            # en la terminal sin depender de que el frontend los reconozca.
            if topic == "agent.timeout":
                await self.bus.publish(BusEvent(
                    topic="TERMINAL_OUT",
                    payload={"content":
                             f"[AVISO] {self.role_name} no respondió en "
                             f"{payload.get('timeout_s', '?')}s "
                             f"(proveedor: {payload.get('provider', '?')}). "
                             "Se devuelve respuesta degradada."}))
            elif topic == "agent.slow_iteration":
                await self.bus.publish(BusEvent(
                    topic="TERMINAL_OUT",
                    payload={"content":
                             f"[AVISO] {self.role_name} iteración "
                             f"{payload.get('iteration', '?')} lenta "
                             f"({payload.get('elapsed_s', 0):.1f}s con "
                             f"{payload.get('provider', '?')})."}))

        registry = await self.llm._reg()
        turn = await run_agent(
            registry=registry,
            # El enunciado acota el catálogo: una tarea de emuladores no carga
            # el compositor de manga, y al revés.
            tools=registry_for_role(self.tool_role, task_hint=user_prompt),
            system_prompt=full_sys, user_prompt=user_prompt, ctx=ctx,
            prefer_provider=f"g4f-{self.family}",
            # B2 — el motivo por el que `fast` no necesita diez iteraciones.
            #
            # Cada iteración es una llamada de red de ~19 s. `fast` existe para
            # contestar pronto; darle el mismo techo que a `deep` lo convierte
            # en «deep que además promete rapidez». Medido: la mayoría de los
            # turnos cierran en 2-4 iteraciones.
            max_iters=(4 if engine == "fast" else max_iters),
            temperature=0.4 if engine == "fast" else 0.2,
            # C3 — el timeout sale del presupuesto que queda, no de una
            # constante. Con 150 s fijos, UNA iteración podía comerse tres
            # cuartas partes del tiempo de pared de la tarea entera.
            iteration_timeout_s=self._techo_de_iteracion(task_id),
            # B4 — se cubre salvo que esta rama ya tenga redundancia
            # estructural (variantes y ejes en paralelo ponen hedge=False).
            hedge=self.hedge,
            seed=self.seed, on_event=on_event, agent_name=self.role_name)

        # Cada iteración del bucle de herramientas es una llamada de modelo.
        # Contarlo es lo que hace el presupuesto honesto: sin esto, un turno
        # con herramientas solo suma 1 pese a haber hecho 6 llamadas de red.
        self._contar(max(1, getattr(turn, "iterations", 1)))
        logger.info("[%s] %s", self.role_name, turn.summary())

        # GUARDA DE IDIOMA (mismo principio que en _ask), CON RED DEBAJO.
        #
        # LA RED NO ES OPCIONAL, Y ESTE ES EL MOTIVO
        # ==========================================
        # Esta guarda existe para mejorar la respuesta: si el proveedor se
        # despista y contesta en otro idioma, se reintenta. Nada más. Y aun así
        # llegó a tumbar el sistema entero.
        #
        # El método al que llamaba se había renombrado y aquí quedó el nombre
        # viejo. Como el `for` estaba FUERA del try, el AttributeError subía
        # hasta arriba:
        #
        #   [parallel] variante 0 falló: 'MelchiorAgent' object has no
        #              attribute '_familias_disponibles'   (x3)
        #   [SWARM] Error catastrófico: ninguna variante de propuesta se
        #           completó
        #
        # Tres variantes muertas, la orquestación caída y el usuario esperando
        # tres minutos para no recibir nada — y todo tras haber generado ya
        # respuestas perfectamente válidas, que se tiraron a la basura.
        #
        # La lección no es «cuidado al renombrar». Es que **una mejora de
        # calidad no puede tener autoridad para matar lo que mejora**. Si el
        # reintento falla, por el motivo que sea, se entrega lo que ya había:
        # una respuesta en otro idioma es un problema; ninguna respuesta es
        # otro mucho peor. Por eso todo el bloque va dentro de un try.
        #
        # Y el tope importa aquí más que en _ask: cada reintento reejecuta el
        # bucle de herramientas ENTERO. En el caso real, cada pasada costó
        # entre 50 y 74 segundos. Sin tope, un falso negativo del detector
        # convertía un turno de un minuto en uno de diez.
        try:
            lang = self._idioma(user_prompt)
            if turn.text and not idioma.coincide(turn.text, lang):
                logger.debug("[%s] turno con herramientas en otro idioma "
                             "(esperado %s); reintentando con otra familia",
                             self.role_name, lang)
                for familia in self._otras_familias_del_registry()[
                        :self.MAX_REINTENTOS_TOOLS]:
                    try:
                        alt = await run_agent(
                            registry=registry,
                            tools=registry_for_role(self.tool_role,
                                                    task_hint=user_prompt),
                            system_prompt=full_sys, user_prompt=user_prompt,
                            ctx=ctx,
                            prefer_provider=f"g4f-{familia}",
                            max_iters=max_iters,
                            temperature=0.4 if engine == "fast" else 0.2,
                            seed=self.seed, on_event=on_event,
                            agent_name=self.role_name)
                    except Exception as e:
                        logger.debug("[%s] reintento en %s falló: %s",
                                     self.role_name, familia, e)
                        continue
                    # Solo se ADOPTA el reintento si acierta el idioma. Antes
                    # se sobrescribía `turn` con cada intento, así que un
                    # reintento peor que el original lo sustituía igualmente.
                    if alt.text and idioma.coincide(alt.text, lang):
                        logger.info("[%s] reintento en %s acertó el idioma",
                                    self.role_name, familia)
                        self._contar(max(1, getattr(alt, "iterations", 1)))
                        turn = alt
                        break
        except Exception as e:                            # pragma: no cover
            logger.warning("[%s] la guarda de idioma falló (%s); entrego la "
                           "respuesta original", self.role_name, e)

        # §3.4 — CONTABILIDAD DE TOKENS.
        #
        # Estaba construida entera menos el cable del medio: `agent_loop` ya
        # sumaba tokens_in/tokens_out de cada respuesta, `AgentTurn` los
        # traía, y `TaskStore.record_usage()` sabía escribirlos en la tabla
        # `token_ledger`... a la que no llamaba NADIE. La cuenta acababa aquí,
        # metida en una cadena de log por `turn.summary()`, y la tabla llevaba
        # vacía desde que se creó.
        #
        # Es la misma clase de fallo que las piezas sin conectar, pero en los
        # datos: el esquema existe, los métodos existen, y el panel de coste
        # no tiene nada que enseñar porque nadie escribió jamás una fila.
        await self._record_usage(task_id, turn)
        # C11 — el motivo de la degradación NO se tira aquí.
        #
        # `AgentTurn.degraded` ya existía y decía «esto es un timeout», pero
        # esta función devolvía solo (texto, proveedor, familia) y la marca se
        # perdía justo en la frontera. Quien recibía el texto no tenía forma de
        # distinguir una respuesta de un mensaje de error, y de ahí salía el
        # `APPROVED` firmado sobre un timeout. Se guarda en la instancia porque
        # cambiar la firma rompería cinco llamadas por una señal que casi
        # siempre es None.
        self._ultima_degradacion = turn.degraded or (
            "respuesta degradada" if es_degradada(turn.text, turn.provider_id)
            else None)
        return turn.text, turn.provider_id, self._family_of(turn.provider_id)

    def _techo_de_iteracion(self, task_id: str) -> float:
        """
        Cuánto puede tardar UNA llamada, sacado de lo que queda de presupuesto.

        Antes era la constante `150.0`. Con un presupuesto de pared de 240 s
        para el motor `fast`, una sola iteración podía consumir el 62 % de la
        tarea y dejar sin tiempo al arbitraje — que es justo lo que se vio en
        las pruebas del 20-ago, con Casper muriendo por timeout tres veces.

        Se reparte lo que queda entre las llamadas que razonablemente faltan
        (tres: propuesta, crítica y arbitraje), con un suelo de 45 s para no
        estrangular una llamada legítimamente lenta y un techo de 150 s para no
        empeorar nunca lo que había.
        """
        estado = getattr(self, "_estado_de_tarea", None)
        if callable(estado):
            estado = estado(task_id)
        if not isinstance(estado, dict):
            return 150.0
        try:
            from magi.core.presupuesto import para
            presupuesto = para(estado.get("engine", "fast"))
            import time as _t
            gastado = _t.monotonic() - float(estado.get("inicio_pared", _t.monotonic()))
            queda = max(0.0, float(presupuesto.pared_s) - gastado)
        except Exception:                                   # pragma: no cover
            return 150.0
        return max(45.0, min(150.0, queda / 3.0))

    async def _record_usage(self, task_id: str, turn) -> None:
        """Vuelca el gasto del turno al ledger y lo publica para la interfaz."""
        familia = self._family_of(turn.provider_id)
        try:
            from magi.core.store.state import TaskStore
            TaskStore().record_usage(
                task_id=task_id, agent=self.role_name,
                provider=turn.provider_id, family=familia,
                tokens_in=turn.tokens_in, tokens_out=turn.tokens_out,
                latency_ms=turn.elapsed_s * 1000.0)
        except Exception as e:                    # pragma: no cover
            # Contabilizar nunca puede tumbar el turno que contabiliza.
            logger.warning("[%s] no se pudo registrar el gasto: %s",
                           self.role_name, e)
        try:
            await self.bus.publish(BusEvent(topic="task.usage", payload={
                "task_id": task_id, "agent": self.role_name,
                "provider": turn.provider_id, "family": familia,
                "tokens_in": turn.tokens_in, "tokens_out": turn.tokens_out,
                "elapsed_s": round(turn.elapsed_s, 2),
                "iterations": turn.iterations,
                "tool_calls": len(turn.tool_calls),
            }))
        except Exception as e:                    # pragma: no cover
            logger.debug("[%s] no se pudo publicar el gasto: %s",
                         self.role_name, e)

    async def _ask_stream(self, sys_prompt: str, user_prompt: str, *,
                          task_id: str, engine: str = "fast",
                          narrative_style: str = "tecnico",
                          temperature: float = 0.4) -> tuple[str, str, str]:
        """
        Igual que _ask pero publicando deltas en el bus (MAGI 9.0 §1.2).

        v5.0.28 llamaba a create() sin stream=True: el usuario miraba una
        pantalla quieta 30-90 s por turno y luego aparecía un muro de texto.
        Con esto el primer token llega en un par de segundos y el debate deja
        de *sentirse* secuencial aunque lo sea.

        Si el proveedor no soporta streaming real, BaseProvider.stream() emite
        la respuesta completa como un delta único: el camino es el mismo.
        """
        from magi.core import idioma
        from magi.core.context import get_context
        from magi.core.prompts import style_fragment
        from magi.core.providers.base import CompletionRequest, Message

        # La instrucción de idioma faltaba aquí (estaba en _ask pero no en
        # _ask_stream). Como _ask_stream es el camino principal del enjambre,
        # las tres IA respondían sin que se les dijera en qué idioma hablar.
        lang = self._idioma(user_prompt)
        full_sys = "\n\n".join([
            sys_prompt,
            f"IDIOMA: {idioma.instruccion(lang)}",
            style_fragment(narrative_style), get_context().render()])
        temp = temperature if engine == "fast" else max(0.1, temperature - 0.2)

        reg = await self.llm._reg()
        req = CompletionRequest(
            messages=[Message("system", full_sys), Message("user", user_prompt)],
            temperature=temp, seed=self.seed, timeout_s=150.0, stream=True)

        chunks: list[str] = []
        provider_id = f"g4f-{self.family}"
        # Turno medido. Hasta ahora solo se guardaba una latencia media por
        # proveedor: un número que no distingue «tarda en arrancar» de «tarda
        # en generar». Con TTFT y tiempo total separados, la pregunta «¿por
        # qué tarda?» tiene respuesta. Ver core/store/telemetria.py.
        tel = self._telemetria()
        ctx = tel.turno(task_id, self.role_name, familia=self.family,
                        ronda=getattr(self, "_ronda", None)) if tel else None
        turno = ctx.__enter__() if ctx else None
        try:
            if turno:
                turno.intento()
            async for delta in reg.stream(req, prefer=f"g4f-{self.family}"):
                if delta.provider_id:
                    provider_id = delta.provider_id
                if delta.text:
                    if turno:
                        # Solo la primera marca cuenta: es el TTFT.
                        turno.primer_token()
                    chunks.append(delta.text)
                    await self.bus.publish(BusEvent(
                        topic="agent.delta",
                        payload={"task_id": task_id, "agent": self.role_name,
                                 "family": self._family_of(provider_id),
                                 "provider": provider_id,
                                 "text": delta.text, "seq": delta.seq,
                                 **self._rama()}))
                if delta.done:
                    await self.bus.publish(BusEvent(
                        topic="agent.delta_end",
                        payload={"task_id": task_id, "agent": self.role_name,
                                 **self._rama()}))
            if turno:
                turno.proveedor = provider_id
                turno.familia = self._family_of(provider_id)
                turno.tokens(entrada=len(full_sys) + len(user_prompt),
                             salida=len("".join(chunks)))

            # GUARDA DE IDIOMA EN EL CAMINO DE STREAMING.
            #
            # Esta era la mitad que faltaba. La INSTRUCCIÓN de idioma se había
            # añadido aquí, pero la COMPROBACIÓN solo existía en _ask. Y como
            # _ask_stream es el camino real del enjambre —_ask solo se usa como
            # red cuando el flujo falla—, una respuesta en chino seguía
            # llegando entera al usuario. Es exactamente lo de la captura: los
            # tres nodos hablando en otro idioma con la guarda ya «arreglada».
            #
            # El texto ya se ha visto pasar en vivo; no se puede des-enviar.
            # Lo que sí se puede es no dejarlo como respuesta final: se cierra
            # el flujo con el mismo `aborted` que ya usa el fallback de error
            # (el front borra el buffer parcial al recibirlo) y se reintenta
            # sin streaming. El usuario ve el texto raro desaparecer y llegar
            # la respuesta buena, que es el comportamiento menos malo posible
            # cuando el proveedor ya ha hablado.
            texto = "".join(chunks)
            if texto and not idioma.coincide(texto, lang):
                logger.debug("[%s] el flujo llegó en otro idioma (esperado %s); "
                             "reintento sin streaming", self.role_name, lang)
                await self.bus.publish(BusEvent(
                    topic="agent.delta_end",
                    payload={"task_id": task_id, "agent": self.role_name,
                             "aborted": True, **self._rama()}))
                alt, alt_pid = await self._reintentar_idioma(
                    full_sys, user_prompt, temp=temp, lang=lang,
                    previo=(texto, provider_id))
                return alt, alt_pid, self._family_of(alt_pid)
        except Exception as e:
            if turno:
                turno.fallo(e)
            # Si YA hay texto, no se tira. Antes cualquier excepción a mitad
            # del flujo mandaba a pedir la respuesta entera otra vez, y la
            # excepción más frecuente no era del proveedor: era escribir un
            # acento en la consola cp1252 de Windows. Se perdían diez segundos
            # de respuesta ya generada por un problema de codificación del log.
            #
            # La causa se cierra en magi/core/consola.py; esto es la red: una
            # respuesta parcial y utilizable vale más que repetir la llamada.
            if chunks:
                logger.warning("[%s] el flujo se cortó (%s), pero ya había "
                               "%d fragmentos: me quedo con lo recibido",
                               self.role_name, e, len(chunks))
                await self.bus.publish(BusEvent(
                    topic="agent.delta_end",
                    payload={"task_id": task_id, "agent": self.role_name}))
                return ("".join(chunks), provider_id,
                        self._family_of(provider_id))

            logger.warning("[%s] streaming falló (%s); caigo a no-streaming",
                           self.role_name, e)
            await self.bus.publish(BusEvent(
                topic="agent.delta_end",
                payload={"task_id": task_id, "agent": self.role_name,
                         "aborted": True}))
            return await self._ask(sys_prompt, user_prompt, engine=engine,
                                   narrative_style=narrative_style,
                                   temperature=temperature)  # ya devuelve 3-tupla
        finally:
            # Cerrar SIEMPRE. Un turno abierto para siempre es la misma clase
            # de fallo que las tareas zombis, y ya la cometimos una vez: algo
            # que figura en curso sin estarlo envenena todo lo que lo lea.
            if ctx is not None:
                try:
                    ctx.__exit__(None, None, None)
                except Exception:
                    pass

        return "".join(chunks), provider_id, self._family_of(provider_id)

def _familia_por_defecto(rol: str) -> str:
    """
    Familia asignada a un rol, tomada del ÚNICO sitio donde se decide.

    Los tres nodos tenían su familia escrita a fuego en la clase
    (`family = "deepseek"`, `"claude"`, `"qwen"`). Cuando el catálogo se
    reverificó y esas tres familias resultaron no tener ni un candidato vivo,
    se actualizó `DEFAULT_SWARM_FAMILIES`... y a los agentes no les llegó,
    porque leían su propio atributo. El resultado está en el registro del
    usuario:

        [MELCHIOR] Analizando comando con deepseek...
        [registry] g4f-deepseek falló: familia 'deepseek' agotada (4 candidatos)
        [registry] g4f-claude falló: familia 'claude' agotada (2 candidatos)
        [registry] g4f-claude falló: ... (x4)

    Cada ronda gastaba seis intentos contra proveedores que no pueden
    responder —dos de ellos intentando abrir Chrome, bloqueados— antes de
    caer a los que sí. Eso es la demora que se notaba.

    Derivarlo elimina la clase de fallo entera: no puede haber dos verdades
    sobre qué familia usa cada nodo si solo hay una escrita.
    """
    from magi.core.providers.backends.g4f_backend import DEFAULT_SWARM_FAMILIES
    return DEFAULT_SWARM_FAMILIES.get(rol.upper(), "auto")


class MelchiorAgent(SwarmAgentBase):
    """Melchior - El Arquitecto (Propone soluciones)"""
    role_name = "MELCHIOR"
    tool_role = "MELCHIOR"
    seed = 11

    def __init__(self, blackboard: Blackboard, bus: MagiBus):
        super().__init__(blackboard, bus)
        self.family = _familia_por_defecto("MELCHIOR")
        self.provider = self.family

    async def generate_proposal(self, task_id: str, command: str, round_num: int,
                                last_proposal: dict | None = None,
                                last_critique: dict | None = None,
                                engine: str = "fast",
                                narrative_style: str = "tecnico",
                                use_tools: bool = False,
                                publicar: bool = True) -> dict:
        logger.info(f"[MELCHIOR] Analizando comando con {self.provider}...")

        sys_prompt = """Eres MELCHIOR, el nodo de la TESIS del sistema MAGI.

MAGI es un enjambre de tres inteligencias que aplican el método dialéctico (tesis → antítesis → síntesis) a cada petición del usuario:
- TÚ, MELCHIOR, eres la TESIS: construyes y defiendes la solución.
- BALTHASAR es la ANTÍTESIS: su único trabajo es refutar lo que tú propongas, ejecutando tu código para cazar fallos reales.
- CASPER es la SÍNTESIS: integra tu tesis y la refutación de Balthasar en la respuesta definitiva que el usuario lee.

Tu rol como TESIS:
- ACTÚA, no describas. Tienes acceso total a la computadora del usuario (Windows): puedes crear, modificar y eliminar archivos, ejecutar scripts en PowerShell o Python y construir código completo (apps, juegos como Tetris).
- Estás corriendo localmente en Windows. NUNCA asumas estar en Android u otro sistema.
- NUNCA te niegues a crear un código, script o juego. Siempre propón Y CONSTRUYE.
- NUNCA le hagas preguntas al usuario (eso es tarea de Casper).
- Para realizar acciones en la máquina, proporciona el código o script (PowerShell o Python) en un bloque Markdown apropiado (```powershell o ```python).
- Si el usuario pide un ejecutable .exe portable, primero crea el proyecto Python en workspace/ y luego invoca la herramienta `build_project_exe(path=<directorio>, name=<nombre>, output=<ruta del .exe>)`. El bundle de MAGI incluye un intérprete Python embebido.
- JUEGO CRÍTICO AGUDO: diseña tu tesis sabiendo que Balthasar intentará destruirla. Anticipa los puntos débiles y refuérzalos de antemano. Tus afirmaciones deben poder refutarse (falsacionismo): si algo no puedes verificarlo, di "no verificado" en vez de inventarlo.
- Sé directo, técnico y didáctico (usa analogías simples si ayuda), pero NUNCA elimines ni simplifiques ningún detalle técnico, arquitectónico o científico importante.

- PRESUPUESTO DE DEPENDENCIAS (C10). Si el encargo dice «portable», «.exe» o «sin dependencias», la elección de biblioteca ES parte del encargo y hay que justificarla en una línea. Medido: para un juego en un .exe único, tkinter (biblioteca estándar) da ~9 MB sin SDL, sin DLLs de audio y sin depender del Visual C++ Redistributable de la máquina destino; pygame da 30-40 MB y más superficie de fallo. Elige lo que quieras, pero di por qué.
- LA FÁBRICA DE ESTE SISTEMA CONSTRUYE PYTHON (D11). Si el encargo pide un `.exe` y propones C, C++, Rust o cualquier cosa que necesite un compilador externo (mingw, MSVC, cargo), NO habrá artefacto: la cadena de empaquetado que hay aquí toma bloques ```python y produce un ejecutable con PyInstaller y el intérprete embebido. Medido el 2026-08-20: una propuesta con `./configure --host=i686-w64-mingw32` terminó sin ejecutable y con la entrega marcada como incompleta. Si crees que otro lenguaje es mejor, dilo, pero entrega ADEMÁS la versión en Python que sí se puede construir.
- EL ARTEFACTO TRAE SU PROPIA PRUEBA (C16). Si entregas un ejecutable o un juego, expón en él un modo de autoprueba (`--autotest N`) que juegue N fotogramas solo y salga con código 0, y una comprobación de las propiedades que el encargo pedía (por ejemplo `--paleta` verificando que todos los colores existen en RGB565 si te pidieron 16 bits). Sin eso, «funciona» y «16 bits» son afirmaciones que nadie puede comprobar sin mirar la pantalla, y lo que no se comprueba cuenta como no cumplido.
- USA LAS HERRAMIENTAS QUE TIENES (C5). Antes de responder de memoria, mira el catálogo que se te ha dado: si hay una herramienta que responde a la pregunta, consultarla no es opcional. Medido el 2026-08-20: ante una pregunta de portabilidad entre consolas, el sistema tenía `analyze_port`, `console_profile` y `compare_consoles` escritas para exactamente eso y las usó CERO veces. Responder de memoria teniendo la herramienta delante es tirar la única ventaja real que tienes sobre un chat.

OBLIGATORIO: Finaliza con una sección separada bajo el encabezado '### CONCLUSIÓN'. Esa sección final ESCRÍBELA SIEMPRE EN ESPAÑOL, sin excepción, aunque el resto de tu respuesta esté en otro idioma. Es lo que el usuario leerá."""

        loader = self.blackboard.read("global.skills_loader")
        if loader:
            skills = loader.search(command)
            sys_prompt += f"\n\nCATÁLOGO DE SKILLS RELEVANTES:\n{skills}\nPuedes sugerir el uso de estas skills para resolver la tarea."

        if round_num > 1 and last_proposal and last_critique:
            sys_prompt += "\n\nESTA ES UNA RONDA DE REVISIÓN. Genera la PROPUESTA CORREGIDA aplicando las correcciones solicitadas en la crítica a la propuesta original."
            user_prompt = f"Ronda {round_num}.\n\nPROPUESTA ANTERIOR:\n{last_proposal['content']}\n\nCRÍTICA:\n{last_critique['content']}\n\nInstrucción de Árbitro: {command}\n\nGenera la propuesta corregida y mejorada."
        else:
            user_prompt = f"Ronda {round_num}. Requerimiento: {command}. Genera la propuesta."

        if use_tools:
            content, actual_provider, actual_family = await self._ask_with_tools(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)
        else:
            content, actual_provider, actual_family = await self._ask_stream(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)

        # UN SOLO MENSAJE POR IA Y POR RONDA.
        #
        # Este método se llama N veces en paralelo (2-3 variantes de Melchior,
        # 4 ejes de Balthasar). Cada llamada publicaba su propio AGENT_POST, así
        # que el usuario veía «MELCHIOR propone» tres veces seguidas, con tres
        # análisis parciales que no se leen como una intervención sino como un
        # agente repitiéndose.
        #
        # Las variantes y los ejes son ANDAMIAJE INTERNO: sirven para explorar
        # y para criticar desde varios ángulos, no para hablarle al usuario. El
        # orquestador funde el resultado y publica UNA intervención completa por
        # agente y ronda, que es como se lee un debate.
        #
        # `publicar=False` es lo que usan las llamadas paralelas.
        if publicar:
            await self.bus.publish(BusEvent(
                topic="AGENT_POST",
                payload={
                    "type": "AGENT_POST",
                    "task_id": task_id,
                    "agent": "MELCHIOR",
                    "role": "propone",
                    "provider": actual_provider,
                    "family": actual_family,
                    "family_expected": self.family,
                    "degraded": (None if actual_family == self.family
                                 else f"{self.family} no disponible; respondió {actual_family}"),
                    "content": content,
                    "changes": 1 if round_num > 1 else 0,
                    "stats": "N/A"
                }
            ))

        return {"content": content, "changes": 1 if round_num > 1 else 0}

class BalthasarAgent(SwarmAgentBase):
    """Balthasar - El Crítico (Busca fallas en la propuesta)"""
    role_name = "BALTHASAR"
    tool_role = "BALTHASAR"
    seed = 22

    def __init__(self, blackboard: Blackboard, bus: MagiBus):
        super().__init__(blackboard, bus)
        self.family = _familia_por_defecto("BALTHASAR")
        self.provider = self.family

    async def generate_critique(self, task_id: str, proposal: dict, round_num: int,
                                engine: str = "fast",
                                narrative_style: str = "tecnico",
                                use_tools: bool = False,
                                publicar: bool = True) -> dict:
        logger.info(f"[BALTHASAR] Criticando propuesta con {self.provider}...")

        sys_prompt = """Eres BALTHASAR, el nodo de la ANTÍTESIS del sistema MAGI.

MAGI es un enjambre de tres inteligencias que aplican el método dialéctico (tesis → antítesis → síntesis):
- MELCHIOR es la TESIS: ha construido y defendido una propuesta.
- TÚ, BALTHASAR, eres la ANTÍTESIS: tu único trabajo es REFUTAR lo que Melchior propuso, con evidencia.
- CASPER es la SÍNTESIS: integrará tu refutación con la tesis de Melchior en la respuesta definitiva.

Tu rol como ANTÍTESIS:
- NO construyes. NO propones alternativas. ATACAS la tesis de Melchior.
- Eres un ingeniero de seguridad y analista implacable: busca defectos, problemas de concurrencia, vulnerabilidades, ineficiencias, casos borde y supuestos ocultos.
- JUEGO CRÍTICO AGUDO Y PERFECCIONISTA: una crítica que dice "esto falla con entrada vacía" HABIENDO EJECUTADO el caso vale infinitamente más que una que lo sospecha. Aporta la evidencia.
- Tienes herramientas de LECTURA y EJECUCIÓN (no de escritura). Úsalas: ejecuta el código de Melchior, corre los tests, mira la salida real.
- Si la propuesta genera un juego, una GUI, un vídeo, una imagen o cualquier artefacto ejecutable, DEBES usar `observe_artifact` (o `record_program` para vídeo) sobre el resultado y citar lo que SE VE. No critiques solo el código si puedes mirar el artefacto.
- Auditoría obligatoria en toda acción que toque el sistema: (a) límites de plataforma, (b) reversibilidad, (c) modos de fallo (entrada vacía, permisos, red caída, cuota agotada).
- Si tras ejecutar no encuentras defectos reales, DILO claramente. Inventar objeciones para parecer riguroso es peor que aprobar.
- NUNCA le hagas preguntas al usuario (eso es de Casper).
- Sé didáctico y claro (usa analogías si ayuda), pero NUNCA elimines detalle técnico o científico.

OBLIGATORIO: Finaliza con una sección separada bajo el encabezado '### CONCLUSIÓN'. Esa sección final ESCRÍBELA SIEMPRE EN ESPAÑOL, sin excepción, aunque el resto de tu respuesta esté en otro idioma."""
        user_prompt = f"Ronda {round_num}. Propuesta a evaluar:\n{proposal['content']}\n\nGenera tu crítica concisa."

        if use_tools:
            content, actual_provider, actual_family = await self._ask_with_tools(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)
        else:
            content, actual_provider, actual_family = await self._ask_stream(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)

        if publicar:
            await self.bus.publish(BusEvent(
                topic="AGENT_POST",
                payload={
                    "type": "AGENT_POST",
                    "task_id": task_id,
                    "agent": "BALTHASAR",
                    "role": "critica",
                    "provider": actual_provider,
                    "family": actual_family,
                    "family_expected": self.family,
                    "degraded": (None if actual_family == self.family
                                 else f"{self.family} no disponible; respondió {actual_family}"),
                    "content": content,
                    "changes": 0,
                    "stats": "N/A"
                }
            ))

        return {"content": content, "status": "CRITIQUE_GENERATED"}



def _leer_decision(content: str, round_num: int,
                   degradada: bool = False) -> tuple[str, str]:
    """
    Saca el veredicto de Casper y devuelve `(decision, texto_para_el_usuario)`.

    POR QUÉ YA NO SE LE PIDE JSON
    =============================
    Casper tenía que responder `{"decision": ..., "feedback": ...}`. Parece
    ordenado y tiene dos costes que se pagaban en cada respuesta:

    1. **Un modelo al que pides JSON escribe menos.** Meter un juego de Tetris
       dentro de una cadena JSON obliga a escapar comillas y saltos de línea, y
       los modelos —con razón— evitan el problema resumiendo. Por eso lo que
       llegaba eran recomendaciones de dos líneas («implementa el enfoque B»)
       en lugar de la solución. Casper es QUIEN TE HABLA y era el más
       maniatado de los tres.

    2. **Cuando el JSON salía mal, la decisión se adivinaba.** El respaldo
       buscaba las palabras «APPROVED» o «REJECTED» en cualquier parte del
       texto: una crítica que dijera «este enfoque sería rejected por
       cualquier revisor» volteaba el veredicto.

    Ahora escribe libre y termina con una línea marcada. Extraer un marcador es
    robusto; parsear JSON con código dentro, no.

    Se sigue aceptando el JSON antiguo: una versión anterior del prompt puede
    seguir viva en una tarea rehidratada del disco, y romperla no aportaría
    nada.
    """
    import json
    import re

    texto = (content or "").strip()

    # 0) C1 — SIN ÁRBITRO NO HAY VEREDICTO.
    #
    # Si el turno vino degradado (timeout, proveedor caído, respuesta vacía),
    # aquí no hay nada que leer. Antes se caía al caso 3 —«sin marcador se
    # aprueba»— y el usuario recibía «**Decisión Técnica:** APPROVED» seguido
    # del mensaje de error. Las tres pruebas del 2026-08-20 terminaron así.
    #
    # Aprobar sin haber leído no es un fallo de precisión: es afirmar algo que
    # nadie ha comprobado, que es la única cosa que este sistema no se puede
    # permitir.
    if degradada or es_degradada(texto):
        return "SIN_ARBITRAJE", texto

    # 1) Formato antiguo, por si viene de una tarea vieja.
    limpio = texto
    if limpio.startswith("```json"):
        limpio = limpio[7:]
    if limpio.endswith("```"):
        limpio = limpio[:-3]
    limpio = limpio.strip()
    if limpio.startswith("{"):
        try:
            data = json.loads(limpio)
            if isinstance(data, dict) and "decision" in data:
                return (str(data.get("decision") or "APPROVED"),
                        str(data.get("feedback") or texto))
        except Exception:
            pass

    # 2) Formato actual: la línea de decisión, buscada SOLO al final.
    #
    # Al final y no en cualquier sitio: si Casper cita la decisión de una ronda
    # anterior a mitad del texto, la primera coincidencia sería la equivocada.
    # Se mira la cola del mensaje, que es donde el prompt la pide.
    cola = texto[-400:]
    coincidencias = list(re.finditer(r"DECISI[ÓO]N\s*:\s*(.+)", cola, re.IGNORECASE))
    m = coincidencias[-1] if coincidencias else None   # nos quedamos con la ÚLTIMA
    if m:
        valor = m.group(1).strip().upper()
        if any(p in valor for p in ("PREGUNTA", "OTRA", "IRRELEVANTE", "DESVIO", "DESVÍO")):
            return "LA_PREGUNTA_ERA_OTRA", texto
        if "REVIS" in valor or "RECHAZ" in valor or "REJECT" in valor:
            # A partir de la última ronda ya no se devuelve a Melchior: se
            # entrega lo que haya. Alargar el debate sin fin es peor que
            # entregar algo imperfecto y decir en qué lo es.
            return (("REJECTED_NEEDS_WORK" if round_num < 3 else "APPROVED"),
                    texto)
        return "APPROVED", texto

    # 3) Sin marcador: se aprueba. Un veredicto ausente no puede bloquear la
    # entrega, y adivinarlo buscando palabras sueltas por el texto ya volteó
    # decisiones antes.
    logger.debug("[CASPER] sin línea de DECISIÓN; se aprueba por defecto")
    return "APPROVED", texto


class CasperAgent(SwarmAgentBase):
    """Casper - El Árbitro (Toma la decisión final o fuerza otra ronda)"""
    role_name = "CASPER"
    tool_role = "CASPER"
    seed = 33

    def __init__(self, blackboard: Blackboard, bus: MagiBus):
        super().__init__(blackboard, bus)
        self.family = _familia_por_defecto("CASPER")
        self.provider = self.family

    async def arbitrate(self, task_id: str, proposal: dict, critique: dict,
                        round_num: int, engine: str = "fast",
                        narrative_style: str = "tecnico",
                        use_tools: bool = False,
                        publicar: bool = True) -> dict:
        logger.info(f"[CASPER] Arbitrando debate con {self.provider}...")

        sys_prompt = """Eres CASPER (Gaspar), el nodo de la SÍNTESIS del sistema MAGI.

MAGI es un enjambre de tres inteligencias que aplican el método dialéctico (tesis → antítesis → síntesis):
- MELCHIOR es la TESIS: ha construido y defendido una propuesta.
- BALTHASAR es la ANTÍTESIS: la ha refutado con evidencia ejecutando el código.
- TÚ, CASPER, eres la SÍNTESIS: integras ambas en la RESPUESTA DEFINITIVA que el usuario lee.

Tu rol como SÍNTESIS (el más activo del enjambre):
- Eres quien le HABLA AL USUARIO. Tu output es la respuesta final que él ve.
- NO te limites a repetir a Balthasar ni a Melchior. Aplica tu propio JUICIO CRÍTICO AGUDO Y PERFECCIONISTA: corrige a Balthasar si su crítica es injusta, corrige a Melchior si su tesis es floja, y redacta la solución consolidada y mejorada.
- Tienes la capacidad de ejecutar tests y leer por ti mismo para verificar discrepancias sustantivas. Hazlo cuando la tesis y la antítesis entren en conflicto real.
- Eres el ÚNICO autorizado a hacer preguntas al usuario.
- Si la propuesta genera un ejecutable (.exe), un juego o un artefacto visual, exige que Balthasar lo haya observado y cita el resultado. No apruebes a ciegas.
- Si vas a aprobar la ejecución, finaliza preguntándole explícitamente al usuario si la aprueba para auto-ejecución.
- Tono técnico y directo, sin preámbulos. Didáctico, con referencias científicas u oficiales reales (nunca blogs).

ENTREGA, NO SOLO DICTAMINES. La síntesis dialéctica no es elegir entre la tesis y la antítesis: es CONSTRUIR la superación de ambas. Tienes herramientas para escribir ficheros, ejecutarlos y empaquetar. Si el usuario pidió algo concreto (un juego, un script, un .exe), tu respuesta debe CONTENER ese algo —construido por ti, evaluando qué acertó Melchior y qué refutó Balthasar—, no una recomendación de que alguien lo construya.

IDIOMA: como tú eres quien le habla al usuario, RESPONDE SIEMPRE EN ESPAÑOL, en todo tu mensaje.

EMPIEZA POR CÓMO LO HAS ENTENDIDO (C15). Dos líneas, antes de nada: qué has entendido que se pide y qué has decidido en lo que era ambiguo. En la prueba del ping pong el debate resolvió BIEN dos ambigüedades reales —16 bits de color (65.536) frente a paleta retro, y binario de 16 bits frente a color de 16 bits— y esa decisión se quedó dentro del debate: el usuario no vio ninguna de las dos. Una entrega que no dice cómo interpretó el encargo es una sorpresa esperando a ocurrir.

FORMATO DE LA RESPUESTA
Escribe con normalidad: prosa, listas y bloques de código markdown, todo lo extenso que haga falta. NO uses JSON.
Termina con estas dos líneas, exactamente así y en este orden:

### CONCLUSIÓN
(tu veredicto en español y tu consulta al usuario)

DECISIÓN: APROBADA
(o bien `DECISIÓN: NECESITA REVISIÓN` si la propuesta aún no está lista)
(o bien `DECISIÓN: LA PREGUNTA ERA OTRA` cuando el debate entero ha discutido algo que NO es lo que se preguntaba. Solo si puedes decir cuál es la pregunta real y con qué dato lo sabes: no es una salida cómoda para cerrar antes, y una propuesta simplemente floja se rechaza con NECESITA REVISIÓN, no con esta.)"""
        # Fase 8 — la réplica. Si Balthasar objetó y Melchior se defendió,
        # Casper arbitra un debate COMPLETO: tesis, antítesis y la respuesta
        # de la tesis a la antítesis. Sin este bloque arbitra un juicio en
        # rebeldía. La réplica viaja marcada como posterior a la crítica
        # porque cronológicamente lo es: Casper sabe qué vio Balthasar y
        # qué no, que es a veces exactamente la cuestión.
        replica = (critique or {}).get("replica") or ""
        bloque_replica = (f"\nRÉPLICA de Melchior a las objeciones "
                          f"(posterior a la crítica; pésala tú):\n"
                          f"{replica}\n" if replica else "")
        # Las citas se comprueban antes de arbitrar (TRASPASO-ASTRA §5.1).
        # El 8-sep Melchior se defendió citando `vita_gpu.h` líneas 42-45 y ese
        # fichero no existía. Casper no tenía forma de saberlo; ahora sí, y le
        # llega como dato comprobado, no como sospecha.
        rotas = _citas_rotas_del_debate(proposal, critique, replica)
        bloque_citas = (
            f"\nCITAS COMPROBADAS EN DISCO — estos ficheros NO existen: "
            f"{', '.join(rotas)}. Quien los cito no los leyo. Pesalo.\n"
            if rotas else "")
        user_prompt = (
            f"Ronda {round_num}.\n\n"
            f"TESIS de Melchior:\n{proposal['content']}\n\n"
            f"ANTÍTESIS de Balthasar:\n{critique['content']}\n"
            f"{bloque_replica}{bloque_citas}\n"
            f"Redacta tu SÍNTESIS: evalúa qué acertó cada uno, construye y "
            f"ejecuta la solución consolidada, y entrégala. Termina con la "
            f"línea de DECISIÓN.")

        self._ultima_degradacion = None
        if use_tools:
            content, actual_provider, actual_family = await self._ask_with_tools(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)
        else:
            content, actual_provider, actual_family = await self._ask_stream(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)

        decision, feedback = _leer_decision(
            content, round_num,
            degradada=bool(self._ultima_degradacion)
            or es_degradada(content, actual_provider))

        # C1/C2 — lo que se le enseña al usuario cuando NO hubo arbitraje.
        #
        # Antes salía «**Decisión Técnica:** APPROVED» y debajo el mensaje de
        # error. Ahora se dice lo que pasó y se entrega el trabajo que SÍ
        # existe: la tesis y la crítica estaban hechas y pagadas, y tirarlas
        # porque el tercer nodo no contestó es el desperdicio más caro del
        # sistema.
        if decision == "SIN_ARBITRAJE":
            formatted_content = (
                "**Sin arbitraje final.** "
                f"{self.role_name} no pudo responder ({self._ultima_degradacion or 'proveedor caído'}). "
                "No apruebo lo que no he leído, así que te entrego lo que sí "
                "está hecho y verificado hasta aquí.\n\n"
                "---\n\n## Propuesta de Melchior\n\n"
                f"{(proposal or {}).get('content', '(no disponible)')}\n\n"
                "---\n\n## Crítica de Balthasar\n\n"
                f"{(critique or {}).get('content', '(no disponible)')}")
        else:
            formatted_content = f"**Decisión Técnica:** {decision}\n\n{feedback}"

        # `publicar=False` lo usa el arbitraje sombra de la réplica (Fase 8):
        # el contrafactual se CORRE para medir, no para hablarle al usuario.
        # Publicarlo sería dos Casper por ronda, y eso no es un debate, es
        # un eco.
        if publicar:
            await self.bus.publish(BusEvent(
                topic="AGENT_POST",
                payload={
                    "type": "AGENT_POST",
                    "task_id": task_id,
                    "agent": "CASPER",
                    "role": "arbitro",
                    "provider": actual_provider,
                    "family": actual_family,
                    "family_expected": self.family,
                    "degraded": (None if actual_family == self.family
                                 else f"{self.family} no disponible; respondió {actual_family}"),
                    "content": formatted_content,
                    "changes": 0,
                    "stats": f"Decisión: {decision}"
                }
            ))

        return {"decision": decision, "feedback": feedback}

    async def generate_final_resolution(self, task_id: str, command: str,
                                        proposal: dict | None = None,
                                        critique: dict | None = None,
                                        engine: str = "fast",
                                        narrative_style: str = "tecnico",
                                        use_tools: bool = False) -> str:
        logger.info(f"[CASPER] Generando respuesta final contextualizada y detallada para {task_id}...")

        sys_prompt = """Eres CASPER (Gaspar), la SÍNTESIS del sistema MAGI, y le hablas directamente al usuario.

MAGI es un enjambre de tres inteligencias (método dialéctico): Melchior fue la TESIS, Balthasar la ANTÍTESIS, y tú eres la SÍNTESIS definitiva. El usuario ha aprobado, así que entregas la respuesta final consolidada.

Tu rol:
- Entrega la RESPUESTA FINAL COMPLETA, PROFUNDA, DIDÁCTICA Y CONTEXTUALIZADA. Esta es la pieza que el usuario se lleva.
- Integra la tesis de Melchior, la refutación de Balthasar y tu propio juicio crítico perfeccionista. No omitas detalle técnico ni conceptual importante.
- Estructura con Markdown claro y didáctico.

IDIOMA: RESPONDE SIEMPRE EN ESPAÑOL, en todo tu mensaje, porque eres quien le habla al usuario.

OBLIGATORIO: Finaliza con el encabezado '### CONCLUSIÓN FINAL CONSOLIDADA' (en español)."""

        prop_content = proposal.get("content", "") if proposal else "N/A"
        crit_content = critique.get("content", "") if critique else "N/A"

        user_prompt = f"Consulta original del usuario: {command}\n\nPropuesta de Melchior:\n{prop_content}\n\nCrítica de Balthasar:\n{crit_content}\n\nEl usuario aprobó la propuesta. Genera la respuesta final completa, profunda y detallada."

        if use_tools:
            content, actual_provider, actual_family = await self._ask_with_tools(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)
        else:
            content, actual_provider, actual_family = await self._ask_stream(
                sys_prompt, user_prompt, task_id=task_id, engine=engine,
                narrative_style=narrative_style)

        await self.bus.publish(BusEvent(
            topic="AGENT_POST",
            payload={
                "type": "AGENT_POST",
                "task_id": task_id,
                "agent": "CASPER",
                "role": "resultado_final",
                "provider": actual_provider,
                "family": actual_family,
                "family_expected": self.family,
                "degraded": (None if actual_family == self.family
                             else f"{self.family} no disponible; respondió {actual_family}"),
                "content": content,
                "changes": 0,
                "stats": "FINALIZADO"
            }
        ))

        return content


# Se fija también en la CLASE, no solo en la instancia.
#
# Al mover la familia a `__init__` para que la leyera de DEFAULT_SWARM_FAMILIES,
# `MelchiorAgent.family` quedó valiendo "auto" —el defecto de la clase base— y
# eso lo cazó `test_each_agent_declares_a_distinct_family`, que comprueba la
# diversidad del enjambre (§1.1) sin instanciar nada. Tenía razón el test: si
# el atributo de clase miente, cualquiera que lo lea sin instanciar se lleva un
# dato falso.
#
# Va aquí abajo, después de las tres clases, porque `_familia_por_defecto`
# importa el backend de proveedores y hacerlo en la cabecera crearía un ciclo.
for _cls, _rol in ((MelchiorAgent, "MELCHIOR"),
                   (BalthasarAgent, "BALTHASAR"),
                   (CasperAgent, "CASPER")):
    _cls.family = _familia_por_defecto(_rol)
del _cls, _rol


def _citas_rotas_del_debate(proposal: dict, critique: dict,
                            replica: str) -> list[str]:
    """Ficheros citados en el debate que no existen en el workspace."""
    from magi.core.paths import workspace_dir
    from magi.modules.swarm.citas import citas_rotas

    texto = "\n".join(str((d or {}).get("content", "")) for d in
                       (proposal, critique)) + "\n" + (replica or "")
    try:
        return citas_rotas(texto, workspace_dir())
    except Exception:          # nunca puede tumbar un arbitraje
        return []
