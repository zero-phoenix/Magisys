import asyncio
import logging

from magi.core.obs.bus_log_handler import BusLogHandler
from magi.core.paths import db_path, workspace_dir
from magi.core.store.database import MagiDatabase
from magi.core.store.logger import BusLogger
from magi.modules.infrastructure.naoko import NaokoAgent
from magi.modules.memgraph import MemGraphAdapter
from magi.modules.skills.loader import AASLoader
from magi.modules.swarm.orchestrator import SwarmOrchestrator

from .blackboard import Blackboard
from .bus import BusEvent, MagiBus
from .policy.engine import Capability, PolicyEngine
from .rpc.ws_server import WSServer

logger = logging.getLogger(__name__)

class Kernel:
    """
    Núcleo (Área 0). Único dueño del estado y bucle principal.
    Amalgama el Bus, el servidor RPC, y las áreas de dominio.
    """
    def __init__(self, host="127.0.0.1", port=20128):
        self.bus = MagiBus()
        self.db = MagiDatabase(db_path=str(db_path()))
        self.bus_logger = None # Se inicializa en start()

        self.blackboard = Blackboard()
        self.swarm = SwarmOrchestrator(self.blackboard, self.bus)
        self.policy = PolicyEngine()
        self.memgraph = MemGraphAdapter(self.bus)
        # §3.4 — observabilidad. Enganchado al bus para que Naoko vea
        # degradación, no solo excepciones.
        from magi.core.obs.metrics import MetricsCollector
        self.metrics = MetricsCollector()
        self.metrics.attach(self.bus)
        self.naoko = NaokoAgent(self.bus, self.db, swarm=self.swarm,
                                metrics=self.metrics)
        # RITSUKO — quien revisa a quien corrige. Naoko diagnostica al enjambre
        # y aplica cambios; hasta hoy nadie comprobaba a Naoko, y un corrector
        # equivocado mueve el sistema entero en la dirección equivocada con
        # toda la autoridad. Ritsuko solo informa: no arregla nada a propósito
        # (ver la cabecera de ritsuko.py) y usa una familia que no comparte con
        # ninguno de los cuatro, porque un auditor que se cae cuando se cae el
        # auditado no sirve justo el día que hace falta.
        from magi.modules.infrastructure.ritsuko import RitsukoAgent
        self.ritsuko = RitsukoAgent(self.bus, self.db, swarm=self.swarm,
                                    naoko=self.naoko, metrics=self.metrics)

        # Cargar catálogo de Skills
        self.skills_loader = AASLoader()
        loaded_count = self.skills_loader.load()
        if loaded_count > 0:
            self.blackboard.post("global.skills_loader", self.skills_loader)

        self.rpc = WSServer(bus=self.bus, host=host, port=port)
        self._setup_rpc()

    def _setup_rpc(self):
        self.rpc.register_handler("rpc.hello", self._handle_hello)
        self.rpc.register_handler("rpc.policy.check", self._handle_policy_check)
        self.rpc.register_handler("magi_connect", self._handle_connect)
        self.rpc.register_handler("magi_estop", self._handle_estop)
        self.rpc.register_handler("EMERGENCY_STOP", self._handle_estop)
        self.rpc.register_handler("KILL_ALL_PROCESSES", self._handle_estop)
        # §7.3 — parar UN turno sin matar la aplicación ni las demás tareas.
        self.rpc.register_handler("task.cancel", self._handle_cancel_task)
        self.rpc.register_handler("task.running", self._handle_running_tasks)
        self.rpc.register_handler("task.list", self._handle_list_tasks)
        self.rpc.register_handler("task.archive", self._handle_archive_task)
        self.rpc.register_handler("task.delete", self._handle_delete_task)
        # Ciclo de mejora de Naoko: proponer, decidir en cada compuerta y
        # consultar lo que está pendiente de tu respuesta.
        self.rpc.register_handler("naoko.improve.propose", self._handle_improve_propose)
        self.rpc.register_handler("naoko.improve.decide", self._handle_improve_decide)
        self.rpc.register_handler("naoko.improve.list", self._handle_improve_list)
        self.rpc.register_handler("SYS_EXEC", self._handle_sys_exec)
        self.rpc.register_handler("rpc.state.sync", self._handle_state_sync)
        self.rpc.register_handler("git.clone", self._handle_git_clone)
        self.rpc.register_handler("naoko.chat", self._handle_naoko_chat)
        # Canal propio de Ritsuko: separado del de Naoko a propósito. Mezclar
        # al corrector y a su auditora en la misma conversación es la forma más
        # rápida de no saber quién dijo qué.
        # Los handlers viven en la propia Ritsuko, no aquí: el kernel ya roza
        # su techo de líneas y, sobre todo, la superficie RPC de una pieza se
        # entiende mejor al lado de la pieza que en una lista de cien.
        self.rpc.register_handler("ritsuko.chat", self.ritsuko.rpc_chat)
        self.rpc.register_handler("ritsuko.informes", self.ritsuko.rpc_informes)
        self.rpc.register_handler("obs.metrics", self._handle_metrics)
        self.rpc.register_handler("naoko.self_improve", self._handle_self_improve)
        self.rpc.register_handler("eval.run", self._handle_eval_run)
        # §GUI — la pestaña Configuración estaba en la barra sin nada detrás:
        # se pulsaba y no aparecía nada. Y Vista previa apuntaba a un
        # localhost:3000 que nadie levanta, así que el usuario veía la página
        # de error del navegador. Estos dos endpoints les dan contenido real.
        self.rpc.register_handler("sys.config", self._handle_config)
        self.rpc.register_handler("artifacts.list", self._handle_artifacts_list)
        self.rpc.register_handler("artifacts.read", self._handle_artifacts_read)

    async def _handle_metrics(self, payload, websocket):
        """Panel de salud (§3.4): latencias, herramientas, alertas."""
        return self.metrics.snapshot()

    async def _handle_config(self, payload, websocket):
        """
        Configuración REAL del sistema, leída del sistema, no de una copia.

        Todo lo que devuelve se consulta en vivo: el reparto del enjambre sale
        del registro de proveedores, las latencias de las medidas por cada
        backend y el estado del cortafuegos de su propia sonda. Una pantalla de
        configuración que muestre valores escritos a mano es justo la clase de
        cosa que este proyecto ha estado desmontando.
        """
        from magi import __version__
        from magi.core import no_browser, paths
        from magi.core import autonomia

        # v5.29: el nivel de autonomía se cambia AQUÍ, por RPC — es la vía
        # que usa la GUI y la prueba estelar headless. Un valor inválido no
        # tumba la consulta: se ignora y el nivel sigue siendo el anterior.
        if isinstance(payload, dict) and payload.get("autonomia"):
            if not autonomia.fijar(str(payload["autonomia"])):
                logger.warning("[kernel] autonomia inválida: %r",
                               payload.get("autonomia"))
        from magi.core.providers.backends.g4f_backend import (
            FAMILY_SPECS,
            HEDGE_AFTER_S,
            HEDGE_MAX,
            VERIFIED_FAMILIES,
        )
        from magi.core.providers.cloud import get_registry
        from magi.core.tools import ALL_DOMAINS, registry_for_role

        reg = await get_registry()
        asignacion = reg.select_for_swarm()

        familias = []
        for r in reg.all():
            medidas = getattr(r.provider, "_latencia", {}) or {}
            familias.append({
                "id": r.id,
                "familia": r.family,
                "prioridad": r.priority,
                "verificada": r.family in VERIFIED_FAMILIES,
                "disponible": r.available,
                "en_rotacion": r.breaker.allows(),
                "llamadas": r.calls,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "candidatos": [
                    {"proveedor": n, "modelo": m or "(por defecto)",
                     "latencia_ms": round(medidas.get((n, m), 0)) or None}
                    for n, m in FAMILY_SPECS.get(r.family, [])
                ],
                # Por qué cada candidato NO se intenta. Sin esto, una familia
                # agotada se veía como una lista de proveedores sin explicar,
                # y no había forma de saber si faltaba instalar algo, si era
                # cuota o si el cortafuegos lo había cortado.
                "descartados": (r.provider.motivos_descartados()  # type: ignore[attr-defined]
                                if hasattr(r.provider, "motivos_descartados")
                                else {}),
                # Breaker de CALIDAD por candidato (v5.29 Fase 2): quién está
                # ABIERTO por responder basura. El router a ciegas era esto.
                "calidad": (r.provider._calidad.snapshot()  # type: ignore[attr-defined]
                            if hasattr(r.provider, "_calidad") else {}),
            })

        # `task_hint=""` a propósito, y explícito para que se vea que es una
        # decisión: aquí SÍ se quiere el catálogo entero. Esto es una pantalla
        # de consulta, no un prompt — enseña de qué es capaz cada rol, no lo
        # que se le va a mandar en una tarea concreta. Sin el argumento, el
        # guard `test_nadie_pide_el_catalogo_sin_acotar` no puede distinguir
        # este caso legítimo del descuido que existe para cazar.
        herramientas = {rol: sorted(registry_for_role(rol, task_hint="").names())
                        for rol in ("MELCHIOR", "BALTHASAR", "CASPER")}

        return {
            "version": __version__,
            # Nivel de autonomía vigente (v5.29). Consultable y cambiable
            # por RPC: sys.config con {"autonomia": "manual|supervisada|total"}.
            "autonomia": autonomia.nivel(),
            "enjambre": {"reparto": asignacion.by_role,
                         "familias": asignacion.families,
                         "diversidad": asignacion.diversity,
                         "nota": asignacion.note},
            "familias": familias,
            "inferencia": {"hedge_after_s": HEDGE_AFTER_S,
                           "hedge_max": HEDGE_MAX,
                           "cache_entradas": len(reg.cache),
                           "familias_verificadas": list(VERIFIED_FAMILIES)},
            "herramientas": herramientas,
            "dominios": sorted(ALL_DOMAINS),
            "rutas": paths.describe(),
            "cortafuegos": no_browser.self_test(),
            "violaciones": no_browser.violations()[:5],
            # De dónde salen los datos de proveedores y si se pueden editar sin
            # recompilar. Antes esto no se podía ni preguntar.
            "catalogo": self._info_catalogo(),
            # Si esta base está al día. Un usuario con la base a medias tenía
            # fallos incomprensibles y ninguna forma de verlo.
            "esquema": self._info_esquema(),
            # El libro de admisión: cuántas entradas hay en cola y cuántas se
            # quedaron sin resolver. Cero perdidas es el estado correcto.
            "admision": self._info_admision(),
            "diagnostico": self._info_diagnostico(),
            # Dónde se va el tiempo. Cuatro números en vez de una media.
            "telemetria": self._info_telemetria(),
            # Qué ha tocado NAOKO en tu repositorio, y si la traza está intacta.
            "auditoria": self._info_auditoria(),
            # Latencia MEDIDA por candidato, con media histórica de las medias
            # diarias. La columna del panel se llamaba «latencia medida» y casi
            # todo decía «sin medir»; esto es lo que la llena.
            "sonda": self._info_sonda(),
            # La puerta del navegador: qué falta para desbloquear los seis
            # proveedores que exigen sesión, y si hay permiso vigente.
            "sesion_web": self._info_sesion_web(),
        }

    def _info_sesion_web(self) -> dict:
        """
        Estado de la puerta de sesión web.

        Se enseña SIEMPRE, también —y sobre todo— cuando no está disponible: la
        pregunta que el usuario tiene delante es «¿por qué Claude sale sin
        verificar?», y la respuesta es esta. Un panel que solo muestra lo que
        funciona deja las carencias sin explicar.
        """
        try:
            from dataclasses import asdict

            from magi.core import no_browser, sesion_web
            d = asdict(sesion_web.estado())
            d["necesitan_sesion"] = dict(sesion_web.PROVEEDORES_QUE_LA_NECESITAN)
            # Aperturas AUTORIZADAS, separadas de las violaciones: una es algo
            # que tú pediste y la otra algo que el sistema intentó a tus
            # espaldas. Mezclarlas dejaría el registro sin distinguirlas.
            d["aperturas_autorizadas"] = no_browser.autorizadas()[:5]
            return d
        except Exception as e:                            # pragma: no cover
            return {"error": str(e)}

    def _info_sonda(self) -> dict:
        """
        Lo que la sonda ha medido, listo para el panel.

        Si falla, se devuelve el motivo en vez de una tabla vacía: una tabla
        vacía se lee como «no hay proveedores», que es una afirmación distinta
        de «no he podido leerlo».
        """
        try:
            from magi.core.providers import sonda
            return sonda.resumen_para_panel(self.swarm.store)
        except Exception as e:                            # pragma: no cover
            return {"error": str(e), "familias": []}

    def _info_telemetria(self) -> dict:
        try:
            from magi.core.store import telemetria as tl
            store = self.swarm.store
            return {**tl.resumen(store),
                    "herramientas_que_fallan": tl.herramientas_que_fallan(store),
                    "hedging": tl.sirve_el_hedging(store),
                    # Dónde se va el tiempo ordenado por p95, no por media: una
                    # media no distingue «siempre tarda 4 s» de «suele tardar 1
                    # y a veces 30», y son problemas distintos. Datos que ya se
                    # guardaban desde que existe la telemetría y que nadie leía.
                    "cuellos": tl.cuellos_de_botella(store),
                    # Y lo que se ha salido HOY de su propio comportamiento.
                    "avisos_lentitud": tl.herramientas_fuera_de_su_p95(store)}
        except Exception as e:                            # pragma: no cover
            return {"error": str(e)}

    def _info_auditoria(self) -> dict:
        try:
            from magi.core.auditoria import auditoria
            a = auditoria()
            return {"verificacion": a.verificar(),
                    "ultimas": [
                        {"iso": e.get("iso"), "actor": e.get("actor"),
                         "accion": e.get("accion"),
                         "detalle": str(e.get("detalle", ""))[:160]}
                        for e in a.entradas(10)],
                    "diario": str(a.diario)}
        except Exception as e:                            # pragma: no cover
            return {"error": str(e)}

    def _info_catalogo(self) -> dict:
        try:
            from magi.core.providers.backends.g4f_backend import informe_catalogo
            return informe_catalogo()
        except Exception as e:                            # pragma: no cover
            return {"error": str(e)}

    def _info_esquema(self) -> dict:
        try:
            return self.swarm.store.estado_migraciones()
        except Exception as e:                            # pragma: no cover
            return {"error": str(e)}

    def _info_admision(self) -> dict:
        try:
            adm = self.swarm.admision
            perdidas = adm.perdidas()
            cola = sum(len(adm.en_cola(t)) for t in self.swarm.active_tasks)
            return {"en_cola": cola,
                    "perdidas": len(perdidas),
                    "ultimas": [e.resumen() for e in adm.recientes(8)]}
        except Exception as e:                            # pragma: no cover
            return {"error": str(e)}

    def _info_diagnostico(self) -> dict:
        try:
            from magi.modules.infrastructure import diagnostico as dg
            return {"version": dg.VERSION, "casos": len(dg.CATALOGO),
                    "texto": dg.catalogo_legible()}
        except Exception as e:                            # pragma: no cover
            return {"error": str(e)}

    async def _handle_artifacts_list(self, payload, websocket):
        """Ficheros que MAGI ha generado en el workspace, para la vista previa."""
        from magi.modules.studio.preview import listar_artefactos
        return listar_artefactos(limite=int((payload or {}).get("limite", 200)))

    async def _handle_artifacts_read(self, payload, websocket):
        """Contenido de un artefacto, listo para pintarlo en la vista previa."""
        from magi.modules.studio.preview import leer_artefacto
        return leer_artefacto((payload or {}).get("path", ""))

    async def _handle_self_improve(self, payload, websocket):
        """
        Auto-mejora medible (§3.5), a petición.

        No se dispara sola: cambiar el sistema tiene coste de cuota y el usuario
        debe poder elegir cuándo. Lo que sí es automático es la MEDICIÓN — el
        cambio solo se conserva si el banco mejora sin regresiones.
        """
        hypothesis = (payload or {}).get("hypothesis", "").strip()
        if not hypothesis:
            return {"status": "error",
                    "message": "indica qué cambio quieres probar"}

        async def noop():
            return None

        verdict = await self.naoko.run_self_improvement(hypothesis, noop, noop)
        return {"status": "ok", "verdict": verdict}

    async def _handle_eval_run(self, payload, websocket):
        """Ejecuta el banco de evaluación y devuelve la puntuación."""
        from magi.core.eval import default_bench
        from magi.core.providers.cloud import FreeCloudLLM

        llm = FreeCloudLLM()

        async def runner(prompt: str) -> str:
            content, _ = await llm.generate("Responde de forma directa.", prompt)
            return content

        result = await default_bench().run(runner, label="manual")
        await self.bus.publish(BusEvent(topic="eval.result",
                                        payload=result.to_dict()))
        return result.to_dict()

    async def _handle_naoko_chat(self, payload, websocket):
        msg = payload.get("message", "") if isinstance(payload, dict) else str(payload)
        image = payload.get("image", None) if isinstance(payload, dict) else None
        await self.bus.publish(BusEvent(topic="naoko.user_message", payload={"message": msg, "image": image}))
        return {"status": "ok"}

    async def _handle_hello(self, payload, websocket):
        return {"status": "MAGI Kernel Online", "version": "1.0"}

    async def _handle_connect(self, payload, websocket):
        return {"result": "CONNECTED", "version": "1.0.0"}

    async def _handle_estop(self, payload, websocket):
        """
        Parada de emergencia REAL (§7.3).

        Este handler entero era:

            logger.critical("E-STOP INVOCADO DESDE LA GUI")
            return "EMERGENCY_STOP_TRIGGERED"

        Una línea de log y una cadena. No cancelaba ningún bucle ni mataba
        ningún proceso: el botón de parada de la interfaz no paraba nada, y
        devolvía una respuesta con aspecto de éxito.

        Es el control que más caro sale que mienta. Todo el acceso sin
        restricciones a la máquina se sostiene sobre dos salidas: deshacer lo
        hecho (§4.2, el journal) y PARAR lo que se está haciendo. La segunda
        no existía.
        """
        from magi.core.cancel import supervisor
        logger.critical("E-STOP INVOCADO DESDE LA GUI")
        informe = await supervisor().cancel_all()
        await self.bus.publish(BusEvent(
            topic="task.cancelled", payload=informe.to_payload()))
        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT", payload={"content": informe.render()}))
        return informe.to_payload()

    async def _handle_cancel_task(self, payload, websocket):
        """
        Cancela UNA tarea (§7.3: "poder parar un turno a mitad sin matar la
        app").

        Antes la única opción era la parada de emergencia, que además de no
        funcionar habría sido un mazazo: si tienes tres conversaciones y una
        se está yendo por las ramas, no quieres tirar las otras dos.
        """
        from magi.core.cancel import supervisor
        task_id = (payload or {}).get("task_id", "").strip()
        if not task_id:
            return {"status": "error", "message": "indica qué tarea parar"}

        sup = supervisor()
        if not sup.is_running(task_id):
            return {"status": "ok", "stopped_anything": False,
                    "detail": f"{task_id} no está en marcha; nada que parar. "
                              f"En curso: {', '.join(sup.running_tasks()) or 'ninguna'}"}

        informe = await sup.cancel(task_id)
        estado = self.swarm.active_tasks.get(task_id)
        if estado is not None:
            estado["status"] = "cancelled"
            self.swarm._persist(task_id)
        await self.bus.publish(BusEvent(
            topic="task.cancelled", payload=informe.to_payload()))
        await self.bus.publish(BusEvent(
            topic="TERMINAL_OUT", payload={"content": informe.render()}))
        return informe.to_payload()

    async def _handle_running_tasks(self, payload, websocket):
        """Qué hay en marcha ahora mismo, para poder ofrecer pararlo."""
        from magi.core.cancel import supervisor
        return {"running": supervisor().running_tasks()}

    async def _handle_list_tasks(self, payload, websocket):
        """
        Tareas para la columna izquierda de la GUI.

        Devuelve las visibles (no archivadas ni borradas) con su título: así la
        columna muestra «Juego Tetris portable» en vez del task_id crudo. La
        GUI lo llama al conectar para repoblar la lista tras un reinicio.
        """
        try:
            store = self.swarm.store
            tareas = store.visibles(limit=100)
            return {"tasks": [
                {"task_id": t.task_id, "titulo": t.nombre,
                 "status": t.status, "round": t.round}
                for t in tareas]}
        except Exception as e:
            logger.warning("[kernel] no se pudo listar tareas: %s", e)
            return {"tasks": []}

    async def _handle_archive_task(self, payload, websocket):
        """Archiva una conversación: sale de la vista sin borrarse."""
        task_id = (payload or {}).get("task_id", "").strip()
        if not task_id:
            return {"status": "error", "message": "indica qué tarea archivar"}
        try:
            self.swarm.store.archivar(task_id, motivo="archivada por el usuario")
            await self.bus.publish(BusEvent(
                topic="task.archived",
                payload={"task_id": task_id}))
            return {"status": "ok", "task_id": task_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _handle_delete_task(self, payload, websocket):
        """
        Borra una conversación de la vista.

        Marca `borrada=True` (no destruye datos: reversible a nivel de store).
        Si la columna de la migración 0003 no existe, cae a `delete` real.
        """
        task_id = (payload or {}).get("task_id", "").strip()
        if not task_id:
            return {"status": "error", "message": "indica qué tarea borrar"}
        try:
            with self.swarm.store._conn() as c:
                k = c.execute("PRAGMA table_info(task_state)").fetchall()
                if any(r[1] == "borrada" for r in k):
                    c.execute(
                        "UPDATE task_state SET borrada=1, updated_at=? "
                        "WHERE task_id=?",
                        (__import__("time").time(), task_id))
                else:
                    c.execute("DELETE FROM task_state WHERE task_id=?", (task_id,))
            await self.bus.publish(BusEvent(
                topic="task.deleted",
                payload={"task_id": task_id}))
            return {"status": "ok", "task_id": task_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ---------------------------------------------------- ciclo de mejora

    async def _handle_improve_propose(self, payload, websocket):
        """
        Abre una mejora. `origin="usuario"` cuando la propones tú.

        Tu propuesta recorre exactamente el mismo circuito que una idea de
        Naoko: se pidió así. Que la idea sea tuya no la exime de la crítica
        del enjambre; si acaso al revés.
        """
        p = payload or {}
        titulo = (p.get("title") or "").strip()
        if not titulo:
            return {"status": "error", "message": "indica qué quieres mejorar"}
        m = await self.naoko.propose_improvement(
            titulo, (p.get("rationale") or "").strip(),
            origin=p.get("origin") or "usuario")
        return m.to_dict()

    async def _handle_improve_decide(self, payload, websocket):
        """
        Tu decisión en una compuerta. Es lo único que hace avanzar el ciclo.

        Un "no" descarta y no es un error: tratar el rechazo como fallo
        empujaría a insistir, y una propuesta que insiste deja de serlo.
        """
        from magi.modules.infrastructure.improvement import Stage, user_decides

        p = payload or {}
        log = self.naoko._improvements()
        m = log.get((p.get("improvement_id") or "").strip())
        if m is None:
            return {"status": "error", "message": "no existe esa mejora"}
        if not m.awaiting_user:
            return {"status": "error",
                    "message": f"{m.improvement_id} está en {m.stage.value} y "
                               f"no espera decisión tuya"}

        anterior = m.stage
        aprueba = bool(p.get("approve"))
        try:
            user_decides(m, aprueba)
        except Exception as e:
            return {"status": "error", "message": str(e)}
        log.save(m)
        await self.naoko._narrate(
            m, f"decidiste {'SÍ' if aprueba else 'NO'} en {anterior.value}")

        if not aprueba:
            return m.to_dict()

        # Cada compuerta abre una fase distinta. Van en segundo plano y bajo el
        # supervisor: son largas y el usuario debe poder pararlas (§7.3).
        from magi.core.cancel import supervisor

        async def _seguir():
            try:
                if anterior is Stage.FALLIDA:
                    pass          # reintentar solo devuelve a la compuerta
                elif anterior is Stage.IDEA:
                    await self.naoko.draft_plan(m)
                elif anterior is Stage.PLAN_BORRADOR:
                    await self.naoko.run_circuit(m)
                elif anterior is Stage.PLAN_FINAL:
                    await self.naoko.execute_improvement(m)
                elif anterior is Stage.ESPERANDO_PUBLICACION:
                    await self.naoko.publish_improvement(m)
            except asyncio.CancelledError:
                # `CancelledError` NO hereda de `Exception` desde 3.8, así que
                # el `except Exception` de abajo no la veía. Y la tarea está
                # inscrita en el supervisor, o sea que EL BOTÓN DE PARADA era
                # el disparador: pulsarlo durante una ronda dejaba la mejora en
                # `ronda`/`ejecutando`/`publicando` —estados de trabajo, no
                # compuertas—, `user_decides` la rechazaba en ambos sentidos y
                # `active()` la devolvía para siempre. Justo el atasco que el
                # bloque de abajo dice haber eliminado, entrando por la puerta
                # de al lado.
                logger.warning("[mejora] %s cancelada por el usuario",
                               m.improvement_id)
                from magi.modules.infrastructure.improvement import TRABAJO, fail
                if m.stage in TRABAJO:
                    fail(m, "cancelada desde la parada de emergencia")
                raise
            except Exception as e:
                # Sin esto la mejora se quedaba en `ronda` o `ejecutando`, que
                # NO son compuertas: `user_decides` los rechazaba, no había
                # forma de reanudar y `active()` los devolvía para siempre. La
                # única salida era editar SQLite a mano.
                logger.exception("[mejora] %s falló", m.improvement_id)
                from magi.modules.infrastructure.improvement import TRABAJO, fail
                if m.stage in TRABAJO:
                    fail(m, str(e))
                await self.bus.publish(BusEvent(
                    topic="TERMINAL_OUT",
                    payload={"content": f"[NAOKO] la fase falló: {e}. "
                                        f"Puedes reintentarla o descartarla."}))
            finally:
                self.naoko._improvements().save(m)
                await self.bus.publish(BusEvent(
                    topic="naoko.improvement", payload=m.to_dict()))

        supervisor().register_loop(
            f"mejora-{m.improvement_id}", asyncio.create_task(_seguir()))
        return m.to_dict()

    async def _handle_improve_list(self, payload, websocket):
        """Qué hay abierto y qué espera respuesta tuya."""
        log = self.naoko._improvements()
        return {"all": [m.to_dict() for m in log.all()[:20]],
                "pending": [m.to_dict() for m in log.pending_user()]}

    async def _handle_policy_check(self, payload, websocket):
        cap = Capability(name=payload.get("name"), resource=payload.get("resource"))
        res = self.policy.request_capability("rpc_client", cap)
        return {"granted": res.granted, "reason": res.reason}

    async def _handle_git_clone(self, payload, websocket):
        import asyncio

        repo_url = payload.get("url")
        if not repo_url:
            return {"status": "error", "message": "URL requerida"}

        scratch_dir = workspace_dir()
        scratch_dir.mkdir(parents=True, exist_ok=True)

        # Publicar inicio en terminal
        await self.bus.publish(BusEvent(topic="SYS_EXEC", payload={"task_id": "sys_git", "command": f"git clone {repo_url}"}))
        await self.bus.publish(BusEvent(topic="sys.terminal.out", payload=f"\\n> Clonando {repo_url} en {scratch_dir}...\\n"))

        process = await asyncio.create_subprocess_shell(
            f"git clone {repo_url}",
            cwd=str(scratch_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()
        out_msg = (stdout.decode() + "\\n" + stderr.decode()).strip()

        await self.bus.publish(BusEvent(topic="sys.terminal.out", payload=f"{out_msg}\\n[Git Clone completado con código {process.returncode}]"))

        return {"status": "ok", "message": "Clonado completado en scratch/"}

    async def _handle_state_sync(self, payload, websocket):
        """Devuelve el estado real del sistema para poblar la GUI sin simulación."""

        # Escanear proyectos reales en el workspace del usuario
        base_dir = workspace_dir()
        real_projects = []
        if base_dir.exists():
            for child in base_dir.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    real_projects.append({
                        "name": child.name,
                        "desc": "local · git detectado" if (child / ".git").exists() else "local · sin remoto"
                    })

        return {
            "projects": real_projects,
            "metrics": {
                "prov_a": "31/50",
                "prov_b": "agotado",
                "prov_c": "ok",
                "status": "online"
            }
        }

    async def _handle_sys_exec(self, payload, websocket):
        import asyncio
        import uuid

        command = payload.get("command", "") if isinstance(payload, dict) else payload
        raw_id = payload.get("id", "task_0") if isinstance(payload, dict) else "task_0"

        # B1 (v11): una orden escrita se EJECUTA, nunca se debate.
        from magi.core.comandos import clasificar  # noqa: E501
        orden, objetivo = clasificar(command)
        if orden == "cancel":
            return await self._handle_cancel_task(
                {"id": objetivo or raw_id}, websocket)
        if orden == "estop":
            return await self._handle_estop({}, websocket)

        if isinstance(command, str) and command.startswith("GIT_PUSH_TO_GITHUB"):
            repo_url = command.split(" ", 1)[1] if " " in command else ""
            if not repo_url:
                await self.bus.publish(BusEvent(topic="TERMINAL_OUT", payload="URL de GitHub requerida para push."))
                return

            scratch_dir = workspace_dir()
            await self.bus.publish(BusEvent(topic="TERMINAL_OUT", payload=f"Iniciando subida a GitHub: {repo_url}"))
            script = f"""
            git init
            git add .
            git commit -m "Auto-commit by MAGI"
            git branch -M main
            git remote add origin {repo_url}
            git push -u origin main -f
            """

            process = await asyncio.create_subprocess_shell(
                script,
                cwd=str(scratch_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            out_msg = (stdout.decode() + "\n" + stderr.decode()).strip()
            await self.bus.publish(BusEvent(topic="TERMINAL_OUT", payload=f"{out_msg}\n[Subida completada con código {process.returncode}]"))
            return

        if isinstance(command, str) and command.startswith("SYS_EXEC_HOST"):
            script = command.replace("SYS_EXEC_HOST", "", 1).strip()
            scratch_dir = workspace_dir()

            await self.bus.publish(BusEvent(topic="TERMINAL_OUT", payload="Ejecutando script local en host (ZCode Mode)..."))

            process = await asyncio.create_subprocess_shell(
                script,
                cwd=str(scratch_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            out_msg = (stdout.decode() + "\n" + stderr.decode()).strip()
            await self.bus.publish(BusEvent(topic="TERMINAL_OUT", payload=f"{out_msg}\n[Ejecución completada con código {process.returncode}]"))
            return

        # Siempre generar un id único si es task_0 o vacío
        if not raw_id or raw_id == "task_0":
            task_id = f"task_{uuid.uuid4().hex[:8]}"
        else:
            task_id = raw_id

        pld = payload if isinstance(payload, dict) else {}
        engine, gui_style = pld.get("engine", "fast"), pld.get("narrative_style", "tecnico")
        # `motor.estilo_y_motor`: triviales sin red y D2 degradación por salud.
        from magi.core.providers.cloud import FreeCloudLLM
        from magi.modules.infrastructure.motor import estilo_y_motor
        narrative_style, engine, origen = await estilo_y_motor(
            command, engine, gui_style, FreeCloudLLM(),
            self.swarm.store, getattr(self, "metrics", None))
        logger.info("[kernel] estilo=%s motor=%s decidido por %s",
                    narrative_style, engine, origen)
        if "salud-degradada" in origen:
            await self.bus.publish(BusEvent(
                topic="TERMINAL_OUT",
                payload={"content": f"[D2] {origen}: deep degradado a fast."}))
        await self.bus.publish(BusEvent(
            topic="swarm.style",
            payload={"task_id": task_id, "style": narrative_style,
                     "decidido_por": origen, "engine": engine}))

        # Carpeta de proyecto automática por conversación.
        new_proj_dir = workspace_dir() / f"project_{task_id}"
        new_proj_dir.mkdir(parents=True, exist_ok=True)

        await self.bus.publish(BusEvent(
            topic="system.project_created",
            payload={"name": f"project_{task_id}"}
        ))

        # Publicar en el bus para que el Logger lo intercepte
        await self.bus.publish(BusEvent(
            topic="SYS_EXEC",
            payload={"task_id": task_id, "command": command, "engine": engine,
                     "narrative_style": narrative_style}
        ))

        # Delegamos el control al Orquestador del Enjambre (Área 16)
        # El orquestador publicará los avances en el MagiBus que la GUI consumirá
        # MAGI 9.0 §2.3 — enrutamiento adaptativo.
        #
        # Estaba escrito y con tests, y NO se llamaba desde ningún sitio: toda
        # petición seguía pasando por el debate popperiano completo. Preguntar
        # "¿qué hora es?" costaba 9 llamadas a la nube y 60-90 s.
        from magi.core.providers.cloud import get_registry
        from magi.core.router import classify

        try:
            decision = await classify(command, await get_registry())
        except Exception as e:
            logger.warning("[kernel] clasificador falló (%s); ruta task", e)
            from magi.core.router import Route, RoutingDecision
            decision = RoutingDecision(Route.TASK, 0.5, "fallo del clasificador",
                                       2, True)

        logger.info("[kernel] ruta=%s (%s, confianza %.2f)",
                    decision.route.value, decision.reason, decision.confidence)
        await self.bus.publish(BusEvent(
            topic="swarm.routed",
            payload={"task_id": task_id, **decision.to_dict()}))

        # v5.0.28 llamaba a submit_task(task_id, command) sin pasar engine:
        # el selector de motor de la GUI tampoco tenía efecto.
        await self.swarm.submit_task(task_id, command, engine=engine,
                                     narrative_style=narrative_style,
                                     route=decision.route.value,
                                     max_rounds=decision.max_rounds,
                                     use_tools=decision.use_tools)

        # Título automático de la conversación. La columna izquierda mostraba
        # el task_id crudo («task_a3f9c2b1»); ahora una IA lo resume a 5-8
        # palabras a partir del comando, para que la lista diga «Juego Tetris
        # portable». Va en background: no puede bloquear el arranque del
        # enjambre. Si la red falla, cae a un recorte local del comando.
        asyncio.create_task(self._titular_tarea(task_id, command))

    async def _titular_tarea(self, task_id: str, command: str) -> None:
        """Genera un título corto para la tarea y avisa a la GUI."""
        titulo = command.strip().splitlines()[0][:60] if command else task_id
        try:
            from magi.core.providers.base import CompletionRequest, Message
            from magi.core.providers.cloud import get_registry
            reg = await get_registry()
            resp = await reg.complete(CompletionRequest(
                messages=[
                    Message("system",
                            "Resume la petición del usuario en un título de "
                            "máximo 8 palabras, en español, sin puntuación "
                            "final, sin comillas. Solo el título."),
                    Message("user", command[:500])],
                temperature=0.0, max_tokens=20, timeout_s=15.0), use_cache=True)
            cand = (resp.content or "").strip().splitlines()[0].strip()
            cand = cand.strip('"«»“”').strip()
            if cand and len(cand) <= 100:
                titulo = cand
        except Exception as e:
            logger.debug("[kernel] título IA falló (%s); uso recorte local", e)
        try:
            self.swarm.store.renombrar(task_id, titulo)
        except Exception:
            pass
        await self.bus.publish(BusEvent(
            topic="task.titled",
            payload={"task_id": task_id, "titulo": titulo}))

    async def start(self):
        logger.info("Iniciando MAGI Kernel...")

        # Persistencia de eventos críticos (system.started, error.critical,
        # obs.alert...). Antes había un TODO en bus.publish: el evento se
        # entregaba a los suscriptores pero se perdía si el proceso caía.
        # El sink es no bloqueante: el bus nunca lo espera.
        self.bus.attach_critical_sink(self._persist_critical_event)

        # Inicializamos el Logger ahora que el event_loop existe
        self.bus_logger = BusLogger(self.bus, self.db)

        # Conectar el root logger al bus para enviar logs a la UI
        bus_handler = BusLogHandler(self.bus)
        logging.getLogger().addHandler(bus_handler)

        await self.memgraph.start()
        await self.naoko.start()
        # Ritsuko DESPUÉS de Naoko: se suscribe a lo que Naoko emite, y un
        # auditor que arranca antes que el auditado se pierde justo el
        # arranque, que es donde más cosas se rompen.
        await self.ritsuko.start()
        await self.rpc.start()

        await self.bus.publish(BusEvent(
            topic="system.started",
            payload={"status": "online"},
            critical=True
        ))

        # La sonda, en segundo plano y con freno propio. Ver `_refrescar_sonda`.
        self._tarea_sonda = asyncio.create_task(self._refrescar_sonda())

        # SEGADORA DE ZOMBIS (v5.29 Fase 3): cada 5 min escanea rondas
        # esperando aprobación; las de >30 min se anuncian al bus una vez.
        async def _segar_periodico():
            while True:
                await asyncio.sleep(300)
                try:
                    await self.swarm.segar_zombis()
                except Exception as e:  # noqa: BLE001 — el reaper no tumba nada
                    logger.debug("[kernel] segadora de zombis falló: %s", e)
        self._tarea_segar = asyncio.create_task(_segar_periodico())

        logger.info("Kernel listo.")

    #: Cuánto espera la sonda desde el arranque antes de gastar un solo
    #: token. Ver el comentario largo de §G4 en `_refrescar_sonda`: el
    #: primer minuto de una sesión es cuando la persona escribe su primera
    #: petición, y es exactamente cuando el freno de 24 h dispara el sondeo.
    _TREGUA_DE_ARRANQUE_S = 120.0

    def _enjambre_ocupado(self) -> bool:
        """
        ¿Hay alguien esperando una respuesta ahora mismo?

        Cuenta las tareas en curso Y las que están a punto de arrancar. Mirar
        solo `in_progress` dejaba fuera el instante que más importa: el que va
        entre «el usuario pulsa Ejecutar» y «el orquestador marca la tarea en
        curso». Justo ahí caía el sondeo.
        """
        vivas = getattr(self.swarm, "active_tasks", {}) or {}
        if any((st or {}).get("status") in ("in_progress", "running", "queued")
               for st in vivas.values()):
            return True
        # La cola de admisión: lo que el usuario ya ha escrito y todavía no ha
        # llegado al orquestador. Si hay algo ahí, hay alguien esperando.
        try:
            admision = getattr(self.swarm, "admision", None)
            if admision is not None and getattr(admision, "pendientes", None):
                return bool(admision.pendientes())
        except Exception:                                   # pragma: no cover
            pass
        return False

    async def _refrescar_sonda(self) -> None:
        """
        Mide los proveedores si toca, y hace que el reparto obedezca al dato.

        POR QUÉ EN SEGUNDO PLANO
        ========================
        Sondear una docena de candidatos tarda entre veinte segundos y un
        minuto. Hacerlo en el arranque dejaría la interfaz esperando a algo que
        no necesita para funcionar: MAGI arranca con la última medida conocida
        y se corrige sola cuando la nueva llega.

        POR QUÉ NO LLEVA SU PROPIO «CADA CUÁNTO»
        =======================================
        El freno vive en `sonda.refrescar_si_toca`, no aquí. Si estuviera aquí,
        abrir y cerrar MAGI cinco veces dispararía cinco sondeos completos
        contra proveedores gratuitos, con la cuota del usuario. Una sonda que
        gasta tu cuota ha empeorado el sistema por muy buenos que sean sus
        datos.

        Y NO PUEDE TUMBAR EL ARRANQUE
        =============================
        Todo va dentro de un `try`. Si la sonda falla, MAGI sigue con el
        catálogo escrito a mano, que es exactamente lo que hacía antes de que
        la sonda existiera. Un sistema de observación que impide arrancar al
        sistema observado no es una mejora.
        """
        # Bucle periódico (Fase 1.2) - comprueba periódicamente sin bloquear,
        # pero el freno real está en `sonda.refrescar_si_toca`.
        # G4 — TREGUA DE ARRANQUE. Medido el 2026-08-23 en el registro del
        # usuario: abre MAGI, escribe «crea un juego de tetris en un unico
        # ejecutable exe portable», y lo siguiente que sale por el terminal es
        #
        #     [sonda] 32 candidatos medidos, 0 saltados por tope diario
        #     [sonda] 32 mediciones (la ultima medicion fue hace 84.1 h)
        #
        # seguido de los canarios de deriva con cobertura x3 en gpt, gemini y
        # command. Sesenta y pico llamadas HTTP a proveedores gratuitos
        # —limitados por cuota— ANTES de atender lo que la persona acababa de
        # pedir. La familia `razonamiento` acabó agotada devolviendo cuatro
        # caracteres («tud.») tres veces seguidas.
        #
        # B8 ya existía y no bastó, porque comprueba UNA vez y luego se va a
        # medir durante un minuto: es un comprobar-y-actuar con una ventana
        # enorme en medio. Y el momento en que se abre esa ventana es
        # exactamente el peor posible —el arranque—, porque el freno de 24 h
        # garantiza que la primera sesión del día dispare el sondeo justo
        # cuando la persona está escribiendo su primera petición.
        #
        # La tregua no cuesta nada: la sonda no es urgente. MAGI arranca con
        # la última medida conocida, que es lo que hace de todas formas.
        await asyncio.sleep(self._TREGUA_DE_ARRANQUE_S)

        while True:
            try:
                # B8 — la sonda espera a que el enjambre esté quieto.
                #
                # Sondear cuesta entre veinte segundos y un minuto de llamadas
                # a los mismos proveedores gratuitos que la tarea está usando.
                # Hacerlo a la vez no solo enlentece la tarea: contamina la
                # medición, porque lo que mide es la cuota que ella misma
                # acaba de gastar. En la auditoría del 20-ago se vio
                # `sonda.actualizada` a mitad de una tarea, y cuatro «derivas»
                # detectadas justo después.
                if self._enjambre_ocupado():
                    await asyncio.sleep(60)
                    continue

                from magi.core.providers import sonda
                from magi.core.providers.backends.g4f_backend import (
                    LlmDeSonda,
                    candidatos_para_sondear,
                )

                # OJO al módulo: get_registry vive en providers.cloud, no en
                # providers.registry. Importarlo del sitio equivocado lanzaba
                # ImportError, el except de abajo se lo tragaba y la sonda no
                # llegaba a ejecutarse NUNCA — con el catálogo escrito a mano
                # mandando para siempre. Lo encontró pyright (unknown import
                # symbol), no un test.
                from magi.core.providers.cloud import get_registry

                store = self.swarm.store
                # Segunda comprobación, pegada al gasto. Entre el `if` de
                # arriba y esta línea hay imports y una llamada a registro:
                # poco tiempo, pero suficiente para que entre una petición.
                # La comprobación barata se hace las veces que haga falta;
                # el sondeo, una sola vez y solo si sigue sin haber nadie.
                if self._enjambre_ocupado():
                    await asyncio.sleep(60)
                    continue

                hechas, motivo = await sonda.refrescar_si_toca(
                    LlmDeSonda(), candidatos_para_sondear(), store)
                logger.info("[sonda] %s", motivo)

                medias = sonda.medias_por_familia(store)
                if medias:
                    (await get_registry()).aplicar_medidas(medias)
                    logger.info("[sonda] el reparto ahora obedece a %d familias "
                                "medidas", len(medias))
                await self.bus.publish(BusEvent(
                    topic="sonda.actualizada",
                    payload={"mediciones": hechas, "motivo": motivo,
                             "familias_medidas": len(medias)}))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[sonda] no se pudo refrescar: %s", e)

            # Revisar cada hora (3600s). El verdadero freno (24h) lo pone refrescar_si_toca.
            await asyncio.sleep(3600)

    async def _persist_critical_event(self, event):
        """
        Sink del bus: vuelca eventos críticos a task_event.

        Es la persistencia que faltaba: un crash pierde los eventos en RAM, y
        justo los críticos (system.started, error.critical, obs.alert) son los
        que hacen falta para diagnosticar por qué se cayó. La tabla ya existía
        (migración 0001_base); nadie escribía en ella.

        Dos cuidados que no son opcionales, y que son exactamente los que
        convierten una buena idea en un problema si se pasan por alto:

        1. sqlite3 es SÍNCRONO. Llamarlo desde una corrutina para el bucle de
           eventos entero mientras dura la escritura. Con la interfaz colgada
           del bus, cada evento crítico se vería como un tirón en pantalla.
           Va en asyncio.to_thread, que es como escribe todo store/database.py
           (por eso la conexión se abre con check_same_thread=False).

        2. `with sqlite3.connect(...) as conn` hace commit al salir pero NO
           cierra la conexión — es un error de bulto muy común. Un descriptor
           de fichero abierto por evento crítico es una fuga lenta que acaba
           en «too many open files» tras horas de sesión. Cierre explícito en
           finally.

        Se invoca vía asyncio.create_task desde el bus: nunca bloquea el
        broadcast. Si la BD falla, se registra en debug y el sistema sigue.
        """
        import json as _json
        import time as _time
        try:
            payload = event.payload
            task_id = (payload.get("task_id") if isinstance(payload, dict)
                       else None) or "_system"
            fila = (task_id, event.topic,
                    _json.dumps(payload, default=str, ensure_ascii=False),
                    _time.time())

            def _escribir():
                conn = self.db._get_connection()
                try:
                    with conn:  # commit/rollback
                        conn.execute(
                            "INSERT INTO task_event "
                            "(task_id, topic, payload, ts) VALUES (?, ?, ?, ?)",
                            fila)
                finally:
                    conn.close()  # el `with` NO lo hace

            await asyncio.to_thread(_escribir)
        except Exception as e:
            logger.debug("[kernel] no se pudo persistir evento crítico %s: %s",
                         event.topic, e)

    async def shutdown(self):
        if getattr(self, "naoko", None) is not None:
            await self.naoko.stop()
        logger.info("Apagando Kernel...")
        await self.rpc.close()

if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    async def main():
        kernel = Kernel()
        try:
            await kernel.start()
            await asyncio.Future()
        except KeyboardInterrupt:
            await kernel.shutdown()
    asyncio.run(main())

