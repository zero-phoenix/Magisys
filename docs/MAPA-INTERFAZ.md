# Mapa de la interfaz de MAGI

Generado por `magi.modules.gui.mapa`. No arranca la aplicación:
mapea el cableado por topics entre `magi-gui/src` y `magi/`.

| | |
|---|---:|
| Comandos conectados (UI → handler) | 20 |
| Eventos conectados (backend → UI) | 26 |
| Sin nadie al otro lado | 0 |
| Capacidades invisibles | 12 |
| Se ven por otro canal | 13 |

## Comandos conectados

_la UI los manda y hay `register_handler` que los atiende_

- `artifacts.list`
- `artifacts.read`
- `eval.run`
- `git.clone`
- `lilim.pregunta`
- `naoko.chat`
- `naoko.improve.decide`
- `naoko.improve.list`
- `naoko.improve.propose`
- `naoko.self_improve`
- `obs.metrics`
- `ritsuko.chat`
- `ritsuko.informes`
- `rpc.state.sync`
- `sys.config`
- `task.archive`
- `task.cancel`
- `task.delete`
- `task.list`
- `task.running`

## Eventos conectados

_el backend los emite y la UI los nombra_

- `agent.delta`
- `agent.delta_end`
- `agent.tool_result`
- `agent.tool_use`
- `eval.result`
- `naoko.improvement`
- `naoko.log`
- `naoko.status`
- `obs.alert`
- `provider.model_drift`
- `ritsuko.informe`
- `ritsuko.log`
- `ritsuko.ronda_dormida`
- `ritsuko.status`
- `swarm.approval_required`
- `swarm.fases`
- `swarm.routed`
- `swarm.style`
- `swarm.verification_failed`
- `system.project_created`
- `task.archived`
- `task.cancelled`
- `task.deleted`
- `task.plan`
- `task.titled`
- `task.usage`

## Sin nadie al otro lado

_la UI los nombra y el backend ni los emite ni los atiende_

- (ninguno)

## Capacidades invisibles

_trabajo que se hace y el usuario NO ve por ningún canal_

- `agent.done`
- `agent.thought`
- `agent.turn_done`
- `knowledge.recorded`
- `memgraph.status`
- `ritsuko.user_message`
- `rpc.hello`
- `rpc.policy.check`
- `sonda.actualizada`
- `swarm.ronda`
- `sys.terminal.out`
- `system.started`

## Se ven por otro canal

_la UI no los nombra, pero salen junto a un TERMINAL_OUT o a un log de panel: el usuario se entera igual_

- `agent.slow_iteration`
- `agent.timeout`
- `error.critical`
- `naoko.diagnostico`
- `naoko.trace`
- `naoko.user_message`
- `ritsuko.veto_de_deriva`
- `swarm.artefacto_listo`
- `swarm.budget_exhausted`
- `swarm.entrada_encolada`
- `swarm.entrega_incompleta`
- `swarm.task_completed`
- `swarm.verificacion_agotada`

