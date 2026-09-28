"""
Nivel de autonomía (megaplan v5.29, Fase 0).

`manual`: todo lo aprueba el humano (comportamiento histórico).
`supervisada`: el enjambre pregunta antes de tocar la máquina (default).
`total`: las rondas cuyo plan solo escribe en workspace y no ejecuta
comandos riesgosos se aprueban solas, con auditoría en el bus.

Es estado de proceso a propósito: se consulta en cada ronda y se cambia
por RPC (`sys.config` con `{"autonomia": ...}`) o por la env var
`MAGI_AUTONOMIA` al arrancar.
"""
from __future__ import annotations

import os

NIVELES = ("manual", "supervisada", "total")
_DEFAULT = "supervisada"


def _normaliza(valor: str | None) -> str | None:
    if not valor:
        return None
    v = valor.strip().lower()
    # «auto», «autónomo» y «total» son la misma intención; acepta el CRUDO
    # que llegue de una GUI o de una env var sin corregir.
    if v in ("auto", "autonomo", "autónomo"):
        v = "total"
    return v if v in NIVELES else None


_actual: str = _normaliza(os.environ.get("MAGI_AUTONOMIA")) or _DEFAULT


def nivel() -> str:
    return _actual


def fijar(valor: str) -> bool:
    """Fija el nivel. Devuelve False si el valor no es válido (no lanza:
    un RPC de configuración no puede tumbar el kernel)."""
    global _actual
    v = _normaliza(valor)
    if v is None:
        return False
    _actual = v
    return True
