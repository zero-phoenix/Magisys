"""
Fase 2 (v6.0 §A3, §A7, §C7): velocidad sin nuevas llamadas.

De qué van estos tests
======================
La meta del plan es una petición como la del 16-ago («tetris exe portable»)
en <15 llamadas y <2,5 min. La Fase 1 puso el techo; esta Fase 2 afina:

- A3 fan-out por motor: `deep` explora más (su presupuesto lo permite),
  `fast` se queda frugal, y un rebuild nunca regenera el fan-out entero.
- A7 degradación por latencia: un candidato que ya demostró responder en
  2 s no puede secuestrar 24 s de espera. El registry corta por el techo
  dinámico antes de fallar y pasar al siguiente.
- C7 cortesía de tasa: `RateLimiterManager` existía y nadie lo usaba. Un
  bucket por candidato espacia las ráfagas de llamadas HTTP hacia los
  proveedores gratuitos sin bloquear nunca una llamada.
"""
import asyncio
import time

import pytest

from magi.core.providers.backends.echo import EchoProvider
from magi.core.providers.base import (
    BaseProvider,
    CompletionRequest,
    Delta,
    Message,
    ProviderError,
)
from magi.core.providers.registry import (
    ProviderRegistry,
    _techo_dinamico_s,
)
from magi.modules.swarm.orchestrator import _n_variantes

# ---------------------------------------------------------------- §A3 fan-out


def test_el_fan_out_es_de_dos_en_los_dos_motores():
    """
    Este test cambió de números el 2026-08-20, y el motivo es una medida.

    Antes: `fast` exploraba 3 enfoques en un build y `deep` hasta 4, con la
    idea de que más enfoques dan mejor resultado. La auditoría del encargo del
    ping pong dice otra cosa: **3 enfoques, 27.753 caracteres producidos,
    24,7 % entregado y ningún artefacto.** La calidad no salía de tener tres
    textos; se iba en escribirlos.

    Dos basta para que Balthasar tenga algo que contrastar, y la cuota que
    sobra se gasta en verificar y construir de verdad (D3), que es donde el
    usuario nota la diferencia.
    """
    assert _n_variantes("fast", "build", False) == 2
    assert _n_variantes("fast", "task", False) == 2
    assert _n_variantes("deep", "build", False) == 2
    assert _n_variantes("deep", "task", False) == 2


def test_un_rebuild_nunca_reabre_el_fan_out_entero():
    # El log del 16-ago: 6 ciclos regenerando las 3 variantes enteras.
    assert _n_variantes("deep", "build", True) == 1
    assert _n_variantes("fast", "task", True) == 1


def test_motor_desconocido_cae_al_perfil_frugal():
    assert _n_variantes("rapidisimo", "task", False) == 2


def test_ruta_desconocida_genera_una_variante():
    assert _n_variantes("fast", "otra_cosa", False) == 1


# ---------------------------------------------------------------- §A7 techo

class ProveedorDormilon(EchoProvider):
    """Responde tras `demo_s`; opcionalmente con una medida previa."""

    def __init__(self, provider_id, family, *, demo_s, medido_ms=None):
        super().__init__(provider_id, family)
        self._demo_s = demo_s
        self._medido_ms = medido_ms

    def mejor_latencia_ms(self):
        return self._medido_ms

    async def complete(self, req):
        await asyncio.sleep(self._demo_s)
        return await super().complete(req)


class ProveedorStreamEcho(BaseProvider):
    """Stream que tarda `demo_s` en emitir el primer token."""

    supports_stream = True

    def __init__(self, provider_id, family, *, demo_s, medido_ms=None):
        object.__init__(self)
        self.id = provider_id
        self.family = family
        self._demo_s = demo_s
        self._medido_ms = medido_ms

    def mejor_latencia_ms(self):
        return self._medido_ms

    async def available(self):
        return True

    async def stream(self, req):
        await asyncio.sleep(self._demo_s)
        yield Delta(text="hola", seq=0, provider_id=self.id)
        yield Delta(text="", seq=1, done=True, provider_id=self.id)


def test_el_techo_es_3x_la_mejor_medida_mas_margen_con_piso():
    assert _techo_dinamico_s(ProveedorDormilon("x", "gpt", demo_s=1,
                                               medido_ms=200), 15.0, False) == 6.0
    assert _techo_dinamico_s(ProveedorDormilon("x", "gpt", demo_s=1,
                                               medido_ms=8000), 15.0, False) == 15.0
    assert _techo_dinamico_s(ProveedorDormilon("x", "gpt", demo_s=1,
                                               medido_ms=2000), 60.0, False) == 11.0


def test_sin_medida_no_hay_techo_y_la_sonda_no_se_mutila():
    sin = ProveedorDormilon("x", "gpt", demo_s=1)
    assert _techo_dinamico_s(sin, 15.0, False) is None
    assert _techo_dinamico_s(sin, 15.0, True) is None, (
        "una sonda mide el tiempo REAL; recortarlo falsearía el dato")


@pytest.mark.asyncio
async def test_un_candidato_lento_con_medida_rapida_no_secuestra_la_cola():
    """
    Medido 200 ms y respuesta de 8 s: el techo (6 s) corta antes que el
    timeout del request (15 s). Sin el techo, esta llamada se llevaba 8 s
    de la cola entera cada vez.
    """
    # El umbral no es un numero de reloj: es lo que tarda ESTE proveedor.
    # Estaba escrito a mano como `< 8.0` y parecia una constante arbitraria de
    # las que tumbaron el CI en la v5.17.0 (R12). Atado a `demo_s`, dice lo que
    # de verdad comprueba: que el techo corto antes de que el lento acabara.
    demo_s = 8
    reg = ProviderRegistry()
    reg.register(ProveedorDormilon("lento", "gpt", demo_s=demo_s, medido_ms=200))
    req = CompletionRequest(messages=[Message(role="user", content="x")],
                            timeout_s=15.0)
    t0 = time.monotonic()
    with pytest.raises(ProviderError):
        await reg.complete(req)
    elapsed = time.monotonic() - t0
    assert elapsed < demo_s, (
        f"corto tarde: {elapsed:.1f}s, y el proveedor lento tardaba {demo_s}s. "
        f"El techo (6 s) tiene que cortar antes que eso")


@pytest.mark.asyncio
async def test_sin_medida_espera_lo_que_haga_falta():
    """
    Sin medición no hay techo: cortar sin dato sería adivinar. El request
    respeta su timeout y la respuesta llega.
    """
    reg = ProviderRegistry()
    reg.register(ProveedorDormilon("lento", "gpt", demo_s=0.4))
    req = CompletionRequest(messages=[Message(role="user", content="x")],
                            timeout_s=15.0)
    resp = await reg.complete(req)
    assert resp.content


@pytest.mark.asyncio
async def test_stream_corta_en_el_primer_token_si_falta_a_su_promesa():
    demo_s = 8
    reg = ProviderRegistry()
    reg.register(ProveedorStreamEcho("lentox", "gpt", demo_s=demo_s,
                                     medido_ms=200))
    req = CompletionRequest(messages=[Message(role="user", content="x")],
                            timeout_s=15.0, stream=True)
    t0 = time.monotonic()
    with pytest.raises(ProviderError):
        async for _ in reg.stream(req):
            pass
    elapsed = time.monotonic() - t0
    assert elapsed < demo_s, (
        f"el primer token tardo {elapsed:.1f}s y el proveedor entero tardaba "
        f"{demo_s}s: no corto en el primer token")


@pytest.mark.asyncio
async def test_stream_llega_cuando_responde_a_tiempo():
    reg = ProviderRegistry()
    reg.register(ProveedorStreamEcho("rapida", "gpt", demo_s=0.2, medido_ms=200))
    req = CompletionRequest(messages=[Message(role="user", content="x")],
                            timeout_s=15.0, stream=True)
    textos = [d.text async for d in reg.stream(req)]
    assert "".join(textos) == "hola"


# ---------------------------------------------------------------- §C7 tasa

def test_la_tasa_viene_del_catalogo():
    from magi.core.providers.backends.g4f_backend import (
        TASA_CAPACITY,
        TASA_RATE,
    )
    assert TASA_RATE == 2.0
    assert TASA_CAPACITY == 4


@pytest.mark.asyncio
async def test_el_bucket_espacia_la_rafaga_y_nunca_bloquea():
    """
    El bucket de TASA_CAPACITY tokens deja pasar el burst y luego espacia;
    la espera tiene un tope duro (2 s) y una llamada suelta inmediata pasa
    sin espera porque el bucket se recargó.
    """
    from magi.core.providers.backends.g4f_backend import (
        _MAX_ESPERA_TASA_S,
        _tasa_manager,
    )

    p = None  # no necesita el provider: _esperar_tasa vive en la clase
    from magi.core.providers.backends.g4f_backend import G4FProvider
    p = G4FProvider("gpt", candidates=[("nostalgia", "v1")])

    # El bucket deja pasar el burst y luego espacia: con 2 tokens dentro,
    # dos llamadas pasan al instante y la tercera espera a que se recargue.
    bucket = _tasa_manager.get_bucket("nostalgia", 2.0, 4)
    bucket.tokens = 2.0

    # El control estaba aqui al lado y no se usaba: la tercera llamada SI
    # espera. Comparar el burst contra ella mide el mecanismo; compararlo
    # contra `< 0.2` medía la maquina (R12).
    burst = []
    for _ in range(2):
        t0 = time.monotonic()
        await p._esperar_tasa("nostalgia")
        burst.append(time.monotonic() - t0)

    t0 = time.monotonic()
    await p._esperar_tasa("nostalgia")
    espera = time.monotonic() - t0

    assert max(burst) < espera / 2, (
        f"el burst no paso al instante: {max(burst):.3f}s frente a "
        f"{espera:.2f}s de la que si espera, en esta misma corrida")
    assert 0.3 < espera < _MAX_ESPERA_TASA_S + 0.2, (
        f"esperaba recarga (~0.6s), tardó {espera:.2f}s")
