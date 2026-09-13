import { useEffect, useMemo, useRef } from 'react';
import { useMagiStore } from './store';

export function useMagiSocket(port: number = 20128) {
  const ws = useRef<WebSocket | null>(null);
  // Selectores, no el store entero. `useMagiStore()` sin selector suscribe a
  // CUALQUIER cambio del store: cada línea de terminal que llega por el socket
  // volvía a renderizar al que usa este hook —App— aunque solo necesite tres
  // setters que nunca cambian. Con selector, estas tres referencias son
  // estables y no provocan renders por su cuenta.
  const setConnected = useMagiStore((s) => s.setConnected);
  const addMessage = useMagiStore((s) => s.addMessage);
  const appendTerminal = useMagiStore((s) => s.appendTerminal);

  useEffect(() => {
    const connect = () => {
      try {
        ws.current = new WebSocket(`ws://127.0.0.1:${port}`);

        ws.current.onopen = () => {
          setConnected(true);
          appendTerminal(`[NETWORK] Conexión WebSocket establecida en puerto ${port}`);
          // Solicitar estado real inicial
          ws.current?.send(JSON.stringify({ type: 'rpc.state.sync', id: 'sync_0' }));
          ws.current?.send(JSON.stringify({ type: 'GET_FILE_TREE', id: 'file_tree_0' }));
          // v5.3.0 — cargar la lista de tareas con sus títulos para repoblar
          // la columna izquierda tras un reinicio.
          fetchTaskList();
        };

        ws.current.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            
            if (data.type === 'event') {
              const topic = data.topic;
              const payload = data.payload;
              
              if (topic === 'agent.delta') {
                // MAGI 9.0 §1.2 — token a token: el primer token llega en ~2 s
                // en vez de esperar 30-90 s a la respuesta completa.
                useMagiStore.getState().appendDelta({
                  task_id: payload.task_id,
                  agent: payload.agent,
                  text: payload.text || '',
                  provider: payload.provider,
                  family: payload.family,
                });
              } else if (topic === 'agent.tool_use') {
                // §2.2 — ver "leyendo dynarec.cpp:412" convierte una caja negra
                // en un colaborador cuyo razonamiento se puede seguir.
                useMagiStore.getState().addToolUse(payload);
              } else if (topic === 'agent.tool_result') {
                useMagiStore.getState().addToolResult(payload);
              } else if (topic === 'obs.alert') {
                // §3.4 — degradación visible: un proveedor a 25 s o una
                // herramienta fallando el 40 % no lanzan excepción, pero
                // arruinan la experiencia igual.
                useMagiStore.getState().addAlert(payload);
              } else if (topic === 'provider.model_drift') {
                useMagiStore.getState().addAlert({
                  kind: 'drift', subject: payload.provider, severity: 'warning',
                  detail: `${payload.provider} cambió de comportamiento `
                    + `(${payload.matched}/${payload.total} sondas correctas). `
                    + `Las comparaciones con resultados anteriores dejan de ser válidas.`,
                });
              } else if (topic === 'swarm.verification_failed') {
                appendTerminal(
                  `[VERIFICACIÓN] Ronda ${payload.round}: el código propuesto `
                  + `no arranca. Devuelto al autor sin gastar ronda.\n`
                  + (payload.detail || ''));
              } else if (topic === 'eval.result') {
                appendTerminal(
                  `[BANCO] ${payload.passed}/${payload.total} `
                  + `(${Math.round((payload.score || 0) * 100)}%)`);
              } else if (topic === 'task.cancelled') {
                // El informe dice lo que se paró DE VERDAD, incluidos los
                // procesos que no murieron. Un botón de parada no puede
                // devolver algo con aspecto de éxito sin haber parado nada.
                useMagiStore.getState().setCancelReport(payload);
                appendTerminal(payload.detail || 'Cancelación completada');
              } else if (topic === 'task.plan') {
                useMagiStore.getState().setPlan(payload);
              } else if (topic === 'task.titled') {
                // v5.3.0 — Naoko tituló la conversación con un resumen IA.
                useMagiStore.getState().setTaskTitle(payload.task_id, payload.titulo);
              } else if (topic === 'task.archived' || topic === 'task.deleted') {
                // v5.3.0 — quita la conversación de la columna izquierda.
                useMagiStore.getState().removeConversation(payload.task_id);
              } else if (topic === 'swarm.style') {
                // Naoko decidió el estilo; informativo, se muestra en la ruta.
                appendTerminal(`[NAOKO] estilo: ${payload.style}`);
              } else if (topic === 'naoko.improvement') {
                // Naoko es EXPRESA: cada paso del ciclo llega aquí para verse.
                useMagiStore.getState().setImprovement(payload);
              } else if (topic === 'swarm.fases') {
                // Fase 7 - los tiempos por fase de la ronda.
                useMagiStore.getState().setFases(payload);
              } else if (topic === 'task.usage') {
                // §7.3 — tokens y tiempo por tarea y por agente.
                useMagiStore.getState().addUsage(payload);
              } else if (topic === 'swarm.approval_required') {
                // §7.4 — el contexto que hace posible decidir: qué ficheros,
                // qué había antes, y si los tests pasaron.
                useMagiStore.getState().setApproval(payload);
                // Y AVISAR de que le toca a él. Esta linea faltaba: nadie ponia
                // `awaitingApproval` a true en toda la interfaz —solo a false, al
                // decidir—, asi que el banner de aprobacion y la apertura
                // automatica del cajon dependian de un flag que nunca se
                // encendia. El enjambre se paraba a esperar y la interfaz no lo
                // decia de ninguna forma. La v5.21.1 puso el aviso; se quedo sin
                // conectar, igual que F2-F5 en el backend.
                useMagiStore.getState().setAwaitingApproval(true);
              } else if (topic === 'swarm.ronda') {
                // El orquestador lo publica «para informar al GUI del coste
                // acumulado» y nadie lo escuchaba: las llamadas gastadas y su
                // techo se quedaban en el bus. Es lo que decide si una tarea
                // llegará al final o se quedará sin presupuesto a mitad.
                useMagiStore.getState().setPresupuesto(payload);
              } else if (topic === 'swarm.routed') {
                useMagiStore.getState().setRoute(payload);
              } else if (topic === 'agent.delta_end') {
                useMagiStore.getState().endDelta({
                  task_id: payload.task_id,
                  agent: payload.agent,
                });
              } else if (topic === 'AGENT_POST') {
                addMessage({
                  id: Math.random().toString(36),
                  task_id: payload.task_id,
                  agent: payload.agent,
                  role: payload.role || 'propone',
                  provider: payload.provider || 'local',
                  content: payload.content,
                  changes: payload.changes || 0,
                  stats: payload.stats || '0 ms'
                });
              } else if (topic === 'TERMINAL_OUT') {
                appendTerminal(payload.content || payload.message || String(payload));
              } else if (topic === 'naoko.log') {
                useMagiStore.getState().addNaokoMessage({
                  id: Math.random().toString(36),
                  agent: payload.agent,
                  role: "DevOps",
                  provider: "G4F",
                  content: payload.content,
                  changes: 0,
                  stats: "0 ms"
                });
              } else if (topic === 'naoko.status') {
                useMagiStore.getState().setNaokoStatus(payload.status);
              } else if (topic === 'ritsuko.log') {
                useMagiStore.getState().addRitsukoMessage({
                  id: Math.random().toString(36),
                  agent: payload.agent,
                  role: "Auditoria",
                  provider: "G4F",
                  content: payload.content,
                  changes: 0,
                  stats: "0 ms"
                });
              } else if (topic === 'ritsuko.ronda_dormida') {
                // El guardián (3-sep-2026): tareas vivas + bus mudo = una
                // ronda colgada. Pinta como mensaje de auditoría — el usuario
                // no puede estar mirando logs para enterarse.
                useMagiStore.getState().addRitsukoMessage({
                  id: Math.random().toString(36),
                  agent: "RITSUKO",
                  role: "Guardián",
                  provider: "local",
                  content: `[GUARDIÁN] Tareas ${payload.vivas?.join(", ")} llevan `
                    + `${payload.silencio_s} s sin producir nada. Ronda dormida: `
                    + `considera reiniciarla.`,
                  changes: 0,
                  stats: `${payload.silencio_s} s`
                });
              } else if (topic === 'ritsuko.status') {
                useMagiStore.getState().setRitsukoStatus(payload.status);
              } else if (topic === 'ritsuko.informe') {
                // Cada informe nuevo refresca la lista: el usuario no tiene
                // que acordarse de pulsar "actualizar" para ver lo que acaba
                // de pedir.
                ws.current?.send(JSON.stringify({ type: 'ritsuko.informes', id: 'req_ritsuko_informes' }));
              } else if (topic === 'system.project_created') {
                ws.current?.send(JSON.stringify({ type: 'rpc.state.sync', id: 'sync_0' }));
              }
            } else if (data.ok !== undefined) {
               // Es una respuesta directa RPC
               if (data.id === 'sync_0' && data.result) {
                 useMagiStore.getState().setProjects(data.result.projects || []);
                 if (data.result.metrics) {
                   useMagiStore.getState().setMetrics(data.result.metrics);
                 }
               } else if (data.id === 'req_ritsuko_informes' && data.result) {
                 useMagiStore.getState().setRitsukoInformes(data.result.informes || []);
               } else if (data.id === 'req_telemetry' && data.result) {
                 useMagiStore.getState().setTelemetry(data.result);
               } else if (data.id === 'file_tree_0' && data.result) {
                 useMagiStore.getState().setFileTree(data.result);
               } else if (data.id === 'req_file_content' && data.result) {
                 if (data.result.content !== undefined) {
                   useMagiStore.getState().setActiveFile(data.result.path, data.result.content);
                 }
               }
            }
          } catch (e) {
            appendTerminal(`[NETWORK] Mensaje RAW: ${event.data}`);
          }
        };

        ws.current.onclose = () => {
          setConnected(false);
          // appendTerminal(`[NETWORK] Conexión perdida. Reconectando en 3s...`);
          setTimeout(connect, 3000);
        };
      } catch (err) {
         console.error("Socket error", err);
      }
    };

    connect();

    return () => {
      if (ws.current) ws.current.close();
    };
  }, [port]);

  // MAGI 9.0 §2.7 — narrativeStyle SÍ viaja al backend.
  // En v5.0.28 el <select> de estilo narrativo existía en App.tsx:307 pero su
  // valor no se enviaba nunca: esta firma no lo aceptaba. Era decorativo.
  const sendCommand = (
    cmd: string,
    taskId?: string,
    engine?: string,
    narrativeStyle?: string,
  ) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        type: "SYS_EXEC",
        payload: {
          command: cmd,
          id: taskId,
          engine: engine || "fast",
          narrative_style: narrativeStyle || "tecnico"
        }
      }));
    }
  };

  // §7.3 — parar UN turno a mitad sin matar la aplicación ni las demás
  // conversaciones. Antes la única opción era la parada de emergencia, que
  // además de ser un mazazo no paraba nada: el handler del kernel escribía
  // una línea de log y devolvía "EMERGENCY_STOP_TRIGGERED".
  // §7.3 — PARAR TODO. Iba por `sendCommand("KILL_ALL_PROCESSES")`, que
  // manda `type: "SYS_EXEC"` con el texto dentro del payload. El kernel
  // despacha por `type`, así que llegaba a `_handle_sys_exec` y la cadena
  // "KILL_ALL_PROCESSES" se trataba como una PETICIÓN DEL USUARIO: creaba un
  // proyecto, llamaba al clasificador y lanzaba un debate del enjambre sobre
  // ella. El botón de parada no solo no paraba: gastaba cuota y abría trabajo
  // nuevo. Hay que mandar el método como `type`.
  // §3.4 / §3.5 — tres capacidades del backend estaban COMPLETAS y no había
  // forma de invocarlas desde la interfaz: `obs.metrics` (panel de salud),
  // `naoko.self_improve` (auto-mejora medible) y `eval.run` (banco de
  // evaluación). Lo encontró una auditoría de qué handlers RPC tienen quien
  // los llame. Faltaba el botón, no el motor.
  const rpc = (metodo: string, payload: unknown = {},
               timeoutMs = 20_000): Promise<any> =>
    new Promise((resolve, reject) => {
      const socket = ws.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        reject(new Error("sin conexión con el kernel"));
        return;
      }
      const id = `${metodo}_${Date.now()}_${Math.random().toString(36).slice(2)}`;
      const alRecibir = (ev: MessageEvent) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.id !== id) return;
          socket.removeEventListener("message", alRecibir);
          clearTimeout(temporizador);
          data.ok === false ? reject(new Error(data.error || "falló"))
                            : resolve(data.result);
        } catch { /* otro mensaje cualquiera */ }
      };
      // Sin timeout, un handler que no responde deja la promesa colgada para
      // siempre y el panel girando: el usuario no distingue "tarda" de "no va".
      const temporizador = setTimeout(() => {
        socket.removeEventListener("message", alRecibir);
        reject(new Error("el kernel no respondió a tiempo"));
      }, timeoutMs);
      socket.addEventListener("message", alRecibir);
      socket.send(JSON.stringify({ type: metodo, id, payload }));
    });

  // El tiempo límite va por llamada: pedir métricas y esperar tres minutos
  // son cosas distintas. Un tope único obliga a elegir entre dejar colgado un
  // panel de lectura o cortar una auto-mejora legítima a mitad.
  const fetchHealth = () => rpc("obs.metrics", {}, 15_000);
  const fetchRunningTasks = () => rpc("task.running", {}, 10_000);

  // Configuración y vista previa. Las dos pestañas existían en la barra y no
  // tenían nada detrás: Configuración no pintaba nada, y Vista previa cargaba
  // un http://localhost:3000 que nadie levanta, así que enseñaba la página de
  // error del navegador.
  const fetchConfig = () => rpc("sys.config", {}, 20_000);
  const listArtifacts = (limite = 200) => rpc("artifacts.list", { limite }, 15_000);
  const readArtifact = (path: string) => rpc("artifacts.read", { path }, 20_000);
  // L5 — la capa local responde en milisegundos: 10 s de tope es de sobra,
  // y si tarda más es que algo va mal, no que esté pensando.
  const askLilim = (pregunta: string) => rpc("lilim.pregunta", { pregunta }, 10_000);

  // v5.3.0 — gestión de la lista de conversaciones: títulos, archivar, borrar.
  const fetchTaskList = () => rpc("task.list", {}, 10_000).then((res) => {
    if (res?.tasks) {
      const store = useMagiStore.getState();
      const titulos: Record<string, string> = {};
      const convs: Record<string, any[]> = {};
      for (const t of res.tasks) {
        titulos[t.task_id] = t.titulo || t.task_id;
        // Solo registrar la conversación si ya existe en el store; si no, se
        // crea vacía para que aparezca en la columna y pueda abrirse.
        if (!store.conversations[t.task_id]) convs[t.task_id] = [];
      }
      if (Object.keys(convs).length) {
        useMagiStore.setState((s) => ({
          conversations: { ...s.conversations, ...convs },
        }));
      }
      // Volcar los títulos de golpe.
      for (const [tid, tit] of Object.entries(titulos)) store.setTaskTitle(tid, tit);
    }
    return res;
  }).catch(() => null);

  const archiveTask = (taskId: string) =>
    rpc("task.archive", { task_id: taskId }, 10_000).catch(() => null);
  const deleteTask = (taskId: string) =>
    rpc("task.delete", { task_id: taskId }, 10_000).catch(() => null);

  // Ciclo de mejora de Naoko. `decide` puede arrancar una fase larga (redactar
  // el plan, dos circuitos del enjambre, aplicar) pero devuelve en cuanto la
  // lanza: el progreso llega por el evento `naoko.improvement`.
  const listImprovements = () => rpc("naoko.improve.list", {}, 15_000);
  const proposeImprovement = (title: string, rationale: string) =>
    rpc("naoko.improve.propose", { title, rationale, origin: "usuario" }, 30_000);
  const decideImprovement = (improvement_id: string, approve: boolean) =>
    rpc("naoko.improve.decide", { improvement_id, approve }, 30_000);
  // El banco y la auto-mejora hacen inferencia real contra proveedores
  // gratuitos: minutos, no segundos.
  const runBenchmark = () => rpc("eval.run", {}, 10 * 60_000);
  const runSelfImprovement = (hypothesis: string) =>
    rpc("naoko.self_improve", { hypothesis }, 15 * 60_000);

  const stopEverything = () => {
    ws.current?.send(JSON.stringify({
      type: 'KILL_ALL_PROCESSES', id: `estop_${Date.now()}`, payload: {},
    }));
  };

  const cancelTask = (taskId: string) => {
    ws.current?.send(JSON.stringify({
      type: 'task.cancel', id: `cancel_${Date.now()}`,
      payload: { task_id: taskId },
    }));
  };

  const sendGitClone = (url: string) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        type: "git.clone",
        payload: { url }
      }));
    }
  };

  const requestFileContent = (path: string) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        type: "GET_FILE_CONTENT",
        id: "req_file_content",
        payload: { path }
      }));
    }
  };

  const fetchTelemetry = () => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        type: "GET_TELEMETRY",
        id: "req_telemetry"
      }));
    }
  };

  const sendNaokoChat = (message: string, image?: string | null) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        type: "naoko.chat",
        payload: { message, image: image || null }
      }));
    }
  };

  const sendRitsukoChat = (message: string) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        type: "ritsuko.chat",
        payload: { message }
      }));
    }
  };

  const fetchRitsukoInformes = () => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        type: "ritsuko.informes",
        id: "req_ritsuko_informes"
      }));
    }
  };

  // v5.8.0 — IDENTIDAD ESTABLE. Esto no es una micro-optimización: es la
  // causa raíz de que la aplicación quemara un núcleo entero estando parada.
  //
  // Hasta aquí, cada render creaba veintidós funciones nuevas y las devolvía
  // en un objeto nuevo. Cualquier `useEffect` que pusiera una de ellas en sus
  // dependencias —lo que el linter de React pide hacer— se volvía a lanzar en
  // CADA render. Y si ese efecto llamaba al kernel y guardaba el resultado con
  // un `setState`, el ciclo se cerraba solo:
  //
  //     efecto -> RPC -> respuesta -> setState -> render
  //            -> identidad nueva -> efecto -> ...
  //
  // Medido el 2026-08-20 sobre Magisys.exe en reposo, sin ninguna tarea
  // corriendo: 97 % de un núcleo. El mismo kernel arrancado solo, sin
  // interfaz y con las mismas 13 tareas rehidratadas: 0 %. El bucle estaba
  // aquí, no en el enjambre. Y el coste real lo pagaba el usuario: Naoko
  // respondía en 10,8 s medidos por sonda WebSocket directa, pero la interfaz
  // no llegaba a pintarlo porque el hilo de render nunca estaba libre. Se
  // percibía como "Naoko no responde".
  //
  // Se arreglaron los cuatro efectos culpables uno por uno, pero eso es jugar
  // al gato y al ratón: el siguiente componente que se escriba y haga lo que
  // el linter pide vuelve a abrir el agujero. Congelar el objeto lo cierra de
  // raíz — ninguna dependencia puede volver a cambiar sola.
  //
  // Es correcto congelar la PRIMERA versión: ninguna de estas funciones cierra
  // sobre estado que cambie. Todas leen `ws.current` (una ref, estable) en el
  // momento de llamarse, o `useMagiStore.getState()` (módulo). `port` ya está
  // fijado por el efecto de conexión, que sí lo lleva en sus dependencias.
  //
  // Guardado por `tests/test_gui_sin_bucles_de_render.py`.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => ({
    sendCommand, sendGitClone, cancelTask, stopEverything,
    fetchHealth, runBenchmark, runSelfImprovement, fetchRunningTasks,
    listImprovements, proposeImprovement, decideImprovement,
    fetchTelemetry, requestFileContent, sendNaokoChat,
    sendRitsukoChat, fetchRitsukoInformes,
    fetchConfig, listArtifacts, readArtifact, askLilim,
    fetchTaskList, archiveTask, deleteTask,
  }), []);
}
