"""
L5: la capa local, alcanzable desde la interfaz.

QUÉ PASABA
==========
Lilim existía desde la v5.25 y sólo era alcanzable como **herramienta del
enjambre**: para preguntarle algo había que abrir una tarea y esperar a que un
nodo decidiera usarla. Una respuesta de 0-3,5 ms detrás de una ronda de debate
completa.

Medido el 12-sep: cero coincidencias de `lilim` en `magi-gui/src/`, y cero en
`magi/core/kernel.py`. No había panel, ni pestaña, ni endpoint. Tres versiones
de trabajo —v5.25, v5.26 y la v13 entera— inalcanzables para quien usa el
programa.

QUÉ SE COMPRUEBA AQUÍ
=====================
Las tres piezas de la cadena, porque cualquiera que falte deja la capa tan
inalcanzable como estaba: el handler responde, el kernel lo registra, y la
interfaz lo llama y lo pinta.

Y una cosa más, que es la que hace útil a esta capa: la respuesta llega **con
su procedencia y con su medición**. Sin fuente, una respuesta local es
indistinguible de una inventada — y no inventar es justamente lo que la
distingue del puente de nube.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_el_handler_responde_con_medicion():
    """Lo que la interfaz necesita para decir «esto es local»."""
    from magi.modules.lilim import rpc_pregunta

    res = await rpc_pregunta({"pregunta": "controles de sega saturn"})

    assert res["ok"], res
    assert res["respuesta"], "respondió vacío"
    assert res["local"] is True
    assert isinstance(res["ms"], float)
    assert res["ms"] < 500, f"la capa local tardó {res['ms']} ms: eso ya no es local, es otra cosa"


@pytest.mark.asyncio
async def test_la_respuesta_trae_su_procedencia():
    """
    Sin fuente, una respuesta local no se distingue de una inventada.

    Es el invariante de Lilim, no un adorno: responde lo que SABE, con de dónde
    lo sabe, y cuando no lo sabe lo dice.
    """
    from magi.modules.lilim import rpc_pregunta

    res = await rpc_pregunta({"pregunta": "controles de sega saturn"})
    texto = (res.get("respuesta") or "").lower()

    assert (
        "memoria local" in texto or "local" in texto
    ), f"la respuesta no dice de dónde sale: {texto[:200]}"


@pytest.mark.asyncio
async def test_una_pregunta_vacia_no_revienta_ni_inventa():
    """Control: el panel manda lo que el usuario escriba, incluido nada."""
    from magi.modules.lilim import rpc_pregunta

    res = await rpc_pregunta({"pregunta": "   "})

    assert not res["ok"]
    assert "vacia" in (res.get("error") or "").lower()


def test_el_kernel_registra_el_endpoint():
    """
    Sin registro, el handler es código muerto.

    Esta sesión se encontró cuatro módulos escritos y sin conectar; el registro
    es exactamente el punto donde eso se decide.
    """
    kernel = (RAIZ / "magi/core/kernel.py").read_text(encoding="utf-8")
    assert '"lilim.pregunta"' in kernel, (
        "el kernel no registra lilim.pregunta: el handler existe y no lo llama " "nadie"
    )


def test_la_interfaz_lo_llama_y_lo_pinta():
    """
    La otra punta de la cadena.

    Mira la fuente porque los tests de componentes de esta interfaz son uno
    solo — mismo patrón que `test_cancel.py` con el informe de cancelación.
    Caza lo que tiene que cazar: que alguien quite el panel y el endpoint se
    quede sirviendo a nadie.
    """
    socket = (RAIZ / "magi-gui/src/useMagiSocket.ts").read_text(encoding="utf-8")
    app = (RAIZ / "magi-gui/src/App.tsx").read_text(encoding="utf-8")
    panel = (RAIZ / "magi-gui/src/components/LilimPanel.tsx").read_text(encoding="utf-8")

    assert "lilim.pregunta" in socket, "el hook no llama al endpoint"
    assert (
        "askLilim" in socket and "askLilim" in app
    ), "la llamada no sale del hook o App no la consume"
    assert '"Lilim"' in app, "no hay pestaña de Lilim en el cajón"
    assert "LilimPanel" in app, "la pestaña existe pero no monta el panel"
    assert (
        "ms" in panel and "local" in panel
    ), "el panel no enseña la medición ni dice que la respuesta es local"
