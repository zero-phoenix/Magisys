"""
E3: el manifiesto del artefacto, en la tarjeta donde se decide.

QUÉ FALTABA
===========
La v11 A1 hizo que el packager escribiera `<exe>.manifest.json` junto al
binario: sha256 del ejecutable, sha256 de cada fuente y el punto de entrada. Es
la procedencia del artefacto — sin ella, un binario no se puede auditar ni
regenerar.

Pero el payload de `swarm.approval_required` no lo llevaba. Medido el 12-sep,
sus campos eran: `task_id, summary, changes, commands, tests_ran, tests_passed,
tests_detail, reversible, journal_error, files_touched`. Ninguno es el
manifiesto.

O sea: el sistema calculaba la procedencia y se la guardaba. Quien tiene que
decir «sí» a un ejecutable lo aprobaba sin ver de qué fuentes salió ni con qué
hash — que es justo lo que A1 venía a resolver.

POR QUÉ UN RESUMEN Y NO EL FICHERO ENTERO
=========================================
El manifiesto de un proyecto mediano lleva el sha256 de cada `.py`: cientos de
líneas. En la tarjeta importa lo que se puede comprobar de un vistazo —qué
ejecutable, con qué hash, desde qué entrada y cuántas fuentes— y la ruta, para
quien quiera abrirlo entero.
"""

from __future__ import annotations

import json

from magi.core.approval import ApprovalRequest, FileChange


def _manifiesto(exe: str = "dist/juego.exe") -> str:
    return json.dumps(
        {
            "exe": exe,
            "exe_sha256": "a" * 64,
            "entry": "main.py",
            "fuentes": {"main.py": "b" * 64, "motor.py": "c" * 64},
        }
    )


def test_el_manifiesto_viaja_en_el_payload():
    """Lo que A1 calcula tiene que llegar a quien decide."""
    req = ApprovalRequest(
        task_id="t-1",
        changes=[
            FileChange(
                path="dist/juego.exe.manifest.json", before="", after=_manifiesto(), kind="create"
            )
        ],
    )

    payload = req.to_payload()

    man = payload.get("manifiesto")
    assert man, f"el payload no lleva manifiesto: {sorted(payload)}"
    assert man["sha256"].startswith("aaaa"), man
    assert man["entry"] == "main.py"
    assert man["fuentes"] == 2, "no cuenta las fuentes selladas"
    assert "juego.exe" in man["exe"]


def test_sin_artefacto_no_hay_manifiesto_y_no_se_inventa():
    """
    Control: la mayoría de tareas no producen un .exe.

    Sin esto, la guarda pasaría igual devolviendo siempre un manifiesto vacío
    con forma de manifiesto, y la tarjeta pintaría una sección hueca en cada
    aprobación.
    """
    req = ApprovalRequest(
        task_id="t-2",
        changes=[FileChange(path="magi/core/kernel.py", before="a", after="b", kind="write")],
    )

    assert not req.to_payload().get("manifiesto")


def test_un_manifiesto_ilegible_no_tumba_la_aprobacion():
    """
    Reunir el contexto no puede reventar la decisión.

    Es el mismo criterio que ya tiene `build_approval_request` con el journal:
    una tarea que se queda colgada porque el panel de revisión falló es peor
    que una revisión incompleta.
    """
    req = ApprovalRequest(
        task_id="t-3",
        changes=[
            FileChange(
                path="dist/x.exe.manifest.json", before="", after="{esto no es json", kind="create"
            )
        ],
    )

    payload = req.to_payload()  # no debe lanzar
    assert not payload.get("manifiesto")
    assert payload["task_id"] == "t-3"
def test_la_tarjeta_de_aprobacion_lo_pinta():
    """
    La otra mitad de E3: que el dato llegue no sirve si nadie lo enseña.

    Este proyecto acaba de encontrarse cuatro modulos escritos y sin conectar,
    y una tarjeta —CancelReportCard— que existia sin que nadie la montara. Asi
    que el backend y la interfaz entran en el mismo commit, y esto lo vigila.

    Mira la fuente porque los tests de componentes de esta interfaz son uno
    solo: es el mismo patron que usa `test_cancel.py` para el informe de
    cancelacion. No es ideal, pero caza lo que tiene que cazar — que alguien
    quite el bloque y el backend siga mandando un dato que nadie ve.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    viewer = (raiz / "magi-gui/src/DiffViewer.tsx").read_text(encoding="utf-8")
    tipos = (raiz / "magi-gui/src/lib/approval.ts").read_text(encoding="utf-8")

    assert "manifiesto" in tipos, "el contrato de TypeScript no lo declara"
    assert "ManifiestoArtefacto" in tipos

    assert "manifiesto" in viewer, (
        "la tarjeta de aprobacion no menciona el manifiesto: el backend lo "
        "manda y nadie lo pinta")
    assert "sha256" in viewer, "se pinta el manifiesto sin el hash del binario"
