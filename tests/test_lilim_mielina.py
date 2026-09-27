"""
Pruebas de la Vaina de Mielina (v5.28.0: aceleración por NUBE de subagentes,
sin KoboldCpp — mandato «jamás modelos locales»).

Verifica:
1. Pre-auditoría estática determinista (ast de Python, 0 ms): intacta desde
   siempre, no es un modelo y nunca lo fue.
2. Clasificación de intenciones (0 ms).
3. Lubricación: con motor de subagentes (echo) devuelve conclusión real; sin
   motor degrada a None/su parte estática — NUNCA a una conclusión inventada.
"""
from __future__ import annotations

import pytest

from magi.modules.lilim.mielina import (
    clasificar_intencion_local,
    lubricar_arbitraje,
    lubricar_critica,
    lubricar_propuesta,
    lubricar_vision,
    pre_auditoria_estatica,
)


@pytest.fixture
def subagentes_echo():
    """Registro de subagentes con un proveedor eco determinista: prueba el
    CABLEADO, no la nube. Se desmonta al salir (los singletons no sobreviven
    a su test)."""
    from magi.core.providers import cloud
    from magi.core.providers.backends.echo import EchoProvider
    from magi.core.providers.registry import ProviderRegistry

    reg = ProviderRegistry()
    reg.register(EchoProvider(provider_id="eco", family="eco",
                              canned="ESQUELETO-ECO"), 1)
    cloud.set_subagent_registry(reg)
    yield reg
    cloud.set_subagent_registry(None)


@pytest.fixture
def subagentes_muertos():
    """Registro cuyo único proveedor falla siempre: el motor 'no está'."""
    from magi.core.providers import cloud
    from magi.core.providers.backends.echo import EchoProvider
    from magi.core.providers.registry import ProviderRegistry

    reg = ProviderRegistry()
    reg.register(EchoProvider(provider_id="muerto", family="muerta",
                              fail_times=99), 1)
    cloud.set_subagent_registry(reg)
    yield reg
    cloud.set_subagent_registry(None)


def test_pre_auditoria_estatica():
    """Detecta problemas sintácticos y estructurales obvios sin invocar modelos."""
    assert pre_auditoria_estatica("") == ["Código vacío o ausente"]

    codigo_pass = "def tarea_incompleta():\n    pass\n"
    defectos = pre_auditoria_estatica(codigo_pass)
    assert any("pass" in d for d in defectos)

    codigo_except = "try:\n    x = 1\nexcept:\n    x = 0\n"
    defectos_ex = pre_auditoria_estatica(codigo_except)
    assert any("except" in d for d in defectos_ex)

    codigo_sintaxis = "def rota(\n"
    defectos_sin = pre_auditoria_estatica(codigo_sintaxis)
    assert any("SyntaxError" in d for d in defectos_sin)

    codigo_sano = "def suma(a: int, b: int) -> int:\n    return a + b\n"
    assert pre_auditoria_estatica(codigo_sano) == []


# ---------------------------------------------------- LO QUE DE VERDAD LLEGA

#: Una propuesta de Melchior tal cual sale: prosa con bloque cercado. NUNCA
#: llega código desnudo, que es lo único que la primera versión sabía leer.
PROPUESTA_REAL = """Voy a cachear los planos que no cambian entre fotogramas.

```python
def compone(plano):
    pass
```

Predicción: composite baja >= 20 %."""

#: El emulador entero es C. Esto es lo que llega en una ronda de YabauseVita.
PROPUESTA_C = """Reduzco el trabajo del SH2 esclavo en espera pasiva.

```c
static void sh2_step(SH2_struct *ctx) {
    u32 op = fetch(ctx->pc);
    ctx->pc += 2;
    dispatch(ctx, op);
}
```"""


def test_no_inventa_un_syntaxerror_en_cada_propuesta_real():
    defectos = pre_auditoria_estatica(PROPUESTA_REAL)
    assert not any("SyntaxError" in d for d in defectos), (
        f"sigue inventando un error de sintaxis sobre prosa: {defectos}")
    assert any("pass" in d for d in defectos), (
        f"se saltó el defecto de verdad al filtrar: {defectos}")


def test_sobre_codigo_c_calla_en_vez_de_mentir():
    """C no es Python roto: es otro lenguaje. El `ast` de Python no opina."""
    assert pre_auditoria_estatica(PROPUESTA_C) == []
    c_desnudo = ("static void paso(SH2 *ctx) {\n"
                 "    ctx->pc += 2;\n"
                 "}\n")
    assert pre_auditoria_estatica(c_desnudo) == []
    assert pre_auditoria_estatica("#include <stdio.h>\nint main(){return 0;}") == []


def test_sigue_viendo_python_roto_sin_cercas():
    assert any("SyntaxError" in d for d in pre_auditoria_estatica("def rota(\n"))


def test_el_docstring_no_disfraza_una_funcion_vacia():
    con_doc = 'def tarea():\n    """Documentada."""\n    pass\n'
    assert any("pass" in d for d in pre_auditoria_estatica(con_doc))


def test_tambien_audita_funciones_async():
    assert any("pass" in d for d in pre_auditoria_estatica(
        "async def tarea():\n    pass\n"))


def test_varios_bloques_se_auditan_todos():
    texto = ("Primero:\n\n```python\ndef a():\n    pass\n```\n\n"
             "Y luego:\n\n```python\ntry:\n    x = 1\nexcept:\n    x = 0\n```\n")
    defectos = pre_auditoria_estatica(texto)
    assert any("pass" in d for d in defectos)
    assert any("except" in d for d in defectos)


def test_clasificar_intencion_local():
    """Enrutamiento determinista rápido."""
    assert clasificar_intencion_local("") == "vacio"
    assert clasificar_intencion_local("task.cancel") == "comando_directo"
    assert clasificar_intencion_local("/run tests") == "comando_directo"
    assert clasificar_intencion_local("controles de la ps_vita") == "memoria_epd"
    assert clasificar_intencion_local("desarrolla un emulador complejo de sh2") == "deliberacion_enjambre"


# ------------------------------------------------------- lubricación por nube

async def test_lubricar_propuesta_con_motor_eco(subagentes_echo):
    res = await lubricar_propuesta("haz un juego")
    assert res == "ESQUELETO-ECO"


async def test_lubricar_propuesta_sin_motor_degrada_a_none(subagentes_muertos):
    """El acelerador sin motor devuelve None; el enjambre sigue igual. Lo que
    NUNCA devuelve es una conclusión inventada."""
    assert await lubricar_propuesta("haz un juego") is None


async def test_lubricar_critica_sin_motor_conserva_la_parte_estatica(
        subagentes_muertos):
    crit = await lubricar_critica("def x(): pass")
    assert any("pass" in c for c in crit)


async def test_lubricar_critica_con_motor_sintetiza(subagentes_echo):
    crit = await lubricar_critica("def x(): pass")
    assert any("pass" in c for c in crit)          # la estática sigue
    assert any("ESQUELETO-ECO" in c for c in crit)  # y el motor añade


async def test_lubricar_arbitraje_sin_motor_es_none(subagentes_muertos):
    assert await lubricar_arbitraje("encargo", "prop", ["obj1"]) is None


async def test_lubricar_vision_sin_vision_disponible_degradan_honesta(
        subagentes_echo):
    """El eco no soporta visión: la respuesta del puente es SYSTEM_NO_VISION
    y lubricar_vision NO lo disfraza como análisis (motor_vision ausente)."""
    vis = await lubricar_vision(b"fake", "mira")
    assert vis.get("formato") == "bytes"
    assert "analisis_vlm" not in vis
