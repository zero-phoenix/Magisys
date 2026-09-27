"""
CANARIO DIARIO de proveedores — SOLO corre con MAGI_CANARIO=1 (el job
`canario` de ci.yml, schedule diario). En la suite normal se SALTA y lo dice:
«no probado» es un estado, no un verde.

Hipótesis que mide (todas sobre terceros que cambian solos):
  H1  El enjambre tiene ≥3 familias g4f resueltas y sanas.
  H2  Con clave Groq, una inferencia REAL de subagente responde (y responde
      Groq de verdad, no el respaldo).
  H3  La nube gratuita completa al menos una petición corta de verdad.

El 2026-08-13, cinco de seis familias «verificadas» el día 6 estaban rotas:
sin este canario, «la nube gratuita sigue viva» es fe, no un hecho medido.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.catalogo_real,
    pytest.mark.frontera,
    pytest.mark.timeout(300),
    pytest.mark.skipif(
        os.environ.get("MAGI_CANARIO") != "1",
        reason="canario de RED real: solo en el job diario de ci.yml "
               "(MAGI_CANARIO=1), nunca en la suite de cada push"),
]


async def test_h1_el_enjambre_tiene_familias_sanas():
    from magi.core.providers.backends import build_default_registry
    reg = await build_default_registry(probe=True)
    fams = reg.families_available()
    assert len(fams) >= 3, (
        f"el enjambre solo tiene {len(fams)} familias sanas ({fams}): "
        f"el catálogo necesita una pasada de scripts/barrer_proveedores.py")


async def test_h2_groq_responde_de_verdad_como_subagente():
    from magi.core.providers import cloud
    from magi.core.providers.backends import groq_backend
    from magi.core.providers.base import CompletionRequest, Message

    hay_clave = bool(os.environ.get("GROQ_API_KEY")
                     or groq_backend._clave_de_fichero())
    if not hay_clave:
        pytest.skip("sin clave Groq: NO PROBADO en vivo hoy "
                    "(los subagentes caen a g4f, ver H3)")

    reg = await cloud.get_subagent_registry()
    resp = await reg.complete(CompletionRequest(
        messages=[Message("system", "Responde solo con la palabra: VIVO."),
                  Message("user", "canario")],
        max_tokens=16, temperature=0.0, timeout_s=45.0,
        presupuesto_s=45.0, hedge=False, tag="canario"),
        prefer="groq-flash")
    assert resp.provider_id.startswith("groq-"), (
        f"con clave puesta, respondió {resp.provider_id} y no Groq: "
        f"la clave o el modelo están mal (¿402/401 enmascarado por failover?)")
    assert resp.content.strip(), "Groq respondió vacío"


async def test_h3_la_nube_gratuita_completa_de_verdad():
    from magi.core.providers import cloud
    from magi.core.providers.base import CompletionRequest, Message, ProviderError

    reg = await cloud.get_registry()
    try:
        resp = await reg.complete(CompletionRequest(
            messages=[Message("system", "Responde solo: VIVO."),
                      Message("user", "canario")],
            max_tokens=16, temperature=0.0, timeout_s=60.0,
            presupuesto_s=60.0, hedge=False, tag="canario"),
        )
    except ProviderError as e:
        pytest.fail(f"ninguna familia gratuita completó el canario: {e}")
    assert resp.content.strip(), (
        f"{resp.provider_id} respondió vacío — cuenta como caído")
