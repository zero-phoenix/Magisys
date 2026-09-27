"""
Subagentes especializados de solo lectura (Megaplan F2; motor Groq desde v5.28.0).

Invariantes de F2:
1. Motor SUBORDINADO: desde el mandato de 2026-09-27, los subagentes corren
   en el registro de SUBAGENTES — Groq (la única clave permitida) primero y
   g4f de respaldo. NUNCA usan las familias del debate principal, y el
   enjambre NUNCA usa Groq: papeles separados, diversidad intacta.
2. Solo lectura: nunca escribe ni muta el sistema.
3. Devuelve conclusión sintetizada, no volcado íntegro de ficheros (ahorro neto de contexto).
4. Turno único y temperatura baja.
5. Tope duro por nodo y ronda (máximo 2 subagentes para evitar agotar cuotas).
6. Traza visible: publica eventos de trazabilidad.
7. SIN FÁBRICA DE VERDES: la versión anterior devolvía «verificado sin
   hallazgos críticos» SIN COMPROBAR NADA cuando no había motor. Un
   subagente que no pudo mirar lo dice — una conclusión inventada con forma
   de veredicto es el fallo más caro que este sistema puede producir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_SUBAGENTES_POR_NODO_Y_RONDA = 2


@dataclass
class SubagenteResultado:
    """Resultado sintético emitido por un subagente de solo lectura."""
    nodo: str
    familia: str
    mision: str
    conclusion: str
    tokens_estimados: int = 0
    herramientas_usadas: list[str] = field(default_factory=list)
    exito: bool = True
    error: str = ""

    def render(self) -> str:
        if not self.exito:
            return f"[{self.nodo}/subagente-{self.familia}] Error: {self.error}"
        return (
            f"[{self.nodo}/subagente-{self.familia} · {self.mision}]\n"
            f"Conclusión: {self.conclusion}"
        )


class GestorSubagentes:
    """Controla la invocación, cuotas y límites de subagentes por nodo y ronda."""

    def __init__(self, limite_por_nodo: int = MAX_SUBAGENTES_POR_NODO_Y_RONDA):
        self.limite_por_nodo = limite_por_nodo
        self._conteo: dict[tuple[str, int], int] = {}

    def registrar_intento(self, nodo: str, round_num: int) -> tuple[bool, str]:
        clave = (nodo.upper(), round_num)
        actual = self._conteo.get(clave, 0)
        if actual >= self.limite_por_nodo:
            return False, (
                f"TOPE EXCEDIDO: {nodo} ya invocó {actual} subagentes en la ronda {round_num}. "
                f"Límite máximo permitido: {self.limite_por_nodo}."
            )
        self._conteo[clave] = actual + 1
        return True, ""

    def subagentes_usados(self, nodo: str, round_num: int) -> int:
        return self._conteo.get((nodo.upper(), round_num), 0)

    def reiniciar(self) -> None:
        self._conteo.clear()


_GESTOR_GLOBAL = GestorSubagentes()


async def _ejecutar_con_subagentes(mision: str) -> tuple[str, str | None]:
    """
    Corre la misión en el registro de SUBAGENTES (Groq primero, g4f detrás).

    Devuelve (conclusión, familia_real_que_respondió). Conclusión vacía =
    no había motor: el llamador lo declara como fallo honesto, NUNCA como
    «verificado sin hallazgos» — que es lo que hacía la versión stub.
    """
    from ...core.providers.base import CompletionRequest, Message, ProviderError
    from ...core.providers.cloud import get_subagent_registry

    sistema = (
        "Eres un subagente de solo lectura de MAGI. Analizas, contrastas y "
        "devuelves UNA conclusión sintética de pocas líneas con evidencia "
        "directa (fichero, línea o dato) por afirmación. Si no puedes "
        "verificar algo, lo dices: 'SIN COMPROBAR'. Nunca inventas hallazgos."
    )
    try:
        reg = await get_subagent_registry()
        resp = await reg.complete(CompletionRequest(
            messages=[Message("system", sistema), Message("user", mision)],
            timeout_s=90.0, presupuesto_s=90.0,
            temperature=0.2, max_tokens=500, hedge=False,
            tag="subagente",
        ))
    except (ProviderError, Exception) as e:  # noqa: BLE001 — se declara, no se disimula
        logger.warning("[subagente] sin motor: %s", e)
        return "", None
    contenido = (resp.content or "").strip()
    if not contenido or contenido.startswith("[Inferencia no disponible"):
        return "", None
    return contenido, resp.family


async def despachar_subagente(
    *,
    nodo: str,
    familia: str,
    mision: str,
    round_num: int = 1,
    gestor: GestorSubagentes | None = None,
    bus: Any = None,
    task_id: str = "",
    ejecutor: Any = None,
) -> SubagenteResultado:
    """
    Despacha un subagente de solo lectura de la misma familia que su nodo.

    Retorna la conclusión sintética (máximo unas líneas con evidencia directa).
    """
    g = gestor or _GESTOR_GLOBAL
    ok_cuota, err_cuota = g.registrar_intento(nodo, round_num)
    if not ok_cuota:
        logger.warning(err_cuota)
        return SubagenteResultado(
            nodo=nodo,
            familia=familia,
            mision=mision,
            conclusion="",
            exito=False,
            error=err_cuota,
        )

    # Si hay ejecutor externo inyectado (o en tests), delegar en él
    if ejecutor is not None:
        conclusion = await ejecutor(nodo=nodo, familia=familia, mision=mision)
    else:
        conclusion, familia_real = await _ejecutar_con_subagentes(mision)
        familia = familia_real or familia

    tokens_estimados = max(1, len(conclusion.split()))
    resultado = SubagenteResultado(
        nodo=nodo,
        familia=familia,
        mision=mision,
        conclusion=conclusion,
        tokens_estimados=tokens_estimados,
        exito=bool(conclusion),
        error="" if conclusion else "sin motor de subagentes disponible",
    )
    if not conclusion:
        return resultado

    if bus is not None and hasattr(bus, "publish"):
        try:
            from ...core.bus import BusEvent
            await bus.publish(
                BusEvent(
                    topic="SUBAGENT_TRACE",
                    payload={
                        "task_id": task_id,
                        "nodo": nodo,
                        "familia": familia,
                        "mision": mision,
                        "conclusion": conclusion,
                        "tokens_estimados": tokens_estimados,
                    },
                )
            )
        except Exception as e:
            logger.debug(f"No se pudo publicar SUBAGENT_TRACE: {e}")

    return resultado
