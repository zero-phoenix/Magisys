/**
 * L5 — preguntar a Lilim sin pasar por una ronda.
 *
 * La capa local existía desde la v5.25 y sólo era alcanzable como herramienta
 * del enjambre: para preguntarle algo había que abrir una tarea y esperar a que
 * un nodo decidiera usarla. Una respuesta de 0-3,5 ms detrás de un debate.
 *
 * Este panel la pone donde tiene sentido. Dos cosas se enseñan siempre y a
 * propósito: **cuánto tardó** y **de dónde sale la respuesta**. Sin procedencia,
 * una respuesta local es indistinguible de una inventada — y no inventar es
 * justamente lo que distingue a Lilim del puente de nube.
 */
import { useState } from "react";

interface Respuesta {
  ok: boolean;
  respuesta?: string;
  ms?: number;
  error?: string;
}

export function LilimPanel({ askLilim }: { askLilim: (p: string) => Promise<any> }) {
  const [texto, setTexto] = useState("");
  const [res, setRes] = useState<Respuesta | null>(null);
  const [cargando, setCargando] = useState(false);

  const preguntar = async () => {
    const p = texto.trim();
    if (!p || cargando) return;
    setCargando(true);
    try {
      setRes(await askLilim(p));
    } catch (e: any) {
      setRes({ ok: false, error: String(e?.message || e) });
    } finally {
      setCargando(false);
    }
  };

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10, height: "100%" }}>
      <div style={{ fontSize: 11, color: "var(--dim)" }}>
        Capa local: sin red, sin GPU, un hilo. Si no lo sabe, lo dice.
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") preguntar(); }}
          placeholder="controles de sega saturn, repos de emudev, novedades 2025…"
          style={{ flex: 1, background: "#0a1214", color: "#cfe0e4", border: "1px solid var(--dim)", padding: "6px 8px", fontSize: 12 }}
        />
        <button className="bt go" onClick={preguntar} disabled={cargando}>
          {cargando ? "…" : "Preguntar"}
        </button>
      </div>

      {res && (
        <div style={{ flex: 1, overflow: "auto", background: "#050a0b", border: "1px solid var(--dim)", padding: 10 }}>
          {/* La medición va arriba y siempre: es la mitad del valor de esta capa. */}
          {res.ok && (
            <div style={{ fontSize: 10, color: "var(--acc2)", marginBottom: 6 }}>
              {res.ms} ms · local, sin red
            </div>
          )}
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12, color: res.ok ? "#cfe0e4" : "#f55" }}>
            {res.ok ? res.respuesta : `error: ${res.error}`}
          </pre>
        </div>
      )}
    </div>
  );
}

// Default export, como el resto de paneles del cajon.
export default LilimPanel;
