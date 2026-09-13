"""
Que el fichero citado exista. Eso es todo, y eso es lo que faltaba.

POR QUÉ EXISTE
==============
Ronda del 8-sep-2026. Balthasar objetó, Melchior se defendió diciendo que las
variables estaban en `vita_gpu.h`, líneas 42-45, y **ese fichero no existe**.
El árbitro no tenía forma de saberlo.

`docs/TRASPASO-ASTRA.md` §5.1 lo puso junto a la escritura que borra, y con el
diagnóstico de por qué esta mitad es la peor: «una réplica que capitula es
inútil; una que se defiende con citas inventadas es peligrosa, porque suena
convincente». Es el mismo patrón que los «53,3 FPS» que nadie midió.

QUÉ COMPRUEBA, Y QUÉ NO
=======================
Solo lo que se puede comprobar sin interpretar: que el fichero citado exista.
No mira si la cita SOSTIENE lo que dice sostener —eso es leer el código, y es
el trabajo de Balthasar—. Comprobar la existencia cuesta un `exists()`, y es
exactamente el paso que faltaba entre «suena convincente» y «es verdad».

Tampoco rechaza nada por su cuenta: devuelve la lista para que viaje al
arbitraje. Quien decide sigue siendo Casper, con un dato más y verificado.

LOS FALSOS POSITIVOS IMPORTAN MÁS QUE LOS FALSOS NEGATIVOS
==========================================================
Un aviso que se equivoca se ignora entero, y con él se va el verdadero. Por eso:

  · la extensión tiene que ser de fichero de verdad, o `12:30` y `ruff==0.16.5`
    se convertirían en citas rotas;
  · basta con que el NOMBRE aparezca en el árbol, sin exigir la ruta completa,
    porque las citas de un modelo casi nunca la traen;
  · y el árbol se recorre una vez y con tope, que esto se llama dentro de una
    ronda y no puede costar más que el arbitraje que informa.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["citas_rotas"]

#: Extensiones que se aceptan como fichero citado. Acotadas a propósito: sin
#: esta lista, cualquier número con dos puntos sería una cita.
_EXT = (
    "c|h|cc|cpp|hpp|cxx|inc|s|asm|py|pyi|ts|tsx|js|jsx|rs|go|java|kt|cs|"
    "rb|php|swift|m|mm|lua|sh|bat|ps1|cmake|mk|make|json|yml|yaml|toml|"
    "ini|cfg|conf|spec|md|rst|txt|sql|proto|vert|frag|glsl"
)

#: `fichero.h:42`, con o sin ruta delante.
_CON_DOS_PUNTOS = re.compile(rf"(?<![\w/\\.])([\w./\\-]+\.(?:{_EXT}))\s*:\s*\d+", re.IGNORECASE)

#: «`fichero.h`, líneas 42-45» — la forma en la que se citó el 8-sep.
_CON_LINEAS = re.compile(
    rf"[`\"']?([\w./\\-]+\.(?:{_EXT}))[`\"']?\s*,?\s*" rf"(?:en\s+)?l[ií]neas?\s*\d+", re.IGNORECASE
)

#: Tope de ficheros que se indexan al buscar por nombre. Un repositorio de
#: emulador anda por los miles; pasado esto, el coste deja de compensar y se
#: prefiere no avisar a retrasar la ronda.
_TOPE_FICHEROS = 20_000

_IGNORADOS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    "_attic",
}


def _nombres_del_arbol(raiz: Path) -> set[str]:
    nombres: set[str] = set()
    for i, p in enumerate(raiz.rglob("*")):
        if i >= _TOPE_FICHEROS:
            break
        if any(parte in _IGNORADOS for parte in p.parts):
            continue
        if p.is_file():
            nombres.add(p.name.lower())
    return nombres


def citas_rotas(texto: str, raiz: Path | str) -> list[str]:
    """
    Ficheros citados en `texto` que no existen bajo `raiz`.

    Devuelve los nombres tal y como se citaron, sin repetir y en el orden en
    que aparecen: así el aviso se lee igual que el texto que lo provocó.
    """
    if not texto:
        return []
    raiz = Path(raiz)
    if not raiz.is_dir():
        return []

    citados: list[str] = []
    for patron in (_CON_DOS_PUNTOS, _CON_LINEAS):
        for m in patron.finditer(texto):
            cita = m.group(1)
            if cita not in citados:
                citados.append(cita)
    if not citados:
        return []

    nombres = _nombres_del_arbol(raiz)
    rotas = []
    for cita in citados:
        ruta = raiz / cita.replace("\\", "/")
        if ruta.exists():
            continue
        if Path(cita).name.lower() in nombres:
            continue
        rotas.append(cita)
    return rotas
