import React from 'react';
import { useMagiStore } from '../store';

export const PlanCard: React.FC = () => {
  // E1 — el de ESTA conversación, no el último que llegó. Antes se leía un
  // plan global: abrir otra tarea enseñaba el plan de la anterior, que es
  // peor que no enseñar ninguno — parece información y no lo es.
  const plan = useMagiStore((state) => state.planes[state.activeConversationId] || null);

  if (!plan || !plan.items || plan.items.length === 0) return null;

  return (
    <div
      style={{
        margin: '10px 16px',
        padding: '12px 16px',
        borderRadius: '6px',
        border: '1px solid #374151',
        backgroundColor: '#18202f',
        color: '#e5e7eb',
        fontSize: '13px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}>
          <span>📋</span>
          <span>PLAN VIVO DE TAREA ({plan.task_id || 'actual'})</span>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {plan.items.map((item: any, idx: number) => {
          const badgeColor =
            item.estado === 'hecha'
              ? '#10b981'
              : item.estado === 'haciendo'
              ? '#3b82f6'
              : item.estado === 'no se pudo'
              ? '#ef4444'
              : '#9ca3af';

          const icon =
            item.estado === 'hecha'
              ? '✓'
              : item.estado === 'haciendo'
              ? '▶'
              : item.estado === 'no se pudo'
              ? '✕'
              : '○';

          return (
            <div
              key={item.id || idx}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '4px 8px',
                borderRadius: '4px',
                backgroundColor: 'rgba(255, 255, 255, 0.03)',
              }}
            >
              <span style={{ color: badgeColor, fontWeight: 'bold', width: '16px', textAlign: 'center' }}>
                {icon}
              </span>
              <span
                style={{
                  fontSize: '11px',
                  padding: '1px 5px',
                  borderRadius: '3px',
                  backgroundColor: `${badgeColor}22`,
                  color: badgeColor,
                  fontWeight: 600,
                  textTransform: 'uppercase',
                }}
              >
                {item.estado}
              </span>
              <span style={{ flex: 1, fontFamily: 'sans-serif' }}>{item.descripcion}</span>
              {item.motivo && (
                <span style={{ fontSize: '11px', color: '#9ca3af', fontStyle: 'italic' }}>
                  ({item.motivo})
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
