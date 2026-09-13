"""
Una cita inventada es peor que una objeción fabricada: suena convincente.

QUÉ PASÓ
========
Ronda del 8-sep-2026, repositorio del emulador. Balthasar objetó y Melchior se
defendió diciendo que las variables estaban definidas en `vita_gpu.h`, líneas
42-45.

**Ese fichero no existe.**

Está escrito en `docs/TRASPASO-ASTRA.md` §5.1, junto a la otra mitad —la
escritura que borra— y con el diagnóstico: es el mismo patrón que cuando
inventó «53,3 FPS» sin ejecutar nada. Y el propio traspaso señala por qué esta
mitad es la peligrosa: «una réplica que capitula es inútil; una que se defiende
con citas inventadas es peligrosa, porque suena convincente».

Un árbitro que lee «está en vita_gpu.h:42-45» no tiene forma de saber que no
existe, salvo que alguien lo compruebe. Comprobarlo cuesta un `os.path.exists`.

QUÉ NO HACE
===========
No juzga si la cita SOSTIENE lo que dice sostener —eso es leer el código, y es
el trabajo de Balthasar—. Solo comprueba lo que se puede comprobar sin
interpretar: que el fichero citado exista. Es el mínimo, y es justo lo que
faltaba.

Y no rechaza nada por su cuenta: devuelve la lista para que viaje al arbitraje.
Quien decide sigue siendo Casper, con un dato más y verificado.
"""

from __future__ import annotations

import pytest

from magi.modules.swarm.citas import citas_rotas


def _arbol(tmp_path):
    """Un trozo del emulador: existe vidgpu.c, no existe vita_gpu.h."""
    (tmp_path / "src" / "vita").mkdir(parents=True)
    (tmp_path / "src" / "vita" / "vidgpu.c").write_text(
        "void VIDGPUVdp2LogTiming(void) {}\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("emulador\n", encoding="utf-8")
    return tmp_path


def test_el_caso_real_del_8_sep(tmp_path):
    """La defensa de Melchior, literal."""
    raiz = _arbol(tmp_path)
    texto = (
        "La objeción no se sostiene: las variables están definidas en "
        "`vita_gpu.h`, líneas 42-45, y el bucle las lee de ahí."
    )

    rotas = citas_rotas(texto, raiz)

    assert rotas, "no detectó la cita a un fichero que no existe"
    assert any("vita_gpu.h" in c for c in rotas), rotas


def test_una_cita_a_un_fichero_que_existe_no_se_marca(tmp_path):
    """
    Control, en la misma corrida.

    Sin esto, la guarda pasaría igual marcándolo TODO como roto — y entonces el
    aviso no distinguiría nada, que es como no tenerlo.
    """
    raiz = _arbol(tmp_path)
    texto = "El contador se imprime en `src/vita/vidgpu.c:1`, ahí está la firma."

    assert citas_rotas(texto, raiz) == []


def test_encuentra_el_fichero_aunque_la_ruta_no_sea_exacta(tmp_path):
    """
    Las citas de un modelo casi nunca traen la ruta completa.

    Si se exigiera la ruta exacta, `vidgpu.c:417` se marcaría como inventada
    estando el fichero en `src/vita/`. Un aviso con falsos positivos se ignora
    entero, y con él se iría el verdadero.
    """
    raiz = _arbol(tmp_path)
    assert citas_rotas("mira vidgpu.c:417 y lo verás", raiz) == []


def test_un_texto_sin_citas_no_inventa_avisos(tmp_path):
    raiz = _arbol(tmp_path)
    texto = "La propuesta compila y el resultado es 3.7 % más rápido."
    assert citas_rotas(texto, raiz) == []


def test_varias_formas_de_citar(tmp_path):
    """`fichero:linea`, «fichero, líneas N-M» y el nombre entre comillas."""
    raiz = _arbol(tmp_path)
    texto = (
        "uno: inventado_a.h:10\n"
        "dos: `inventado_b.c`, líneas 42-45\n"
        "tres: inventado_c.cpp, linea 7"
    )

    rotas = citas_rotas(texto, raiz)

    for esperado in ("inventado_a.h", "inventado_b.c", "inventado_c.cpp"):
        assert any(esperado in c for c in rotas), f"no vio {esperado}: {rotas}"


def test_no_confunde_una_version_ni_una_hora_con_una_cita(tmp_path):
    """
    `ruff==0.16.5` y `12:30` no son ficheros.

    El regex tiene que pedir una extensión de fichero de verdad; si no, cada
    número con dos puntos se convierte en una cita rota y el aviso muere de
    ruido en una semana.
    """
    raiz = _arbol(tmp_path)
    texto = "a las 12:30 con ruff==0.16.5 y python 3.10:2 avisos"
    assert citas_rotas(texto, raiz) == []
@pytest.mark.asyncio
async def test_el_aviso_llega_al_prompt_de_casper(tmp_path, monkeypatch):
    """
    El cableado, no la unidad.

    Un detector de citas que nadie consulta no habria evitado nada el 8-sep, y
    esta sesion acaba de encontrarse cuatro modulos escritos y sin conectar.
    Asi que esto no llama al detector: llama a `CasperAgent.arbitrate` con
    proveedores de guion y lee el prompt que le llega al modelo.
    """
    from swarm_helpers import (
        FAM_BALTHASAR,
        FAM_CASPER,
        FAM_MELCHIOR,
        GuionProvider,
        montar_registro,
    )

    from magi.core import paths as _paths
    from magi.core.blackboard import Blackboard
    from magi.core.bus import MagiBus
    from magi.core.providers.cloud import set_registry
    from magi.modules.swarm.agents import CasperAgent

    _arbol(tmp_path)
    monkeypatch.setenv("MAGI_WORKSPACE", str(tmp_path))
    _paths.workspace_dir.cache_clear()

    casper_prov = GuionProvider(
        f"g4f-{FAM_CASPER}", FAM_CASPER,
        por_defecto=("Sintesis. DECISION: APROBADA", 0.0))
    reg = montar_registro(
        GuionProvider(f"g4f-{FAM_MELCHIOR}", FAM_MELCHIOR),
        GuionProvider(f"g4f-{FAM_BALTHASAR}", FAM_BALTHASAR),
        casper_prov)
    await reg.probe_all()
    set_registry(reg)
    try:
        casper = CasperAgent(Blackboard(), MagiBus())
        await casper.arbitrate(
            task_id="t-citas",
            proposal={"content": "esta definido en `vita_gpu.h`, lineas 42-45"},
            critique={"content": "no encuentro ese fichero"},
            round_num=1, publicar=False)
    finally:
        set_registry(None)

    prompts = "\n".join(casper_prov.vistos)
    assert prompts, "Casper no llego a recibir ningun prompt"
    assert "vita_gpu.h" in prompts, (
        "el prompt de Casper no menciona la cita rota")
    assert "NO existen" in prompts, (
        "la cita aparece, pero sin decir que el fichero no existe")
