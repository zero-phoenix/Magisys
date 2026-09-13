"""
Cuando el enjambre se para a esperarte, la interfaz tiene que decirlo.

QUÉ PASABA, MEDIDO EL 13-SEP-2026
=================================
`docs/TRASPASO-ASTRA.md` §5.2 lo describía así: «la aprobación bloquea sin
decirlo. Cuando el enjambre queda en WAITING_USER_APPROVAL se para y no es
evidente que te espera a ti».

La causa era una línea que faltaba. En toda la interfaz:

    setAwaitingApproval(true)   -> 0 coincidencias
    setAwaitingApproval(false)  -> 2 (App.tsx:852 y :860, al decidir)

Nadie encendía el flag. Y de él colgaba todo el aviso:

  · el banner «PROPUESTA LISTA PARA EJECUCIÓN» se pinta si
    `pendingApproval || awaitingApproval`;
  · `pendingApproval` solo se rellena dentro de un `useEffect` cuya condición
    es `awaitingApproval && !pendingApproval`;
  · y ese mismo efecto es el que abre el cajón en la pestaña del diff.

Con el flag siempre en `false`, la cadena entera estaba muerta: el banner no
aparecía nunca y el cajón no se abría solo. El backend publicaba
`swarm.approval_required` con todo el contexto y la interfaz lo guardaba en el
store sin avisar a nadie.

Es el mismo patrón que F2-F5 en el backend —escrito, publicado en una versión
(v5.21.1, «aprobación visible») y sin conectar—, y la misma lección: un test que
mira la unidad no ve el cable que falta.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _socket() -> str:
    return (RAIZ / "magi-gui/src/useMagiSocket.ts").read_text(encoding="utf-8")


def test_recibir_la_peticion_de_aprobacion_enciende_el_aviso():
    """
    El evento y el aviso, en el mismo sitio.

    No basta con que exista `setAwaitingApproval`: tiene que llamarse donde
    llega `swarm.approval_required`. Se comprueba en el mismo bloque, no en el
    fichero entero, porque encenderlo en cualquier otro lado no avisaría de
    esto.
    """
    socket = _socket()
    bloque = re.search(
        r"topic === 'swarm\.approval_required'\)\s*\{(.*?)\}\s*else if", socket, re.S
    )
    assert bloque, "ya no existe el manejador de swarm.approval_required"

    cuerpo = bloque.group(1)
    assert "setApproval" in cuerpo, "el contexto de la decisión no se guarda"
    assert "setAwaitingApproval(true)" in cuerpo, (
        "la interfaz recibe la petición de aprobación y NO enciende el aviso: "
        "el banner y la apertura del cajón cuelgan de ese flag"
    )


def test_el_aviso_se_apaga_al_decidir():
    """
    Control: un aviso que no se apaga es peor que uno que no aparece.

    Si se encendiera sin apagarse, el banner se quedaría fijo después de
    aprobar y dejaría de significar nada.
    """
    app = (RAIZ / "magi-gui/src/App.tsx").read_text(encoding="utf-8")
    assert (
        app.count("setAwaitingApproval(false)") >= 2
    ), "no se apaga el aviso al aprobar y al rechazar"


def test_el_backend_sigue_publicando_el_evento_del_que_cuelga():
    """
    La otra punta. Si el topic se renombra en Python, esto lo dice aquí y no
    con un banner que dejó de salir sin que nadie sepa por qué.
    """
    orq = (RAIZ / "magi/modules/swarm/orchestrator.py").read_text(encoding="utf-8")
    assert "swarm.approval_required" in orq, "el orquestador ya no publica swarm.approval_required"
