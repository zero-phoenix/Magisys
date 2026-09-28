"""
Política de riesgo (megaplan v5.29, Fase 3).

Decide si una ronda que pide aprobación necesita HUMANO o puede
auto-aprobarse. Es pura sobre `state`: nada de bus ni de proveedores, para
que el test pueda congelar el criterio sin montar el enjambre.

El criterio, de lo más conservador a lo más libre:
- `manual`: SIEMPRE humano.
- `supervisada`: humano si hay comandos pendientes (escritura/ejecución);
  auto solo para rondas de análisis sin toques a la máquina.
- `total`: auto salvo que un comando pendiente salga del workspace o
  toque la red fuera del catálogo.
"""
from __future__ import annotations

import re

_REDA_PELIGROSA = re.compile(
    r"\b(curl|wget|ssh|scp|ftp|netsh|reg\s+add|Remove-Item.*-Recurse|"
    r"git\s+push|pip\s+install|npm\s+install(?:-global)?)\b", re.IGNORECASE)

#: Señales de que el comando sale del sandbox del workspace: ruta absoluta
#: que no empiece por el workspace, o salto de directorio.
_RUTA_ABSOLUTA = re.compile(r"[A-Za-z]:\\\\|/Users/|/home/|C:/", re.IGNORECASE)


def _comando_riesgoso(comando: str) -> bool:
    if _REDA_PELIGROSA.search(comando):
        return True
    if _RUTA_ABSOLUTA.search(comando) or ".." in comando:
        return True
    return False


def requiere_humano(state: dict, autonomia: str) -> bool:
    """True si la aprobación de esta ronda NO puede resolverse sola."""
    if autonomia == "manual":
        return True
    comandos = [str(c) for c in (state.get("pending_commands") or [])]
    if autonomia == "supervisada":
        # Sin comandos que toquen la máquina, la ronda es análisis: auto.
        return bool(comandos)
    # autonomia == "total": solo lo que sale del sandbox pide humano.
    return any(_comando_riesgoso(c) for c in comandos)
