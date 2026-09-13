"""
Aprobación con contexto (Plan MAGI 9.0 §7.4).

EL PROBLEMA
===========
v5.0.28 tenía un banner de aprobación que iba en la dirección correcta y le
faltaba justo lo que hace falta para decidir: QUÉ se va a ejecutar, qué
ficheros toca, y si los tests pasaron.

Y en la interfaz era peor de lo que parecía. El estado de aprobación se
deducía RASPANDO el terminal en busca de una frase:

    if (terminalOutput.includes("Esperando aprobación interactiva del usuario"))
        setPendingApproval(props.content)          // App.tsx:167-172

Es decir: el frontend cogía el texto del último mensaje de un agente y lo
llamaba "el cambio propuesto". No tenía los ficheros, no tenía el contenido
anterior, y por eso `DiffViewer` recibía `originalCode=""` y pintaba TODO como
añadido. No era un diff; era el texto nuevo con el fondo verde.

Pulsar "Aprobar" sobre eso es aprobar a ciegas con la apariencia de haber
revisado, que es peor que aprobar a ciegas sabiéndolo.

DE DÓNDE SALE EL "ANTES"
========================
Del journal de escrituras del §4.2, sin añadir nada. Ese journal ya copia el
contenido previo de cada fichero ANTES de tocarlo, porque hacía falta para
poder deshacer. Resulta que la misma copia responde a la otra pregunta: qué
había antes de este cambio.

Una pieza construida para reversibilidad que también resuelve la revisión no
es casualidad — es lo que pasa cuando el estado que necesitas ya está
guardado en algún sitio en vez de reconstruido a mano.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Tope por fichero al viajar por el websocket. Un .bin de 40 MB en un panel
#: de revisión no informa de nada y bloquea la interfaz mientras se pinta.
MAX_BYTES_PER_FILE = 200_000

#: Extensiones que no se intentan mostrar como texto. La lista es corta a
#: propósito: ante la duda se INTENTA leer y se decide por el contenido, que
#: es más fiable que fiarse del nombre.
BINARY_HINT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
               ".pdf", ".zip", ".gz", ".exe", ".dll", ".so", ".pyc",
               ".mp4", ".mkv", ".webm", ".mp3", ".wav", ".db", ".sqlite")


def _normaliza_saltos(s: str) -> str:
    """
    CRLF y CR sueltos -> LF.

    En Windows, `Path.write_text("a\\n")` deja 'a\\r\\n' en disco: el modo texto
    de Python traduce el salto al escribir. Aquí se lee en BINARIO para no
    corromper nada, así que el CRLF llega intacto — y el panel de revisión
    acababa mostrando como cambio lo que solo era el final de línea del
    sistema operativo. Con ficheros grandes eso es un diff entero en rojo por
    nada, y el usuario no puede ver el cambio de verdad.

    Se normaliza SOLO para revisar y comparar. Ni el journal ni la escritura
    tocan los bytes: deshacer sigue restaurando el fichero exacto.
    """
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _read_text(p: Path | str | None) -> tuple[str, str]:
    """
    Lee un fichero como texto. Devuelve (contenido, motivo_si_no_se_pudo).

    Nunca lanza: esto alimenta un panel de revisión, y que la revisión reviente
    porque un fichero es raro deja al usuario sin poder aprobar nada.
    """
    if p is None:
        return "", ""
    path = Path(p)
    if not path.exists():
        return "", "no existe"
    if path.is_dir():
        return "", "es un directorio"
    if path.suffix.lower() in BINARY_HINT:
        return "", f"binario ({path.suffix}): no se muestra como texto"
    try:
        datos = path.read_bytes()
    except OSError as e:
        return "", f"no se pudo leer: {e}"
    if b"\x00" in datos[:8192]:
        return "", "binario (contiene bytes nulos)"
    if len(datos) > MAX_BYTES_PER_FILE:
        recorte = _normaliza_saltos(
            datos[:MAX_BYTES_PER_FILE].decode("utf-8", "replace"))
        return (recorte + f"\n\n… [recortado: {len(datos):,} bytes en total] …",
                "")
    return _normaliza_saltos(datos.decode("utf-8", "replace")), ""


@dataclass
class FileChange:
    """Un fichero tocado, con su contenido antes y después."""
    path: str
    before: str = ""
    after: str = ""
    kind: str = "modificado"        # creado | modificado | borrado
    note: str = ""                  # por qué no se muestra, si aplica
    #: Si el journal conserva con qué revertirlo. Un fichero CREADO se revierte
    #: borrándolo y no necesita copia; uno modificado sí, y si la copia se
    #: perdió (por `prune`, por ejemplo) el cambio ya no se puede deshacer.
    #: Eso cambia lo que significa aprobar, así que se dice.
    revertible: bool = True

    def _counts(self) -> tuple[int, int]:
        """
        Líneas añadidas y quitadas DE VERDAD, por diferencia de secuencias.

        Estaban calculadas como `max(0, len(después) - len(antes))`, que es la
        diferencia de TAMAÑO y no el número de líneas que cambiaron. Reescribir
        un fichero de treinta líneas entero salía como:

            modificado  core.py  (+0 −0 líneas)

        O sea "sin cambios" en el resumen que lee quien aprueba desde el
        terminal — la misma clase de fallo (aprobar creyendo que has revisado)
        que este módulo se escribió para eliminar, escondida en su propio
        resumen. La interfaz sí tenía LCS real en `diff.ts`; este texto no lo
        usaba.
        """
        if self.note:
            return 0, 0
        import difflib
        antes = self.before.splitlines()
        despues = self.after.splitlines()
        anadidas = quitadas = 0
        for etiqueta, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, antes, despues, autojunk=False).get_opcodes():
            if etiqueta in ("replace", "delete"):
                quitadas += i2 - i1
            if etiqueta in ("replace", "insert"):
                anadidas += j2 - j1
        return anadidas, quitadas

    @property
    def added(self) -> int:
        return self._counts()[0]

    @property
    def removed(self) -> int:
        return self._counts()[1]

    def to_payload(self) -> dict[str, Any]:
        return {"path": self.path, "before": self.before, "after": self.after,
                "kind": self.kind, "note": self.note,
                "revertible": self.revertible}


@dataclass
class ApprovalRequest:
    """
    Todo lo que hace falta para decir sí o no con conocimiento de causa.
    """
    task_id: str
    summary: str = ""
    changes: list[FileChange] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    tests_ran: bool = False
    tests_passed: bool = False
    tests_detail: str = ""
    reversible: bool = True
    #: Motivo por el que no se pudo leer el journal, si pasó. Con esto puesto,
    #: "no toca ningún fichero" deja de ser una afirmación y pasa a ser un
    #: "no lo sé", que es lo único cierto.
    journal_error: str = ""

    @property
    def files_touched(self) -> int:
        return len(self.changes)

    def _manifiesto(self) -> dict[str, Any]:
        """
        A1: la procedencia del artefacto, resumida para la tarjeta.

        El packager escribe `<exe>.manifest.json` con el sha256 del ejecutable
        y el de CADA fuente. Eso se calculaba y se quedaba en disco: quien
        tenía que decir «sí» a un binario lo aprobaba sin ver de dónde salía.

        Se resume a propósito —ejecutable, hash, entrada, cuántas fuentes y
        dónde está el fichero entero—: el manifiesto de un proyecto mediano
        son cientos de líneas de hashes, y en una tarjeta de decisión solo
        sirve lo que se comprueba de un vistazo.

        Se lee del contenido que el journal ya guardó, no del disco: el
        fichero puede haberse movido, y reunir el contexto no puede reventar
        la decisión.
        """
        import json as _json

        for c in self.changes:
            if not c.path.endswith(".manifest.json"):
                continue
            try:
                datos = _json.loads(c.after or "")
            except Exception:
                continue
            if not isinstance(datos, dict) or "exe_sha256" not in datos:
                continue
            return {
                "exe": str(datos.get("exe", "")),
                "sha256": str(datos.get("exe_sha256", "")),
                "entry": str(datos.get("entry", "")),
                "fuentes": len(datos.get("fuentes") or {}),
                "ruta": c.path,
            }
        return {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "summary": self.summary,
            "changes": [c.to_payload() for c in self.changes],
            "commands": self.commands,
            # E3 — sin esto, A1 calculaba la procedencia y nadie la veía.
            "manifiesto": self._manifiesto(),
            "tests_ran": self.tests_ran,
            "tests_passed": self.tests_passed,
            "tests_detail": self.tests_detail,
            # Un journal ilegible no puede producir una promesa de
            # reversibilidad: lo único cierto entonces es que no se sabe.
            "reversible": self.reversible and not self.journal_error,
            "journal_error": self.journal_error,
            "files_touched": self.files_touched,
        }

    def render(self) -> str:
        """Resumen en texto, para el terminal y para quien no use la interfaz."""
        lineas = [f"APROBACIÓN PEDIDA — tarea {self.task_id}", ""]
        if self.summary:
            lineas += [self.summary.strip()[:600], ""]

        if self.journal_error:
            lineas.append(
                f"NO SE PUDO LEER EL JOURNAL ({self.journal_error}). No sé qué "
                f"ficheros toca esto ni puedo garantizar que se pueda "
                f"deshacer. Trátalo como irreversible.")
        elif not self.changes:
            lineas.append("No toca ningún fichero.")
        else:
            lineas.append(f"Ficheros afectados: {len(self.changes)}")
            for c in self.changes:
                detalle = c.note or f"+{c.added} −{c.removed} líneas"
                lineas.append(f"  {c.kind:<11s} {c.path}  ({detalle})")

        if self.commands:
            lineas += ["", "Órdenes que se ejecutarán:"]
            lineas += [f"  $ {c}" for c in self.commands]

        lineas.append("")
        if not self.tests_ran:
            lineas.append("TESTS: no se ejecutaron. No hay evidencia de que "
                          "esto no rompa nada.")
        elif self.tests_passed:
            lineas.append(f"TESTS: en verde. {self.tests_detail}".rstrip())
        else:
            lineas.append(f"TESTS: EN ROJO. {self.tests_detail}".rstrip())

        if self.journal_error:
            pass          # ya se avisó arriba; no repetir una garantía falsa
        elif self.reversible:
            lineas.append("Reversible: el journal guarda el estado previo, "
                          "así que aprobar no es definitivo (§4.2).")
        else:
            lineas.append("AVISO: sin copia previa en el journal. Esto NO se "
                          "puede deshacer solo.")
        return "\n".join(lineas)


def changes_from_journal(task_id: str, journal: Any,
                         errores: list[str] | None = None) -> list[FileChange]:
    """
    Reconstruye qué cambió una tarea a partir del journal de escrituras.

    Se queda con la copia MÁS ANTIGUA de cada fichero: si una tarea tocó el
    mismo fichero tres veces, el "antes" que le interesa al que revisa es el
    de antes de empezar, no el de la penúltima escritura. Coger la última
    mostraría un diff diminuto de un cambio grande.
    """
    try:
        entradas = [e for e in journal.all_entries()
                    if e.task_id == task_id and not e.undone]
    except Exception as e:
        logger.warning("[aprobación] no se pudo leer el journal: %s", e)
        if errores is not None:
            errores.append(str(e)[:120])
        return []

    primera: dict[str, Any] = {}
    for e in entradas:
        primera.setdefault(e.target, e)     # las entradas vienen en orden

    cambios: list[FileChange] = []
    for destino, entrada in primera.items():
        p = Path(destino)
        antes, motivo_antes = _read_text(entrada.backup)
        despues, motivo_despues = _read_text(p if p.exists() else None)

        if entrada.backup is None:
            kind = "creado"
            # Deshacer un fichero creado es borrarlo: no hace falta copia.
            revertible = True
        else:
            kind = "borrado" if not p.exists() else "modificado"
            # Aquí SÍ hace falta la copia, y puede haber desaparecido — el
            # journal tiene `prune(keep_days)`. Si no está, aprobar deja de
            # ser reversible y el usuario tiene derecho a saberlo ANTES.
            revertible = Path(entrada.backup).exists()

        cambios.append(FileChange(
            path=destino, before=antes, after=despues, kind=kind,
            note=motivo_despues or (motivo_antes if kind != "creado" else ""),
            revertible=revertible))
    return sorted(cambios, key=lambda c: c.path)


def build_approval_request(task_id: str, *, journal: Any = None,
                           summary: str = "", commands: list[str] | None = None,
                           tests_ran: bool = False, tests_passed: bool = False,
                           tests_detail: str = "") -> ApprovalRequest:
    """
    Reúne el contexto de una aprobación.

    Tolerante por diseño: si el journal falla o no hay nada registrado, sale
    una petición con menos información y un aviso, nunca una excepción. Una
    tarea que no se puede aprobar porque el panel de aprobación reventó es
    una tarea colgada.
    """
    fallos: list[str] = []
    cambios = changes_from_journal(task_id, journal, fallos) if journal else []
    return ApprovalRequest(
        task_id=task_id, summary=summary, changes=cambios,
        commands=list(commands or []),
        tests_ran=tests_ran, tests_passed=tests_passed,
        tests_detail=tests_detail,
        # Sin cambios no hay nada que deshacer; con cambios, la operación es
        # reversible solo si TODOS lo son. Uno solo sin copia basta para que
        # aprobar deje de ser una decisión que se pueda desandar.
        reversible=all(c.revertible for c in cambios),
        journal_error="; ".join(fallos),
    )
