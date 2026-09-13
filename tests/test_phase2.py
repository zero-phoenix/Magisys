"""
Fase 2 restante: paralelismo (§2.4), verificación ejecutable (§2.5) y
memoria episódica (§2.6).
"""
import asyncio
import os

import pytest

from magi.core.blackboard import Blackboard
from magi.core.bus import BusEvent, MagiBus
from magi.core.providers.backends.echo import EchoProvider
from magi.core.providers.cloud import FreeCloudLLM, set_registry
from magi.core.providers.registry import ProviderRegistry
from magi.core.store.state import TaskStore
from magi.core.verification import (
    ProposalVerifier,
    VerificationReport,
    extract_blocks,
)
from magi.modules.memory.episodic import EpisodicMemory
from magi.modules.swarm.parallel import (
    CRITIQUE_AXES,
    Proposal,
    critique_multi_axis,
    format_variants_for_critic,
    generate_variants,
)

# ------------------------------------------ §2.5 verificación ejecutable

def test_extract_blocks():
    text = "texto\n```python\nprint(1)\n```\nmás\n```bash\nls\n```"
    blocks = extract_blocks(text)
    assert [b[0] for b in blocks] == ["python", "bash"]


@pytest.mark.asyncio
async def test_syntax_error_is_caught_before_anyone_debates_it():
    """
    La clase de fallo más cara de v5.0.28: tres rondas de deliberación elegante
    sobre código con un SyntaxError dentro.
    """
    rep = await ProposalVerifier().verify(
        "Propuesta:\n```python\ndef roto(:\n    pass\n```")
    assert rep.had_code and not rep.ok
    assert rep.failures[0].stage == "syntax"
    assert "línea" in rep.failures[0].detail


@pytest.mark.asyncio
async def test_valid_code_passes_and_becomes_evidence():
    rep = await ProposalVerifier().verify(
        "```python\nassert 2 + 2 == 4\nprint('ok')\n```")
    assert rep.ok
    assert "no es una suposición" in rep.evidence_for_critic()


@pytest.mark.asyncio
async def test_runtime_error_is_caught():
    rep = await ProposalVerifier().verify(
        "```python\nraise ValueError('estalla en ejecución')\n```")
    assert not rep.ok
    assert rep.failures[0].stage == "run"
    assert "estalla en ejecución" in rep.failures[0].detail


@pytest.mark.asyncio
async def test_feedback_goes_back_to_the_author():
    rep = await ProposalVerifier().verify("```python\nx = (\n```")
    fb = rep.feedback_for_author()
    assert "NO pasa la verificación" in fb
    assert "corregida" in fb


@pytest.mark.asyncio
async def test_prose_without_code_is_not_blocked():
    rep = await ProposalVerifier().verify("Propongo revisar la arquitectura.")
    assert rep.ok and not rep.had_code


@pytest.mark.asyncio
async def test_unknown_language_is_skipped_not_failed():
    rep = await ProposalVerifier().verify("```rust\nfn main() {}\n```")
    assert rep.ok and rep.blocks[0].stage == "skipped"


@pytest.mark.asyncio
async def test_broken_json_is_caught():
    rep = await ProposalVerifier().verify('```json\n{"a": 1,,}\n```')
    assert not rep.ok


@pytest.mark.asyncio
async def test_blocks_are_verified_in_parallel():
    """
    Cinco bloques con una pausa de 0,4 s: en paralelo tienen que costar menos
    que en serie, y la serie se MIDE aquí mismo.

    Antes esto era `< 1.5` segundos de reloj, y llevaba un `skipif` para xdist
    porque con la CPU repartida entre workers el umbral no se cumplía aunque el
    verificador paralelizara perfectamente. O sea: el test medía el runner, no
    el código — la regla R12, aprendida cuatro veces en este proyecto, y la
    misma clase de bomba que tumbó el CI en la v5.17.0 con `t_melchior_ms < 900`.

    Ahora se mide el CONTROL en la misma corrida: un bloque suelto cinco veces
    (la serie) contra los cinco de golpe. Si la máquina va lenta, las dos
    medidas crecen juntas y la comparación sigue valiendo. Y sin umbral de
    reloj, el `skipif` sobra: bajo xdist también se cumple.
    """
    import time
    uno = "```python\nimport time; time.sleep(0.4)\n```\n"
    cinco = uno * 5

    t0 = time.monotonic()
    for _ in range(5):
        await ProposalVerifier().verify(uno)
    serie = time.monotonic() - t0

    t0 = time.monotonic()
    rep = await ProposalVerifier().verify(cinco)
    paralelo = time.monotonic() - t0

    assert rep.ok
    assert paralelo < serie * 0.7, (
        f"no se verificaron en paralelo: {paralelo:.2f}s frente a {serie:.2f}s "
        f"en serie, medidos en esta misma corrida")


@pytest.mark.asyncio
async def test_infinite_loop_hits_the_timeout():
    rep = await ProposalVerifier(timeout_s=2.0).verify(
        "```python\nwhile True: pass\n```")
    assert not rep.ok and "timeout" in rep.failures[0].detail


# ------------------------------------------------- §2.4 paralelismo

@pytest.fixture
async def agents():
    reg = ProviderRegistry()
    for fam in ("deepseek", "claude", "qwen"):
        reg.register(EchoProvider(f"g4f-{fam}", fam, canned=f"texto de {fam}"))
    await reg.probe_all()
    set_registry(reg)
    from magi.modules.swarm.agents import BalthasarAgent, MelchiorAgent
    bus = MagiBus()
    m = MelchiorAgent(Blackboard(), bus)
    b = BalthasarAgent(Blackboard(), bus)
    m.llm = b.llm = FreeCloudLLM(reg)
    yield m, b
    set_registry(None)


@pytest.mark.asyncio
async def test_variants_are_generated_in_parallel(agents):
    m, _ = agents
    props = await generate_variants(m, task_id="t", command="diseña algo",
                                    round_num=1, n=3)
    assert len(props) == 3
    assert [p.variant for p in props] == [0, 1, 2]
    assert props[0].label == "Enfoque A"


@pytest.mark.asyncio
async def test_agent_seed_is_restored_after_variants(agents):
    """Cada variante cambia la semilla; debe quedar como estaba."""
    m, _ = agents
    original = m.seed
    await generate_variants(m, task_id="t", command="x", round_num=1, n=3)
    assert m.seed == original


@pytest.mark.asyncio
async def test_variants_survive_partial_failure(agents):
    """Si una variante revienta, las demás siguen valiendo."""
    m, _ = agents
    calls = {"n": 0}
    original = m.generate_proposal

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("cuota agotada")
        return await original(*a, **kw)

    m.generate_proposal = flaky
    props = await generate_variants(m, task_id="t", command="x",
                                    round_num=1, n=3)
    assert len(props) == 2


@pytest.mark.asyncio
async def test_multi_axis_critique_covers_every_axis(agents):
    _, b = agents
    multi = await critique_multi_axis(b, task_id="t",
                                      proposal_text="def f(): pass",
                                      round_num=1)
    assert multi.axes_ok == len(CRITIQUE_AXES)
    rendered = multi.render()
    assert "Corrección" in rendered
    assert "Seguridad" in rendered
    assert "plataforma" in rendered.lower()


@pytest.mark.asyncio
async def test_multi_axis_is_faster_than_serial():
    """
    El nombre promete una comparación contra la serie; ahora la hace.

    DOS COSAS ESTABAN MAL, Y LA SEGUNDA LA DESTAPÓ LA PRIMERA
    ========================================================
    1. Medía `< 2.0` segundos de reloj, que no es «más rápido que en serie»:
       es «más rápido que un número». Un umbral absoluto mide la máquina donde
       corre (R12), y este ya había obligado a poner un `skipif` en el test de
       al lado.

    2. Al cambiarlo por la comparación de verdad, se puso inestable bajo
       `-n auto`. La causa: el fixture `agents` usa `EchoProvider` con la
       respuesta enlatada, o sea **instantánea**. Sin latencia no hay nada que
       paralelizar, y la comparación se convertía en una carrera entre dos
       cosas igual de rápidas, decidida por el ruido del planificador.

       Lo que significa es peor de lo que parece: **con el umbral de 2,0
       segundos este test nunca midió el paralelismo**. Con proveedores
       instantáneos habría pasado igual con una implementación en serie. Un
       test verde que no puede distinguir lo que dice comprobar.

    Por eso se monta aquí un escenario con LATENCIA de verdad: cuatro ejes a
    0,3 s son 1,2 s en serie y ~0,3 s en paralelo. La diferencia es del cuádruple
    y ya no la decide el ruido.
    """
    import time

    from swarm_helpers import GuionProvider, montar_registro

    from magi.modules.swarm.agents import BalthasarAgent
    from magi.modules.swarm.parallel import CRITIQUE_AXES

    retardo = 0.3
    reg = montar_registro(
        GuionProvider("g4f-deepseek", "deepseek",
                      por_defecto=("critica del eje", retardo)),
        GuionProvider("g4f-claude", "claude",
                      por_defecto=("critica del eje", retardo)),
        GuionProvider("g4f-qwen", "qwen",
                      por_defecto=("critica del eje", retardo)))
    await reg.probe_all()
    set_registry(reg)
    try:
        b = BalthasarAgent(Blackboard(), MagiBus())
        b.llm = FreeCloudLLM(reg)
        ejes = list(CRITIQUE_AXES)

        t0 = time.monotonic()
        for eje in ejes:
            await critique_multi_axis(b, task_id="t", proposal_text="x",
                                      round_num=1, axes=[eje])
        serie = time.monotonic() - t0

        t0 = time.monotonic()
        await critique_multi_axis(b, task_id="t", proposal_text="x",
                                  round_num=1)
        paralelo = time.monotonic() - t0
    finally:
        set_registry(None)

    assert paralelo < serie / 2, (
        f"los {len(ejes)} ejes en paralelo tardaron {paralelo:.2f}s y en serie "
        f"{serie:.2f}s, medidos en esta misma corrida con {retardo}s por eje")


def test_variants_are_labelled_for_comparison():
    ps = [Proposal("enfoque uno", 0, family="deepseek"),
          Proposal("enfoque dos", 1, family="deepseek")]
    out = format_variants_for_critic(ps)
    assert "Enfoque A" in out and "Enfoque B" in out
    assert "Compáralos" in out


def test_single_variant_is_passed_through_unchanged():
    out = format_variants_for_critic([Proposal("solo uno", 0)])
    assert out == "solo uno"


def test_unverified_variant_is_flagged_to_the_critic():
    p = Proposal("codigo", 0, verified=False, verification="SyntaxError línea 3")
    out = format_variants_for_critic([p, Proposal("otro", 1)])
    assert "NO PASA VERIFICACIÓN" in out


# ------------------------------------------- §2.6 memoria episódica

def test_memory_is_empty_at_first():
    assert EpisodicMemory("t").render_for_prompt() == ""


def test_memory_lists_what_already_failed():
    """Sin esto, la ronda 3 reproponía lo refutado en la ronda 1."""
    mem = EpisodicMemory("t")
    mem.record(round_num=1, approach="Usar polling cada 100 ms para detectar cambios",
               outcome="refutado", reason="consume CPU sin necesidad")
    mem.record(round_num=2, approach="Cachear el árbol completo en memoria",
               outcome="no_verifica", reason="MemoryError con repos grandes")
    out = mem.render_for_prompt()
    assert "YA INTENTADO" in out
    assert "polling" in out and "REFUTADO" in out
    assert "Cachear" in out and "NO_VERIFICA" in out
    assert "algo DISTINTO" in out


def test_approved_attempts_are_not_shown_as_failures():
    mem = EpisodicMemory("t")
    mem.record(round_num=1, approach="Enfoque que funcionó", outcome="aprobado")
    assert mem.render_for_prompt() == ""


def test_memory_survives_a_restart(tmp_path):
    store = TaskStore(tmp_path / "m.db")
    mem = EpisodicMemory("t", store=store)
    mem.record(round_num=1, approach="Reescribir el dynarec entero",
               outcome="refutado", reason="alcance desproporcionado")

    revived = EpisodicMemory("t", store=TaskStore(tmp_path / "m.db"))
    assert len(revived.failed_approaches()) == 1
    assert "dynarec" in revived.render_for_prompt()


def test_memory_is_bounded_for_small_context_windows():
    """Los proveedores gratuitos tienen ventanas cortas."""
    mem = EpisodicMemory("t")
    for i in range(30):
        mem.record(round_num=i, approach=f"Enfoque numero {i} con texto largo",
                   outcome="refutado", reason="x" * 500)
    out = mem.render_for_prompt()
    assert out.count("- Ronda") <= 6
    assert len(out) < 2500


def test_summary_skips_headers_and_fences():
    mem = EpisodicMemory("t")
    a = mem.record(round_num=1, outcome="refutado",
                   approach="# Título\n```\ncodigo\n```\n"
                            "Este es el enfoque real que se propuso aquí")
    assert "enfoque real" in a.approach
    assert "```" not in a.approach


# --------------------------------------- integración: todo conectado

@pytest.mark.asyncio
async def test_orchestrator_exposes_memory_per_task(tmp_path):
    from magi.modules.swarm.orchestrator import SwarmOrchestrator
    reg = ProviderRegistry()
    reg.register(EchoProvider("g4f-deepseek", "deepseek", canned="ok"))
    await reg.probe_all()
    set_registry(reg)
    try:
        swarm = SwarmOrchestrator(Blackboard(), MagiBus(),
                                  store=TaskStore(tmp_path / "o.db"))
        mem = swarm.memory_for("t1")
        assert mem is swarm.memory_for("t1"), "debe reutilizar la instancia"
        assert swarm.memory_for("t2") is not mem
    finally:
        set_registry(None)


def test_phase2_pieces_are_actually_wired():
    """
    Regla 2 del proyecto: un módulo con tests propios pero sin sitio de llamada
    es andamiaje. Ya me pasó con VerifiedRepair.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "magi/modules/swarm/orchestrator.py").read_text(encoding="utf-8")
    assert "generate_variants(" in src, "§2.4 variantes sin conectar"
    assert "critique_multi_axis(" in src, "§2.4 crítica multi-eje sin conectar"
    assert "ProposalVerifier(" in src, "§2.5 verificación sin conectar"
    assert "memory_for(" in src, "§2.6 memoria sin conectar"


def test_summary_never_leaks_code_fences():
    """
    Cuando la propuesta es casi todo código, ninguna línea de prosa calificaba
    y el respaldo devolvía el texto crudo con ```python dentro. La memoria
    acababa llena de fragmentos ilegibles.
    """
    mem = EpisodicMemory("t")
    a = mem.record(round_num=1, outcome="no_verifica",
                   approach="Propongo:\n```python\ndef f(:\n  pass\n```")
    assert "```" not in a.approach
    assert "[código]" in a.approach or "Propongo" in a.approach
