/**
 * Panel de aprobación con contexto (Plan MAGI 9.0 §7.3, §7.4).
 *
 * QUÉ ERA ESTO ANTES
 * ==================
 * Recibía `originalCode=""` desde App.tsx, así que la columna "Código
 * Original (Estado Actual)" salía SIEMPRE VACÍA y la de la derecha pintaba
 * todo en verde. Debajo, la marca de "línea nueva" era
 * `!oldLines.includes(line)`, que no muestra borrados, se traga las líneas
 * movidas y no distingue las repetidas.
 *
 * O sea: un panel titulado "Aprobación de Código Requerida" que no enseñaba
 * el cambio. Aprobar ahí era aprobar a ciegas creyendo que habías revisado —
 * y de las dos formas de aprobar a ciegas, esa es la mala.
 *
 * QUÉ ES AHORA
 * ============
 * Consume el evento `swarm.approval_required`, que trae los ficheros con su
 * contenido antes y después (§7.4), y los alinea con LCS (`lib/diff.ts`). Los
 * avisos que cambian la decisión —tests en rojo, cambio irreversible, órdenes
 * a ejecutar— van arriba del todo, no enterrados entre líneas de código.
 */
import { useMemo, useState } from "react";
import { ApprovalRequest, approvalWarnings } from "./lib/approval";
import { DiffLine, diffLines, diffSummary } from "./lib/diff";

const COLOR = {
  añadida: { fondo: "rgba(0, 255, 100, 0.13)", texto: "#7dfaa8", signo: "+" },
  borrada: { fondo: "rgba(255, 70, 70, 0.13)", texto: "#ff9a9a", signo: "−" },
  igual: { fondo: "transparent", texto: "#a8bcc1", signo: " " },
} as const;

function LineaDiff({ l }: { l: DiffLine }) {
  const c = COLOR[l.op];
  return (
    <div style={{ display: "flex", background: c.fondo, minHeight: "17px" }}>
      <span style={{ width: 44, textAlign: "right", paddingRight: 6, opacity: 0.4, userSelect: "none", flexShrink: 0 }}>
        {l.oldNumber ?? ""}
      </span>
      <span style={{ width: 44, textAlign: "right", paddingRight: 6, opacity: 0.4, userSelect: "none", flexShrink: 0 }}>
        {l.newNumber ?? ""}
      </span>
      <span style={{ width: 14, color: c.texto, userSelect: "none", flexShrink: 0 }}>
        {c.signo}
      </span>
      <span style={{ whiteSpace: "pre-wrap", color: c.texto, flex: 1 }}>
        {l.text || " "}
      </span>
    </div>
  );
}

interface Props {
  approval: ApprovalRequest | null;
  /** Texto suelto, para cuando el backend aún no manda el evento con datos. */
  fallbackText?: string;
  onApprove: () => void;
  onReject: () => void;
}

export default function DiffViewer({ approval, fallbackText, onApprove, onReject }: Props) {
  const [activo, setActivo] = useState(0);

  const cambios = approval?.changes ?? [];
  const actual = cambios[Math.min(activo, Math.max(0, cambios.length - 1))];

  const diff = useMemo(
    () => (actual && !actual.note ? diffLines(actual.before, actual.after) : null),
    [actual],
  );
  const avisos = approval ? approvalWarnings(approval) : [];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#050a0b", color: "#cfe0e4" }}>
      {/* --------------------------------------------------- cabecera */}
      <div style={{ padding: 10, background: "rgba(10, 20, 25, 0.9)", borderBottom: "1px solid var(--dim)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0, color: "var(--acc)", fontSize: 14 }}>
            Aprobación requerida
          </h3>
          <div style={{ fontSize: 11, color: "var(--dim)", marginTop: 3 }}>
            {approval
              ? `${approval.files_touched} fichero(s) · ` +
                (approval.tests_ran
                  ? approval.tests_passed ? "tests en verde" : "TESTS EN ROJO"
                  : "sin tests") +
                (approval.reversible ? " · reversible" : " · NO reversible")
              : "sin detalle del cambio: el backend no envió el contexto"}
          </div>
        </div>
        {/* A1/E3 — la procedencia del artefacto, donde se decide sobre él.
            Se calculaba desde la v11 y no se enseñaba en ninguna parte: se
            aprobaba un .exe sin ver de qué fuentes salía ni con qué hash. */}
        {approval?.manifiesto?.sha256 ? (
          <div style={{ fontSize: 10, color: "var(--dim)", minWidth: 0, textAlign: "right" }}>
            <div title={approval.manifiesto.exe}>
              artefacto: <b style={{ color: "var(--acc2)" }}>{(approval.manifiesto.exe || "").split(/[\\/]/).pop()}</b>
            </div>
            <div title={approval.manifiesto.sha256}>
              sha256 {approval.manifiesto.sha256.slice(0, 12)}… · {approval.manifiesto.fuentes} fuente(s) selladas · entrada {approval.manifiesto.entry}
            </div>
          </div>
        ) : null}
        <div style={{ display: "flex", gap: 10, flexShrink: 0 }}>
          <button className="bt go" style={{ background: "rgba(0, 255, 100, 0.2)", color: "#0f0" }} onClick={onApprove}>
            Apruebo
          </button>
          <button className="bt go" style={{ background: "rgba(255, 50, 50, 0.2)", color: "#f55" }} onClick={onReject}>
            Rechazo
          </button>
        </div>
      </div>

      {/* ----------------------------------------- avisos que deciden */}
      {avisos.length > 0 && (
        <div style={{ padding: "8px 12px", background: "rgba(255, 170, 0, 0.10)", borderBottom: "1px solid rgba(255,170,0,0.3)" }}>
          {avisos.map((a, i) => (
            <div key={i} style={{ fontSize: 12, color: "#ffcc66", marginBottom: 2 }}>
              ⚠ {a}
            </div>
          ))}
        </div>
      )}

      {approval?.summary && (
        <div style={{ padding: "8px 12px", fontSize: 12, color: "#9fb4b9", borderBottom: "1px solid var(--dim)", maxHeight: 110, overflowY: "auto", whiteSpace: "pre-wrap" }}>
          {approval.summary}
        </div>
      )}

      {approval && approval.commands.length > 0 && (
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--dim)", background: "rgba(255,50,50,0.06)" }}>
          <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 4 }}>
            Se ejecutará en tu máquina:
          </div>
          {approval.commands.map((c, i) => (
            <pre key={i} style={{ margin: 0, fontSize: 12, color: "#ffb3b3", whiteSpace: "pre-wrap" }}>
              $ {c}
            </pre>
          ))}
        </div>
      )}

      {/* --------------------------------------- selector de ficheros */}
      {cambios.length > 1 && (
        <div style={{ display: "flex", gap: 4, padding: "6px 10px", overflowX: "auto", borderBottom: "1px solid var(--dim)" }}>
          {cambios.map((c, i) => {
            const d = c.note ? null : diffLines(c.before, c.after);
            return (
              <button
                key={c.path}
                onClick={() => setActivo(i)}
                style={{
                  fontSize: 11, padding: "3px 8px", whiteSpace: "nowrap",
                  cursor: "pointer", borderRadius: 3,
                  border: "1px solid " + (i === activo ? "var(--acc)" : "var(--dim)"),
                  background: i === activo ? "rgba(0,200,255,0.12)" : "transparent",
                  color: i === activo ? "var(--acc)" : "#8fa4a9",
                }}
              >
                {c.path.split(/[\\/]/).pop()}{" "}
                <span style={{ opacity: 0.7 }}>{d ? diffSummary(d) : c.note}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* ------------------------------------------------------ cuerpo */}
      <div style={{ flex: 1, overflow: "auto", padding: 10, fontFamily: "monospace", fontSize: 12 }}>
        {!approval && (
          <div>
            <div style={{ color: "#ffcc66", marginBottom: 10 }}>
              El backend no envió el contexto del cambio, así que esto es el
              texto de la propuesta y NO un diff. No se puede saber desde aquí
              qué ficheros toca.
            </div>
            <pre style={{ whiteSpace: "pre-wrap", color: "#a8bcc1" }}>
              {fallbackText || "(sin contenido)"}
            </pre>
          </div>
        )}

        {approval && cambios.length === 0 && (
          <div style={{ color: "var(--dim)" }}>
            Esta propuesta no modifica ningún fichero.
          </div>
        )}

        {actual && (
          <>
            <div style={{ color: "var(--dim)", marginBottom: 6 }}>
              {actual.kind} · {actual.path}
              {!actual.revertible && (
                <span style={{ color: "#ff9a9a" }}> · sin copia previa</span>
              )}
            </div>
            {actual.note ? (
              <div style={{ color: "#ffcc66" }}>{actual.note}</div>
            ) : (
              <>
                {diff?.degraded && (
                  <div style={{ color: "#ffcc66", marginBottom: 6 }}>
                    ⚠ {diff.degraded}
                  </div>
                )}
                {diff?.lines.map((l, i) => <LineaDiff key={i} l={l} />)}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
