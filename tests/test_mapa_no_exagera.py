"""
Un indicador que exagera manda a arreglar lo que no está roto.

QUÉ PASABA
==========
`MAPA-INTERFAZ.md` declaraba **25 capacidades invisibles** — «trabajo que se
hace y ningún panel muestra»—, y ese documento se usa para decidir en qué
trabajar.

Medido el 13-sep-2026 sobre esos 25: **13 salían acompañados de un
`TERMINAL_OUT` o de un log de panel en el mismo sitio**. El usuario los veía.
La cuenta real era 12, y de esos doce casi todos son telemetría interna
(`agent.done`, `agent.thought`, `rpc.hello`, `system.started`…) que nadie
querría en un panel.

O sea: el indicador decía 25 donde había 12, y de los 12 solo uno —
`swarm.ronda`, cuyo propio comentario dice «aquí solo se informa al GUI del
coste acumulado»— es un cable que de verdad falta.

POR QUÉ ESTO ES UN FALLO Y NO UN DETALLE
========================================
Es la misma clase de error que un pendiente falso en un megaplan, y esta sesión
ya se encontró uno (A9, que llevaba corregido desde el 2-sep). Un número
inflado en un documento que se consulta para priorizar cuesta exactamente lo
que cuesta trabajar sobre una premisa falsa.

`capacidad invisible` tiene que significar «el usuario NO se entera», no «la UI
no nombra este topic».
"""

from __future__ import annotations

from pathlib import Path

from magi.modules.gui.mapa import mapa

RAIZ = Path(__file__).resolve().parents[1]


def test_lo_que_sale_con_un_canal_visible_no_cuenta_como_invisible():
    """
    La distinción, medida sobre el repositorio real.

    No se fija el número exacto —crecerá con el sistema— sino la propiedad: un
    topic que sale junto a un canal que la interfaz pinta NO puede estar en la
    lista de invisibles.
    """
    m = mapa()

    assert m.acompanados, (
        "ningún topic sale acompañado de un canal visible: o el escaneo dejó "
        "de funcionar, o el sistema cambió de raíz"
    )
    solapan = m.capacidades_invisibles & m.acompanados
    assert not solapan, f"estos se cuentan como invisibles y se ven por otro canal: {solapan}"


def test_los_canales_que_la_interfaz_pinta_siguen_siendo_esos():
    """
    Control del instrumento.

    Si `TERMINAL_OUT` dejara de pintarse en la interfaz, la lista de
    acompañados seguiría dándolos por vistos y el mapa mentiría en la otra
    dirección — que es peor, porque escondería trabajo pendiente.
    """
    from magi.modules.gui.mapa import _CANALES_VISIBLES

    socket = (RAIZ / "magi-gui/src/useMagiSocket.ts").read_text(encoding="utf-8")
    for canal in _CANALES_VISIBLES:
        assert canal in socket, f"el mapa da por visible «{canal}» y la interfaz ya no lo maneja"


def test_el_documento_publica_las_dos_cifras():
    """
    Quien lea el documento tiene que ver la distinción, no solo el total.

    Es documento generado: si esto falla, se regenera con
    `python -m magi.modules.gui.mapa > docs/MAPA-INTERFAZ.md`, nunca a mano.
    """
    doc = (RAIZ / "docs/MAPA-INTERFAZ.md").read_text(encoding="utf-8")
    assert "Capacidades invisibles" in doc
    assert (
        "Se ven por otro canal" in doc
    ), "el mapa del repositorio es de antes de la distinción: regenéralo"
