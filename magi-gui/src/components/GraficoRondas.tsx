/**
 * El debate del enjambre, dibujado por RONDAS.
 *
 * POR QUÉ VIVE AQUÍ
 * =================
 * Estaba dentro de App.tsx y al reescribirlo el fichero pasó de 900 líneas.
 * `test_app_tsx_no_vuelve_a_crecer_sin_limite` lo cazó con el mensaje que toca:
 * extrae un panel en lugar de subir el límite. Es la segunda vez que ese
 * guardián acierta.
 *
 * QUÉ CAMBIA RESPECTO AL ANTERIOR
 * ===============================
 * Antes: un nodo por mensaje, todos iguales, en una fila que crecía. Con eso
 * no se podía contestar a lo único que se le pregunta a un diagrama de un
 * debate — en qué ronda vamos, qué se decidió en cada una, y qué falta para
 * cerrar la actual.
 *
 * Ahora: una cabecera por ronda con su veredicto, y debajo los tres nodos con
 * su estado (hecho / en curso / pendiente). Lo pendiente se dibuja apagado:
 * una ronda a medias tiene que parecer a medias, no rota.
 *
 * La aritmética de agrupar vive en lib/rondas.ts con sus 17 tests. Aquí solo
 * se pinta.
 */
import { Background, Controls, Edge, Node, ReactFlow } from "@xyflow/react";
import { agruparEnRondas, tituloDelDebate } from "../lib/rondas";
import { useMagiStore } from "../store";
import { avisoDelDebate } from "../lib/calidadDebate";

export default function GraficoRondas({ messages }: { messages: any[] }) {
  // El presupuesto de llamadas de ESTA conversacion. El orquestador lo publica
  // en `swarm.ronda` «para informar al GUI del coste acumulado» y la interfaz
  // no escuchaba ese topic: las llamadas gastadas y su techo se quedaban en el
  // bus. Es el dato que dice si una tarea llegara al final o se quedara sin
  // presupuesto a mitad, y va donde se mira el avance del debate.
  const presupuesto = useMagiStore(
    (s) => s.presupuestos[s.activeConversationId] || null);


               // EL DEBATE POR RONDAS, no una cadena de mensajes.
               //
               // Antes se pintaba un nodo por mensaje, todos iguales y en fila.
               // Con eso no se podía contestar a lo único que se le pregunta a
               // un diagrama de un debate: en qué ronda vamos, qué se decidió
               // en cada una y qué falta para cerrar la actual. Y como cada
               // variante de Melchior publicaba lo suyo, una ronda salía con
               // cinco cajas de las que tres eran el mismo agente.
               //
               // La lógica vive en lib/rondas.ts, con sus tests. Aquí solo se
               // pinta.
               const rondas = agruparEnRondas(messages as any);
               const nodes: Node[] = [];
               const edges: Edge[] = [];

               nodes.push({
                 id: 'user',
                 position: { x: 300, y: 0 },
                 data: { label: presupuesto
                           ? `👤 TU PETICIÓN
ronda ${presupuesto.round} · ${presupuesto.calls_used}/${presupuesto.techo} llamadas`
                           : '👤 TU PETICIÓN' },
                 style: { background: '#2c3e50', color: 'white', border: '1px solid #34495e', borderRadius: '8px', width: 200, whiteSpace: 'pre-line' }
               });

               const COLOR: Record<string, string> = {
                 MELCHIOR: 'var(--var)', BALTHASAR: 'var(--acc)', CASPER: 'var(--fn)',
               };
               let prevId = 'user';
               let y = 90;

               rondas.forEach((r) => {
                 const cabecera = `ronda_${r.numero}`;
                 nodes.push({
                   id: cabecera,
                   position: { x: 300, y },
                   data: { label: `▸ RONDA ${r.numero}${r.cerrada ? ` · ${r.conclusion ?? 'cerrada'}` : ' · en curso'}` },
                   style: {
                     background: r.cerrada ? 'rgba(0,200,255,0.10)' : 'rgba(255,204,102,0.10)',
                     color: r.cerrada ? 'var(--acc)' : '#ffcc66',
                     border: `1px solid ${r.cerrada ? 'var(--acc)' : '#ffcc66'}`,
                     borderRadius: '8px', width: 260, fontWeight: 'bold',
                   },
                 });
                 edges.push({ id: `e_${prevId}_${cabecera}`, source: prevId, target: cabecera, animated: !r.cerrada, style: { stroke: 'var(--dim)' } });
                 prevId = cabecera;
                 y += 70;

                 r.nodos.forEach((n, i) => {
                   const id = `r${r.numero}_${n.agente}`;
                   const apagado = n.estado === 'pendiente';
                   nodes.push({
                     id,
                     position: { x: 120 + i * 200, y },
                     data: { label: `${n.agente}\n${n.papel}${n.familia ? ` · ${n.familia}` : ''}${n.resumen ? `\n${n.resumen}` : ''}` },
                     style: {
                       background: 'rgba(10,20,25,0.9)',
                       color: apagado ? 'var(--dim)' : (COLOR[n.agente] ?? '#2980b9'),
                       border: `1px solid ${apagado ? 'var(--dim)' : (COLOR[n.agente] ?? '#2980b9')}`,
                       borderRadius: '8px', width: 180, fontSize: 10,
                       whiteSpace: 'pre-wrap', textAlign: 'left', padding: 6,
                       opacity: apagado ? 0.45 : 1,
                     },
                   });
                   edges.push({ id: `e_${cabecera}_${id}`, source: cabecera, target: id, animated: n.estado === 'en curso', style: { stroke: 'var(--dim)' } });
                 });
                 y += 120;
               });

               return (
                 <div style={{ flex: 1, height: '100%', background: '#050a0b', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                   <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--dim)', color: 'var(--acc)', fontSize: 12 }}>
                     {tituloDelDebate(rondas)}
                   </div>
                   {/* El aviso solo aparece cuando hay algo que decir: si la
                       antítesis es un eco de la tesis, o si una ronda concluye
                       lo mismo que la anterior. Un cartel permanente de «todo
                       va bien» se deja de leer a la tercera vez, y entonces
                       tampoco se lee este. */}
                   {(() => {
                     const aviso = avisoDelDebate(rondas);
                     return aviso ? (
                       <div style={{ padding: '6px 12px', borderBottom: '1px solid var(--dim)', color: '#e0a030', fontSize: 11, lineHeight: 1.4 }}>
                         {aviso}
                       </div>
                     ) : null;
                   })()}
                   <div style={{ flex: 1, minHeight: 0 }}>
                     <ReactFlow nodes={nodes} edges={edges} fitView>
                       <Background color="#222" gap={16} />
                       <Controls />
                     </ReactFlow>
                   </div>
                 </div>
               );
            }
