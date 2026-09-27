"""
Backends de inferencia.

RESTRICCIÓN DEL PROYECTO (§I.3 + mandatos del usuario de 2026-09-27):
  · **Solo IA de nube, jamás modelos locales** (el tripwire
    tests/test_nunca_modelos_locales.py impide que un KoboldCpp/Ollama vuelva).
  · El ENJAMBRE PRINCIPAL —Melchior, Balthasar, Casper, Naoko, Ritsuko— corre
    SOLO con las familias gratuitas de g4f, sin clave: es donde vive la
    diversidad epistemológica del debate.
  · Groq (la ÚNICA clave permitida, GROQ_API_KEY) alimenta EXCLUSIVAMENTE el
    registro de SUBAGENTES: trabajos de solo lectura, aceleración (mielina),
    síntesis de contexto y visión rápida. LPU: la menor latencia medible.
    Sin clave, los subagentes caen solos a g4f y nada deja de funcionar.

La diversidad del enjambre —que en v5.0.28 no existía— se consigue fijando un
proveedor por familia, no dejando el auto-router. Ver g4f_backend.py y
groq_backend.py.
"""
from .echo import EchoProvider
from .g4f_backend import (
    DEFAULT_SWARM_FAMILIES,
    FAMILY_SPECS,
    G4FProvider,
    build_swarm_providers,
)
from .groq_backend import GroqProvider, build_groq_providers

__all__ = [
    "EchoProvider", "G4FProvider", "GroqProvider", "FAMILY_SPECS",
    "DEFAULT_SWARM_FAMILIES", "build_swarm_providers", "build_groq_providers",
    "build_default_registry", "build_subagent_registry",
]

# Orden de preferencia entre familias.
#
# El orden anterior (deepseek 10, claude 15, qwen 20, ...) reflejaba qué
# familias razonan mejor EN TEORÍA. El problema es que `select_for_swarm`
# reparte los tres nodos por este orden, así que Melchior, Balthasar y Casper
# acababan en deepseek, claude y qwen: las tres familias que en la verificación
# empírica del 2026-08-06 no tienen ni un solo candidato vivo. El registro
# anunciaba "diversidad=full" con tres proveedores que no responden.
#
# Ahora manda lo verificado. Delante van las familias con al menos un candidato
# que contestó de verdad, ordenadas por latencia medida; detrás, las que hoy
# están agotadas —siguen registradas, porque pueden revivir, pero no se llevan
# los puestos del enjambre.
_PRIORITY = {
    # verificadas: responden por HTTP, sin navegador (ms medidos)
    "gpt": 10,          # Yqcloud 2000ms · WeWordle 2389ms · CopilotApp 1156ms
    "gemini": 15,       # Gemini/gemini-3.5-flash 3421ms
    "command": 20,      # CohereForAI command-a-03-2025 1078ms
    "llama": 25,        # Groq 922ms
    "hf": 30,           # HuggingSpace 890ms
    "perplexity": 35,   # Perplexity/auto 7921ms (respuesta pobre)
    # sin candidato vivo hoy: se registran, pero al final
    "deepseek": 60, "claude": 65, "qwen": 70, "glm": 75,
    # red de seguridad
    "auto": 99,
}


async def build_default_registry(*, probe: bool = True, families=None):
    """
    Registro del ENJAMBRE PRINCIPAL: SOLO g4f, sin clave y sin Groq.

    Groq queda fuera a propósito (mandato 2026-09-27): Melchior, Balthasar,
    Casper, Naoko y Ritsuko debaten con familias gratuitas DIVERSAS — el valor
    epistemológico del debate depende de sesgos distintos, no del motor más
    rápido. Los subagentes tienen su propio registro: build_subagent_registry.

    `auto` (el auto-router de g4f, que es lo único que usaba v5.0.28) queda
    registrado en última posición: sigue siendo la red de seguridad, pero deja
    de ser el camino principal.
    """
    from ..registry import ProviderRegistry

    reg = ProviderRegistry()
    for family in (families or FAMILY_SPECS.keys()):
        reg.register(G4FProvider(family=family), priority=_PRIORITY.get(family, 80))
    if probe:
        await reg.probe_all()
    return reg


async def build_subagent_registry(*, probe: bool = True):
    """
    Registro de SUBAGENTES: Groq primero, g4f de respaldo.

    Aquí SÍ quiere el motor más rápido disponible — un subagente es trabajo
    de solo lectura con turno único, y su latencia se paga dentro del turno
    del nodo que lo despachó. Con GROQ_API_KEY, Groq (LPU) sirve todo el
    trabajo subordinado; sin clave, las mismas familias g4f del enjambre
    hacen el papel y el sistema no cambia de comportamiento observable.

    Las prioridades g4f empiezan en 100: en el empate sin medidas, Groq va
    primero siempre que esté disponible.
    """
    from ..registry import ProviderRegistry

    reg = ProviderRegistry()
    for prio, groq in enumerate(build_groq_providers(), start=1):
        reg.register(groq, priority=prio)
    for family in FAMILY_SPECS.keys():
        reg.register(G4FProvider(family=family),
                     priority=100 + _PRIORITY.get(family, 80))
    if probe:
        await reg.probe_all()
    return reg
