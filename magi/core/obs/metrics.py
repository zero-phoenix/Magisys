"""
Observabilidad (Plan MAGI 9.0 §3.4).

EL PROBLEMA
===========
Naoko solo se enteraba de EXCEPCIONES: se suscribía a error.critical,
provider.fail y system.crash. Todo lo demás era invisible para ella.

En la práctica eso significa que no veía nada de lo que de verdad degrada el
sistema día a día: un proveedor que responde en 25 s en vez de 3, una
herramienta que falla el 40 % de las veces, una tarea que consume diez veces más
tokens que la media, o un proveedor que cambia el modelo detrás del mismo nombre
sin avisar. Nada de eso lanza una excepción; todo eso arruina la experiencia.

Este módulo la convierte de reactiva en proactiva: se suscribe al bus, agrega, y
emite alertas cuando un indicador se sale de rango.
"""
from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

WINDOW = 200          # muestras por serie
ALERT_COOLDOWN_S = 300.0

# ------------------------------------------------------------------ emisión
# Los backends NO conocen el bus ni el kernel: publican su medida con esta
# función de módulo y el colector vivo la recoge. Si no hay kernel (tests,
# uso como librería), es un no-op barato. Este gancho es lo que faltaba para
# que `obs.metrics.providers` dejara de estar vacío: `record_provider` existía
# y nadie lo llamaba.

_colector: MetricsCollector | None = None


def emit_provider_metric(provider: str, latency_ms: float, ok: bool,
                         family: str = "") -> None:
    """Publica una medida real de llamada a proveedor hacia el colector."""
    if _colector is None:
        return
    try:
        _colector.record_provider(provider, latency_ms, ok)
        if not ok:
            logger.info("[obs] proveedor '%s' (familia %s) marcado con fallo",
                        provider, family or "?")
    except Exception as e:  # noqa: BLE001 — telemetría nunca propaga
        logger.debug("[obs] emit_provider_metric falló: %s", e)


@dataclass
class Series:
    """Ventana deslizante con percentiles."""
    values: deque = field(default_factory=lambda: deque(maxlen=WINDOW))

    def add(self, v: float) -> None:
        self.values.append(v)

    def _pct(self, p: float) -> float:
        if not self.values:
            return 0.0
        s = sorted(self.values)
        return s[min(len(s) - 1, int(len(s) * p))]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def p50(self) -> float:
        return self._pct(0.50)

    @property
    def p95(self) -> float:
        return self._pct(0.95)

    @property
    def p99(self) -> float:
        return self._pct(0.99)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self.values else 0.0

    def snapshot(self) -> dict[str, float]:
        return {"n": self.n, "p50": round(self.p50, 1), "p95": round(self.p95, 1),
                "p99": round(self.p99, 1), "mean": round(self.mean, 1)}


@dataclass
class Counter:
    ok: int = 0
    fail: int = 0

    def record(self, success: bool) -> None:
        if success:
            self.ok += 1
        else:
            self.fail += 1

    @property
    def total(self) -> int:
        return self.ok + self.fail

    @property
    def failure_rate(self) -> float:
        return self.fail / self.total if self.total else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {"ok": self.ok, "fail": self.fail, "total": self.total,
                "failure_rate": round(self.failure_rate, 3)}


@dataclass
class Alert:
    kind: str            # "latency" | "tool_failures" | "provider_down" | "drift"
    subject: str         # proveedor, herramienta, familia…
    detail: str
    severity: str = "warning"   # "warning" | "critical"
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "subject": self.subject, "detail": self.detail,
                "severity": self.severity, "ts": self.ts}


class MetricsCollector:
    """
    Agrega lo que pasa por el bus y emite alertas.

    Umbrales deliberadamente conservadores: una alerta que salta cada dos
    minutos deja de leerse, y entonces es peor que no tenerla.
    """

    def __init__(self, bus=None, *,
                 latency_p95_warn_ms: float = 20_000.0,
                 tool_failure_warn: float = 0.35,
                 min_samples: int = 8):
        self.bus = bus
        self.latency_p95_warn_ms = latency_p95_warn_ms
        self.tool_failure_warn = tool_failure_warn
        self.min_samples = min_samples

        self.provider_latency: dict[str, Series] = defaultdict(Series)
        self.provider_calls: dict[str, Counter] = defaultdict(Counter)
        self.agent_latency: dict[str, Series] = defaultdict(Series)
        self.tool_calls: dict[str, Counter] = defaultdict(Counter)
        self.task_tokens: dict[str, int] = defaultdict(int)
        self.alerts: list[Alert] = []
        self._last_alert: dict[str, float] = {}
        self.started_at = time.time()

        global _colector
        _colector = self
        self._guardar_en = 0.0
        self._cargar_persistidas()

    # ------------------------------------------------------------ persistencia

    def _cargar_persistidas(self) -> None:
        """Rehidrata las medidas de la sesión anterior (Fase 1 v5.29)."""
        from magi.core.obs import store
        for proveedor, d in (store.cargar_provider_stats() or {}).items():
            try:
                c = self.provider_calls[proveedor]
                c.ok += int(d.get("n_ok", 0))
                c.fail += int(d.get("n_fail", 0))
                p50 = float(d.get("p50_ema", 0.0))
                if p50 > 0:
                    self.provider_latency[proveedor].add(p50)
            except Exception:
                continue

    def _persistir(self) -> None:
        from magi.core.obs import store
        stats = {}
        for p, c in self.provider_calls.items():
            s = self.provider_latency.get(p)
            stats[p] = {"n_ok": c.ok, "n_fail": c.fail,
                        "p50_ema": round(s.p50, 1) if s and s.n else 0.0,
                        "p95_ema": round(s.p95, 1) if s and s.n else 0.0}
        store.guardar_provider_stats(stats)

    # ------------------------------------------------------------ suscripción

    def attach(self, bus) -> None:
        """Se engancha al bus. Sin esto el colector es andamiaje."""
        self.bus = bus
        bus.subscribe("agent.tool_result", self._on_tool_result)
        bus.subscribe("provider.metric", self._on_provider_metric)
        bus.subscribe("agent.turn_done", self._on_turn_done)
        logger.info("[obs] colector de métricas enganchado al bus")

    async def _on_tool_result(self, event) -> None:
        payload = getattr(event, "payload", {}) or {}
        for r in payload.get("results", []):
            self.record_tool(r.get("tool", "?"), bool(r.get("ok")))

    async def _on_provider_metric(self, event) -> None:
        p = getattr(event, "payload", {}) or {}
        self.record_provider(p.get("provider", "?"), p.get("latency_ms", 0.0),
                             bool(p.get("ok", True)))

    async def _on_turn_done(self, event) -> None:
        p = getattr(event, "payload", {}) or {}
        self.record_agent(p.get("agent", "?"), p.get("elapsed_s", 0.0) * 1000)
        if p.get("task_id"):
            self.task_tokens[p["task_id"]] += p.get("tokens", 0)

    # --------------------------------------------------------------- registro

    def record_provider(self, provider: str, latency_ms: float,
                        ok: bool = True) -> list[Alert]:
        self.provider_calls[provider].record(ok)
        if ok and latency_ms > 0:
            self.provider_latency[provider].add(latency_ms)
        # Persistencia con freno: como mucho cada 30 s, jamás en el camino
        # crítico de una respuesta.
        now = time.monotonic()
        if now >= self._guardar_en:
            self._guardar_en = now + 30.0
            self._persistir()
        return self._check_provider(provider)

    def record_tool(self, tool: str, ok: bool) -> list[Alert]:
        self.tool_calls[tool].record(ok)
        return self._check_tool(tool)

    def record_agent(self, agent: str, elapsed_ms: float) -> None:
        if elapsed_ms > 0:
            self.agent_latency[agent].add(elapsed_ms)

    # ---------------------------------------------------------------- alertas

    def _emit(self, alert: Alert) -> list[Alert]:
        key = f"{alert.kind}:{alert.subject}"
        now = time.time()
        if now - self._last_alert.get(key, 0.0) < ALERT_COOLDOWN_S:
            return []          # ya avisamos hace poco; no repetir
        self._last_alert[key] = now
        self.alerts.append(alert)
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-100:]
        logger.warning("[obs] %s", alert.detail)
        if self.bus is not None:
            import asyncio

            from magi.core.bus import BusEvent
            try:
                asyncio.get_running_loop()
                asyncio.create_task(self.bus.publish(BusEvent(
                    topic="obs.alert", payload=alert.to_dict(), critical=True)))
            except RuntimeError:
                pass   # sin bucle en marcha (tests síncronos)
        return [alert]

    def _check_provider(self, provider: str) -> list[Alert]:
        s = self.provider_latency[provider]
        c = self.provider_calls[provider]
        out: list[Alert] = []
        if s.n >= self.min_samples and s.p95 > self.latency_p95_warn_ms:
            out += self._emit(Alert(
                "latency", provider,
                f"{provider}: p95 de {s.p95/1000:.1f}s sobre {s.n} llamadas "
                f"(umbral {self.latency_p95_warn_ms/1000:.0f}s). "
                f"Considera sacarlo de rotación.", "warning"))
        if c.total >= self.min_samples and c.failure_rate > 0.5:
            out += self._emit(Alert(
                "provider_down", provider,
                f"{provider}: {c.fail} fallos de {c.total} "
                f"({c.failure_rate:.0%}).", "critical"))
        return out

    def _check_tool(self, tool: str) -> list[Alert]:
        c = self.tool_calls[tool]
        if c.total >= self.min_samples and c.failure_rate > self.tool_failure_warn:
            return self._emit(Alert(
                "tool_failures", tool,
                f"herramienta '{tool}': {c.fail} fallos de {c.total} "
                f"({c.failure_rate:.0%}). Puede ser un prompt confuso o una "
                f"herramienta rota.", "warning"))
        return []

    # ------------------------------------------------------------- exposición

    def snapshot(self) -> dict[str, Any]:
        """Alimenta el panel de salud de la GUI y la vigilancia de Naoko."""
        return {
            "uptime_s": round(time.time() - self.started_at, 1),
            "providers": {
                p: {**self.provider_latency[p].snapshot(),
                    **self.provider_calls[p].snapshot()}
                for p in set(self.provider_latency) | set(self.provider_calls)
            },
            "agents": {a: s.snapshot() for a, s in self.agent_latency.items()},
            "tools": {t: c.snapshot() for t, c in self.tool_calls.items()},
            "tasks_tracked": len(self.task_tokens),
            "total_tokens": sum(self.task_tokens.values()),
            "alerts": [a.to_dict() for a in self.alerts[-20:]],
        }

    def health_summary(self) -> str:
        """Texto compacto para el prompt de Naoko."""
        snap = self.snapshot()
        lines = []
        for p, m in sorted(snap["providers"].items()):
            if m.get("total", 0) == 0:
                continue
            lines.append(f"- {p}: p95 {m['p95']/1000:.1f}s · "
                         f"{m['ok']}/{m['total']} ok")
        for t, m in sorted(snap["tools"].items()):
            if m["failure_rate"] > 0.2:
                lines.append(f"- herramienta {t}: {m['failure_rate']:.0%} de fallos")
        if not lines:
            return "Sin métricas todavía."
        alerts = snap["alerts"]
        head = f"{len(alerts)} alerta(s) activa(s)\n" if alerts else ""
        return head + "\n".join(lines)


# ------------------------------------------------------------ sonda canaria

CANARY_PROMPTS = [
    ("Responde únicamente con el número: ¿cuánto es 17 por 3?", "51"),
    ("Responde únicamente con una palabra: ¿capital de Francia?", "paris"),
    ("Responde únicamente con la palabra OK.", "ok"),
]


@dataclass
class DriftReport:
    provider: str
    matched: int
    total: int
    drifted: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "matched": self.matched,
                "total": self.total, "drifted": self.drifted,
                "detail": self.detail}


async def canary_probe(registry, provider_id: str,
                       threshold: float = 0.5) -> DriftReport:
    """
    Deriva silenciosa del proveedor (§I.8 del documento de arquitectura).

    Un proveedor puede cambiar el modelo detrás del mismo nombre sin avisar, y
    eso rompe en silencio la comparabilidad entre dos ejecuciones. Estaba
    especificado desde la primera versión del plan y nunca se implementó.

    Tres instrucciones fijas con respuesta conocida, temperatura cero. Si
    fallan más de la mitad, el proveedor ya no es el que era.
    """
    from magi.core.providers.base import CompletionRequest, Message

    matched, details = 0, []
    for prompt, expected in CANARY_PROMPTS:
        try:
            resp = await registry.complete(
                CompletionRequest(
                    messages=[Message("user", prompt)],
                    temperature=0.0, max_tokens=16, timeout_s=30.0,
                    probe=True),
                prefer=provider_id, use_cache=False, max_attempts=1)
            got = (resp.content or "").strip().lower()
            if expected in got:
                matched += 1
            else:
                details.append(f"'{prompt[:28]}…' -> {got[:40]!r} (esperado {expected!r})")
        except Exception as e:
            details.append(f"'{prompt[:28]}…' -> error: {e}")

    total = len(CANARY_PROMPTS)
    drifted = (matched / total) < threshold
    return DriftReport(provider_id, matched, total, drifted, "; ".join(details))
