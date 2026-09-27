"""
Registro de proveedores con diversidad de familias REAL (Plan MAGI 9.0 §1.1).

EL BUG QUE ESTO ARREGLA
=======================
v5.0.28, magi/core/providers/cloud.py:122-123:

    if model in ["claude-3.5-sonnet", "qwen-2.5", "deepseek"]:
        model = "gpt-4o"

Melchior pedía deepseek, Balthasar claude-3.5-sonnet, Casper qwen-2.5.
Los tres se reescribían a gpt-4o. El enjambre entero era UN modelo con tres
prompts, y todo el valor epistemológico del debate popperiano (que el crítico
tenga sesgos distintos al proponente) no existía.

Aquí cada backend declara su `family`. `select_for_swarm()` reparte familias
distintas entre los tres nodos, y cuando no puede, lo DICE en vez de disimularlo.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from .base import (
    BaseProvider,
    CompletionRequest,
    CompletionResponse,
    Delta,
    ProviderError,
    ProviderTimeout,
)
from .cache import TTLCache, make_key
from .circuit import CircuitBreaker

logger = logging.getLogger(__name__)

SWARM_ROLES = ("MELCHIOR", "BALTHASAR", "CASPER")


def _techo_dinamico_s(provider: BaseProvider, timeout_s: float,
                      probe: bool) -> float | None:
    """
    Techo de espera efectivo para un proveedor (v6.0 §A7).

    El log del 16-ago tiene colas enteras pagadas por un solo candidato
    lento: la misma latencia de 24 s esperada porque `req.timeout_s` es de
    150 s y nadie consideraba lo que el proveedor YA demostró. Si este
    proveedor tiene mediciones, se le dejan `3 × (mejor medida) + 5 s` (piso
    6 s): no se le corta una respuesta que dentro de lo normal, pero una
    familia que demostró responder en 2 s no puede secuestrar 24.

    Devuelve None si no hay nada que decir (sin medida o es una sonda, cuyo
    tiempo es un dato y no debe mutilarse).
    """
    if probe:
        return None
    medir = getattr(provider, "mejor_latencia_ms", None)
    if medir is None:
        return None
    mejor = medir()
    if mejor is None:
        return None
    return min(timeout_s, max(6.0, mejor / 1000.0 * 3.0 + 5.0))

#: ORDEN DE MÉRITO: quién se lleva la mejor familia.
#:
#: No es el orden en que hablan (Melchior → Balthasar → Casper). Es el orden en
#: que se reparte lo bueno, y son cosas distintas:
#:
#:   1.º BALTHASAR — es el único que EJECUTA para refutar. Su turno es el más
#:       caro en herramientas y el que más se repite cuando algo falla, así que
#:       es donde una familia lenta más daño hace. Y si la refutación llega
#:       tarde o a medias, el debate pierde su parte más valiosa: la que caza
#:       los fallos de verdad.
#:
#:   2.º CASPER — es QUIEN TE HABLA. Su síntesis es la respuesta que lees, y
#:       además propone y ejecuta para demostrarla. Si tarda, esperas tú.
#:
#:   3.º MELCHIOR — su tesis se lanza en 2-3 variantes EN PARALELO, así que el
#:       tiempo de pared es el de una sola llamada. Es el nodo que mejor
#:       absorbe una familia algo más lenta.
#:
#: Antes se repartía en el orden de `SWARM_ROLES`, es decir: la mejor familia
#: para el que menos la necesita.
ORDEN_DE_MERITO = ("BALTHASAR", "CASPER", "MELCHIOR")


@dataclass
class Registration:
    provider: BaseProvider
    priority: int = 100          # menor = se prueba antes
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    available: bool | None = None   # None = aún no sondeado
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0

    @property
    def id(self) -> str:
        return self.provider.id

    @property
    def family(self) -> str:
        return self.provider.family


@dataclass
class SwarmAssignment:
    """Qué proveedor le toca a cada nodo, y si la diversidad se degradó."""
    by_role: dict[str, str]
    families: dict[str, str]
    diversity: str            # "full" | "partial" | "degraded" | "none"
    note: str = ""

    def degraded_reason_for(self, role: str) -> str | None:
        if self.diversity == "full":
            return None
        others = [r for r in self.by_role if r != role
                  and self.families.get(r) == self.families.get(role)]
        if others:
            return f"misma familia ({self.families.get(role)}) que {', '.join(others)}"
        return None


class ProviderRegistry:
    """
    Selecciona proveedores, aplica cortacircuitos, caché y contabilidad.

    Todo lo que en v5.0.28 estaba definido-pero-no-llamado, aquí se llama.
    """

    def __init__(self, cache_maxsize: int = 500, cache_ttl_s: float = 3600.0,
                 metrics=None):
        self._regs: dict[str, Registration] = {}
        # §3.4: colector opcional. Sin él, el registro funciona igual; con él,
        # Naoko ve latencias y tasas de fallo en vez de solo excepciones.
        self.metrics = metrics
        self.cache: TTLCache[CompletionResponse] = TTLCache(cache_maxsize, cache_ttl_s)
        self._probe_lock = asyncio.Lock()
        #: Media histórica en ms por familia, según la sonda. Vacío = aún no se
        #: ha medido nada y manda `priority`, que es un número escrito a mano.
        self._medias_ms: dict[str, float] = {}

    def aplicar_medidas(self, medias_ms: dict[str, float]) -> None:
        """
        Le dice al registro lo que la sonda ha MEDIDO, por familia.

        POR QUÉ HACE FALTA
        ==================
        `priority` es un entero escrito a mano cuando se registró el proveedor.
        Ordenar por él significa repartir el enjambre según lo que alguien creyó
        hace semanas, y estos servicios gratuitos cambian solos: el 2026-08-13,
        cinco de las seis familias marcadas «verificadas» el día 6 estaban
        rotas, y una marcada «imposible» funcionaba.

        Con esto, `select_for_swarm` reparte por la media histórica real —la
        media de las medias diarias, que es lo que `sonda.media_historica`
        calcula— y BALTHASAR recibe la mejor familia MEDIDA, no la mejor
        recordada.

        Las familias sin medida NO se ponen las primeras por defecto: «no lo
        sé» no es «es rápida». Van detrás de todas las medidas, y entre ellas
        manda `priority`.
        """
        self._medias_ms = {f: ms for f, ms in (medias_ms or {}).items()
                           if isinstance(ms, (int, float)) and ms > 0}

    def _merito(self, familia: str, regs: list[Registration]) -> tuple:
        """Clave de orden: primero lo medido y más rápido; luego lo no medido."""
        medida = self._medias_ms.get(familia)
        # (0, ms) ordena antes que (1, ...) para cualquier ms: lo medido manda.
        if medida is not None:
            return (0, medida, regs[0].priority)
        return (1, 0.0, regs[0].priority)

    # ---------------------------------------------------------------- registro

    def register(self, provider: BaseProvider, priority: int = 100) -> None:
        self._regs[provider.id] = Registration(provider=provider, priority=priority)
        logger.info("[registry] %s registrado (familia=%s, local=%s, prio=%d)",
                    provider.id, provider.family, provider.is_local, priority)

    def unregister(self, provider_id: str) -> None:
        self._regs.pop(provider_id, None)

    def get(self, provider_id: str) -> Registration | None:
        return self._regs.get(provider_id)

    def all(self) -> list[Registration]:
        return sorted(self._regs.values(), key=lambda r: r.priority)

    async def probe_all(self, timeout_s: float = 5.0) -> None:
        """Sondea disponibilidad en paralelo. Se llama al arrancar."""
        async with self._probe_lock:
            async def probe(reg: Registration) -> None:
                try:
                    reg.available = await asyncio.wait_for(
                        reg.provider.available(), timeout=timeout_s)
                except Exception as e:
                    logger.debug("[registry] sonda %s falló: %s", reg.id, e)
                    reg.available = False
            await asyncio.gather(*(probe(r) for r in self._regs.values()),
                                 return_exceptions=True)
        ok = [r.id for r in self._regs.values() if r.available]
        logger.info("[registry] disponibles: %s", ", ".join(ok) or "NINGUNO")

    # -------------------------------------------------------------- selección

    def healthy(self, *, need_tools: bool = False, need_vision: bool = False,
                need_stream: bool = False) -> list[Registration]:
        out = []
        for reg in self.all():
            if reg.available is False:
                continue
            if not reg.breaker.allows():
                continue
            p = reg.provider
            if need_tools and not p.supports_tools:
                continue
            if need_vision and not p.supports_vision:
                continue
            if need_stream and not p.supports_stream:
                continue
            out.append(reg)
        return out

    def families_available(self) -> list[str]:
        seen, out = set(), []
        for reg in self.healthy():
            if reg.family not in seen:
                seen.add(reg.family)
                out.append(reg.family)
        return out

    def select_for_swarm(self, roles: Sequence[str] = SWARM_ROLES,
                         **caps) -> SwarmAssignment:
        """
        Reparte proveedores entre los nodos maximizando familias distintas.

        - 3+ familias sanas -> "full": cada nodo, una familia.
        - 2 familias        -> "partial": CASPER (el juez) se aísla en la suya,
                               los otros dos comparten. Se declara.
        - 1 familia         -> "degraded": divergencia forzada por temperatura
                               y semilla, y se dice en la tarjeta de la GUI.
        - 0                 -> "none".
        """
        pool = self.healthy(**caps)
        if not pool:
            return SwarmAssignment({}, {}, "none", "no hay proveedores sanos")

        by_family: dict[str, list[Registration]] = {}
        for reg in pool:
            by_family.setdefault(reg.family, []).append(reg)
        # Orden por MÉRITO MEDIDO si la sonda ha dicho algo; si no, por la
        # prioridad escrita a mano. Ver `aplicar_medidas`.
        fams = sorted(by_family, key=lambda f: self._merito(f, by_family[f]))

        by_role: dict[str, str] = {}
        families: dict[str, str] = {}

        if len(fams) >= len(roles):
            # Se reparte por MÉRITO, no por orden de intervención: la mejor
            # familia va a quien más la necesita. Ver ORDEN_DE_MERITO.
            #
            # `roles` puede venir con otro contenido (tests, futuras variantes),
            # así que se respeta: del orden de mérito solo se usan los roles
            # que de verdad están en juego, y los que no figuren en él van
            # detrás en su orden original. Sin esto, pasar una lista de roles
            # distinta dejaría a alguno sin familia.
            preferentes = [r for r in ORDEN_DE_MERITO if r in roles]
            resto = [r for r in roles if r not in preferentes]
            for role, fam in zip(preferentes + resto, fams, strict=False):
                by_role[role] = by_family[fam][0].id
                families[role] = fam
            return SwarmAssignment(by_role, families, "full")

        if len(fams) >= 2:
            judge = roles[-1]                      # CASPER se aísla
            by_role[judge] = by_family[fams[0]][0].id
            families[judge] = fams[0]
            for role in roles[:-1]:
                by_role[role] = by_family[fams[1]][0].id
                families[role] = fams[1]
            return SwarmAssignment(
                by_role, families, "partial",
                f"solo {len(fams)} familias sanas; {judge} aislado en {fams[0]}")

        only = fams[0]
        for role in roles:
            by_role[role] = by_family[only][0].id
            families[role] = only
        return SwarmAssignment(
            by_role, families, "degraded",
            f"una sola familia disponible ({only}); "
            f"divergencia forzada por temperatura y semilla")

    # -------------------------------------------------------------- inferencia

    async def complete(
        self, req: CompletionRequest, *,
        prefer: str | None = None,
        need_tools: bool = False, need_vision: bool = False,
        use_cache: bool = True, max_attempts: int = 3,
    ) -> CompletionResponse:
        """
        Ejecuta con failover. A diferencia de v5.0.28:
          - hay timeout duro por intento (antes: ninguno, cuelgue infinito)
          - el cortacircuitos se consulta y se actualiza de verdad
          - la caché tiene tope (antes: crecía sin límite)
          - se contabilizan tokens
          - el provider que se reporta es el que RESPONDIÓ
        """
        key = None
        if use_cache and not req.tools and not req.probe:
            key = make_key("complete", prefer, req.model, req.temperature,
                           [m.to_wire() for m in req.messages])
            hit = self.cache.get(key)
            if hit is not None:
                logger.debug("[registry] acierto de caché (%s)", hit.provider_id)
                return hit

        candidates = self._candidates(prefer, need_tools, need_vision)
        if not candidates:
            raise ProviderError("no hay proveedores sanos que cumplan los requisitos")

        # §E1 — el reloj corre para la CADENA, no para cada intento. Ver
        # `CompletionRequest.presupuesto_s`: sin esto, tres candidatos a 150 s
        # cada uno daban 450 s de pared para una sola llamada lógica, y el
        # máximo real medido en este equipo fue de 390 s.
        arranque_cadena = time.monotonic()
        presupuesto = req.presupuesto_s if not req.probe else None

        last_err: Exception | None = None
        for intento, reg in enumerate(candidates[:max_attempts]):
            restante = None
            if presupuesto is not None:
                restante = presupuesto - (time.monotonic() - arranque_cadena)
                # Menos de un segundo no da para nada: gastarlo solo sirve
                # para devolver un timeout más tarde.
                if restante < 1.0:
                    logger.warning(
                        "[registry] presupuesto agotado (%.0fs) tras %d "
                        "intento(s); no se prueban los %d restantes%s",
                        presupuesto, intento,
                        len(candidates[:max_attempts]) - intento,
                        f" [{req.tag}]" if req.tag else "")
                    last_err = last_err or ProviderTimeout(
                        f"la cadena excedió {presupuesto:.0f}s sin respuesta")
                    break

            started = time.monotonic()
            espera = _techo_dinamico_s(reg.provider, req.timeout_s, req.probe)
            espera = espera or req.timeout_s
            if restante is not None:
                espera = min(espera, restante)
            try:
                resp = await asyncio.wait_for(
                    reg.provider.complete(req), timeout=espera)
            except asyncio.TimeoutError:
                # Una SONDA que falla es un dato («este candidato está caído
                # ahora mismo»), no una razón para cerrarle el paso al tráfico
                # real. El 2026-08-16 cada canario con respuesta corta —correcta
                # pero corta— fallaba, abría cortacircuitos y el enjambre se
                # quedaba sin proveedores por haber querido medirlos.
                if not req.probe:
                    reg.breaker.record_failure()
                    if self.metrics is not None:
                        self.metrics.record_provider(reg.id, 0.0, ok=False)
                # El techo que se aplicó de verdad, no el nominal: con
                # presupuesto de cadena, `espera` puede ser bastante menor que
                # `req.timeout_s`, y decir el nominal haría el log mentiroso.
                last_err = ProviderTimeout(f"{reg.id} excedió {espera:.0f}s")
                logger.warning("[registry] %s TIMEOUT (%.0fs)%s", reg.id,
                               espera, " (sonda, no penaliza)" if req.probe else "")
                continue
            except Exception as e:
                if not req.probe:
                    reg.breaker.record_failure()
                    if self.metrics is not None:
                        self.metrics.record_provider(reg.id, 0.0, ok=False)
                last_err = e
                logger.warning("[registry] %s falló: %s%s", reg.id, e,
                               " (sonda, no penaliza)" if req.probe else "")
                continue

            latency = (time.monotonic() - started) * 1000.0
            reg.breaker.record_success(latency)
            if self.metrics is not None:
                self.metrics.record_provider(reg.id, latency, ok=True)
            reg.calls += 1
            reg.tokens_in += resp.usage.prompt_tokens
            reg.tokens_out += resp.usage.completion_tokens
            if key:
                self.cache.set(key, resp)
            return resp

        raise ProviderError(f"todos los proveedores fallaron: {last_err}") from last_err

    async def stream(
        self, req: CompletionRequest, *, prefer: str | None = None,
        need_tools: bool = False,
    ) -> AsyncIterator[Delta]:
        """Streaming con failover en el primer fallo antes del primer token."""
        candidates = self._candidates(prefer, need_tools, False)
        if not candidates:
            raise ProviderError("no hay proveedores sanos para streaming")

        last_err: Exception | None = None
        for reg in candidates:
            started = time.monotonic()
            emitted = False
            espera = _techo_dinamico_s(reg.provider, req.timeout_s, req.probe)
            try:
                if espera is None:
                    async for delta in reg.provider.stream(req):
                        emitted = True
                        yield delta
                else:
                    # El techo mira SOLO el primer token: una vez que el
                    # proveedor habló, la respuesta ya está en marcha y
                    # cortarla por abajo multiplicaría texto parcial.
                    it = reg.provider.stream(req)
                    try:
                        primer = await asyncio.wait_for(
                            it.__anext__(), timeout=espera)
                    except StopAsyncIteration:                   # pragma: no cover
                        raise ProviderError(f"{reg.id}: stream vacío") from None
                    emitted = True
                    yield primer
                    async for delta in it:
                        yield delta
                reg.breaker.record_success((time.monotonic() - started) * 1000.0)
                reg.calls += 1
                return
            except Exception as e:
                last_err = e
                reg.breaker.record_failure()
                if emitted:
                    # Ya salieron tokens: reintentar duplicaría texto en pantalla.
                    logger.error("[registry] %s cortó a mitad de stream: %s", reg.id, e)
                    raise
                logger.warning("[registry] %s falló antes del 1er token: %s", reg.id, e)
                continue
        raise ProviderError(f"streaming falló en todos: {last_err}") from last_err

    def _candidates(self, prefer: str | None, need_tools: bool,
                    need_vision: bool) -> list[Registration]:
        pool = self.healthy(need_tools=need_tools, need_vision=need_vision)
        # Ordenar: preferido primero, luego por estado del breaker (cerrado
        # antes que half-open), luego por latencia p95 observada, luego por
        # la prioridad estática. Así los proveedores lentos van al final sin
        # perder la preferencia explícita.
        def _sort_key(r: Registration):
            state_val = 0 if r.breaker.state() == "closed" else 1
            latency = r.breaker.p95_ms() or float("inf")
            # `prefer` casa por id O POR FAMILIA. Antes solo por id, y eso
            # obligaba a quien llamaba a saber el prefijo del backend
            # ("g4f-gpt", "groq-oss"...): la familia es el contrato estable,
            # el id es un detalle de implementación que ya cambió una vez.
            prefer_match = 0 if (prefer and (r.id == prefer or r.family == prefer)) else 1
            return (prefer_match, state_val, latency, r.priority)
        pool.sort(key=_sort_key)
        return pool

    # ----------------------------------------------------------- observabilidad

    def telemetry(self) -> dict:
        """Alimenta el panel de salud de la GUI y la vigilancia de Naoko."""
        return {
            "providers": [
                {
                    "id": r.id, "family": r.family,
                    "local": r.provider.is_local,
                    "available": r.available,
                    "tools": r.provider.supports_tools,
                    "vision": r.provider.supports_vision,
                    "calls": r.calls,
                    "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                    **r.breaker.snapshot(),
                }
                for r in self.all()
            ],
            "families_available": self.families_available(),
            "cache": self.cache.stats(),
        }
