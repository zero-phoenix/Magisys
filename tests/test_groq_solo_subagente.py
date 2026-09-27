"""
Groq es SOLO-SUBAGENTE (mandato del usuario, 2026-09-27) — y esto se PRUEBA.

El reparto de papeles que este test congela:
  · ENJAMBRE (Melchior, Balthasar, Casper, Naoko, Ritsuko): g4f puro, sin
    clave y SIN Groq. La diversidad epistemológica del debate vive ahí.
  · SUBAGENTES (solo lectura, aceleración, visión): registro propio donde
    Groq manda si hay clave, y g4f hace el respaldo si no la hay.

Si alguien registra Groq en el registro del enjambre «porque es más rápido»,
este test se pone en rojo: no es una optimización, es un cambio de política.
"""
from __future__ import annotations

import pytest

from magi.core.providers.backends import groq_backend


@pytest.fixture(autouse=True)
def clave_bajo_control(monkeypatch):
    monkeypatch.setattr(groq_backend, "_clave_de_fichero", lambda: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


@pytest.fixture
def registros_limpios():
    """Resetea los singletons de cloud al salir: un registro construido en un
    test con su loop de eventos no puede sobrevivir al test."""
    from magi.core.providers import cloud
    yield
    cloud.set_registry(None)
    cloud.set_subagent_registry(None)


async def test_el_registro_del_enjambre_no_tiene_groq():
    from magi.core.providers.backends import build_default_registry
    reg = await build_default_registry(probe=False)
    await reg.probe_all()
    groq = [f for f in reg.families_available() if f.startswith("groq-")]
    assert not groq, f"Groq coló en el ENJAMBRE: {groq} — es solo-subagente"


async def test_con_clave_groq_alimenta_los_subagentes(monkeypatch):
    from magi.core.providers.backends import build_subagent_registry
    monkeypatch.setenv("GROQ_API_KEY", "k-test")
    reg = await build_subagent_registry(probe=True)
    fams = reg.families_available()
    groq = [f for f in fams if f.startswith("groq-")]
    assert groq, "habiendo clave, los subagentes deben poder usar Groq"
    # Y el reparto de subagentes por mérito sin medidas pone Groq delante.
    primero = reg.healthy()[0]
    assert primero.family.startswith("groq-"), (
        f"con clave, el primer motor de subagentes debería ser Groq y es "
        f"{primero.family}")


async def test_sin_clave_los_subagentes_caen_a_g4f_y_siguen_vivos():
    from magi.core.providers.backends import build_subagent_registry
    reg = await build_subagent_registry(probe=True)
    fams = reg.families_available()
    assert fams, "sin clave, los subagentes deben tener familias g4f"
    assert not any(f.startswith("groq-") for f in fams)


async def test_el_enjambre_sigue_teniendo_diversidad_con_clave_puesta(
        monkeypatch):
    """La clave presente NO puede reordenar el enjambre: ni Groq arriba ni
    nada. El reparto del enjambre depende solo de las familias gratuitas."""
    from magi.core.providers.backends import build_default_registry
    monkeypatch.setenv("GROQ_API_KEY", "k-test")
    reg = await build_default_registry(probe=True)
    asignacion = reg.select_for_swarm()
    assert asignacion.diversity == "full", (
        f"el enjambre perdió diversidad: {asignacion.diversity} — {asignacion.note}")
    for familia in asignacion.families.values():
        assert not familia.startswith("groq-")


async def test_despachar_subagente_sin_motor_falla_honestamente(
        monkeypatch, registros_limpios):
    """El stub de la v5.27.x devolvía «verificado sin hallazgos críticos»
    SIN COMPROBAR NADA. Un subagente sin motor lo declara: nunca verde falso."""
    from magi.core.providers import cloud
    from magi.modules.swarm.subagentes import GestorSubagentes, despachar_subagente

    async def _sin_motor():
        raise RuntimeError("sin red en este test")

    monkeypatch.setattr(cloud, "get_subagent_registry", _sin_motor)
    res = await despachar_subagente(
        nodo="MELCHIOR", familia="gpt", mision="revisa el fichero X",
        gestor=GestorSubagentes())
    assert not res.exito
    assert "sin motor" in res.error
    assert res.conclusion == ""


async def test_despachar_subagente_con_motor_devuelve_conclusion_real(
        monkeypatch, registros_limpios):
    from magi.core.providers import cloud
    from magi.core.providers.backends.echo import EchoProvider
    from magi.core.providers.registry import ProviderRegistry
    from magi.modules.swarm.subagentes import GestorSubagentes, despachar_subagente

    reg = ProviderRegistry()
    reg.register(EchoProvider(provider_id="eco", family="eco",
                              canned="HALLAZGO: línea 3 divide por cero."), 1)
    cloud.set_subagent_registry(reg)
    res = await despachar_subagente(
        nodo="BALTHASAR", familia="gemini", mision="revisa la propuesta",
        gestor=GestorSubagentes())
    assert res.exito
    assert "HALLAZGO" in res.conclusion
    assert res.familia == "eco", "la familia del resultado es la REAL que respondió"
