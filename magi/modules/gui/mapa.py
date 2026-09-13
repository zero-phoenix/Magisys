"""
El mapa de la interfaz: qué habla con qué, y qué no habla con nada.

EL PROBLEMA
===========
La regla 3 del README dice: «cada capacidad del backend tiene que poder
invocarse desde la interfaz». Era una norma, y el historial demuestra lo que
pasa con las normas: `eval.run` y `naoko.self_improve` tenían motor completo y
ningún botón; `MetricsCollector` publicaba métricas que ningún panel pintaba.

`test_sin_huerfanos` puso trinquete al lado del código (símbolos que nadie
llama). Falta el otro lado: **el cable entre el backend y la pantalla**.

La interfaz y el núcleo no se llaman por funciones — hablan por *topics* sobre
un bus (`useMagiSocket.ts` ↔ `magi/core/bus.py`). Eso hace el mapa
comprobable sin arrancar la aplicación:

  - un topic que la UI **escucha** y nadie publica  → panel muerto: un hueco
    en pantalla que no se llenará nunca
  - un topic que el backend **publica** y la UI ignora → capacidad invisible:
    trabajo que se hace y nadie ve

Son las dos formas del «punto en blanco que no funciona», y las dos son
detectables leyendo los dos lados.

QUÉ NO HACE
===========
No arranca la aplicación ni pulsa botones. Esto mapea el **cableado**; que un
botón cableado haga además lo correcto es una campaña de QA aparte, y decir lo
contrario sería exactamente el tipo de afirmación que R9 vino a prohibir.

No exige cero huérfanos: mismo criterio que `test_sin_huerfanos`. Publica la
cifra y impide que suba.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: La API del modulo es esta y solo esta. Lo demas es COMO se
#: calcula, y exponerlo lo convertia en cinco definiciones publicas
#: que nadie llamaba — justo lo que el trinquete de huerfanos vigila.
__all__ = ["mapa", "Mapa"]

#: Un topic es `familia.accion`, en minúsculas y con puntos. El formato lo
#: fija el bus, no este módulo: si algún día cambia, esto deja de encontrar
#: nada y el test se pone rojo — que es preferible a encontrar de menos en
#: silencio.
_TOPIC = r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+"

_EN_UI = re.compile(r"""["'`](""" + _TOPIC + r""")["'`]""")

#: Eventos que el backend EMITE (backend → UI).
_PUBLICA = re.compile(
    r"""(?:publish|emit|append_event|publicar)\s*\(\s*["'](""" + _TOPIC + r""")["']""")
_COMPARA = re.compile(
    r"""topic\s*(?:==|!=)\s*["'](""" + _TOPIC + r""")["']""")
_LITERAL_TOPIC = re.compile(
    r"""topic\s*=\s*["'](""" + _TOPIC + r""")["']""")

#: Comandos que el backend ATIENDE (UI → backend). Este patrón faltaba en la
#: primera versión y el mapa mentía: llamaba «panel muerto» a `task.archive`,
#: que tiene handler en `kernel.py:72`. Un mapa que mezcla las dos direcciones
#: es peor que no tener mapa — es la clase de documento que fue cierto una vez.
_ATIENDE = re.compile(
    r"""register_handler\s*\(\s*["'](""" + _TOPIC + r""")["']""")

#: Prefijos que NO son topics del bus aunque tengan la forma: rutas de
#: fichero, paquetes npm, claves de i18n. Sin esto el mapa se llena de ruido
#: y deja de leerse, que es como muere una auditoría.
_RUIDO = (
    "magi.gui", "magi.core", "magi.modules",       # rutas de import
    "react.", "vite.", "node.", "window.", "document.",
    "process.env", "import.meta",
)


#: Sufijos que delatan que la cadena no es un topic del bus aunque lo parezca:
#: `.current` es una ref de React, y las claves de `localStorage` se cuelan
#: porque son cadenas con punto. Se filtran por patrón y no por nombre: una
#: lista de excepciones a mano envejece igual que un documento a mano.
_SUFIJOS_NO_TOPIC = (".current", ".value", ".length")


def _es_ruido(t: str) -> bool:
    return (t.startswith(_RUIDO)
            or t.endswith(_SUFIJOS_NO_TOPIC)
            or t.endswith((".ts", ".tsx", ".js", ".py", ".json", ".css",
                           ".md", ".svg", ".png", ".html"))
            or t.count(".") > 3)


def _raiz_repo(inicio: str | Path | None = None) -> Path | None:
    base = Path(inicio or Path.cwd()).resolve()
    for c in (base, *base.parents):
        if (c / "magi").is_dir() and (c / "magi-gui").is_dir():
            return c
    return None


def _recolectar(ficheros, *patrones) -> set[str]:
    fuera: set[str] = set()
    for p in ficheros:
        try:
            texto = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat in patrones:
            fuera |= {m for m in pat.findall(texto) if not _es_ruido(m)}
    return fuera


#: Claves de almacenamiento del navegador. Tienen forma de topic y no lo son.
#: Se detectan por su USO (`getItem`/`setItem`) y no por su nombre: así el
#: filtro sigue valiendo cuando alguien añada la siguiente.
_ALMACEN = re.compile(
    r"""(?:get|set|remove)Item\s*\(\s*["'`](""" + _TOPIC + r""")["'`]""")


def _topics_interfaz(raiz: Path) -> set[str]:
    src = raiz / "magi-gui" / "src"
    if not src.is_dir():
        return set()
    ficheros = [p for p in src.rglob("*") if p.suffix in (".ts", ".tsx")]
    return _recolectar(ficheros, _EN_UI) - _recolectar(ficheros, _ALMACEN)


def _py(raiz: Path):
    return [p for p in (raiz / "magi").rglob("*.py")
            if "python-embed" not in str(p) and "_attic" not in str(p)]


def _topics_emitidos(raiz: Path) -> set[str]:
    return _recolectar(_py(raiz), _PUBLICA, _COMPARA, _LITERAL_TOPIC)


def _topics_atendidos(raiz: Path) -> set[str]:
    return _recolectar(_py(raiz), _ATIENDE)


@dataclass
class Mapa:
    interfaz: set[str] = field(default_factory=set)
    emitidos: set[str] = field(default_factory=set)
    atendidos: set[str] = field(default_factory=set)
    #: Topics que la UI no nombra pero que salen JUNTO a un canal visible.
    #: Sin esta distinción, el mapa contaba como invisible todo lo que la UI
    #: no nombrara por su nombre — y el usuario los veía igual.
    acompanados: set[str] = field(default_factory=set)

    @property
    def backend(self) -> set[str]:
        return self.emitidos | self.atendidos

    @property
    def conectados(self) -> set[str]:
        return self.interfaz & self.backend

    @property
    def paneles_muertos(self) -> set[str]:
        """La UI nombra el topic y el backend no lo emite ni lo atiende: no hay
        nadie al otro lado del cable, en ninguna de las dos direcciones."""
        return self.interfaz - self.backend

    @property
    def capacidades_invisibles(self) -> set[str]:
        """
        Lo que el backend hace y el usuario NO ve por ningún canal.

        Antes era `backend - interfaz` a secas: contaba como invisible todo
        topic que la UI no nombrara. Medido el 13-sep-2026 sobre los 25 que
        declaraba, **13 salían acompañados de un `TERMINAL_OUT` o de un log de
        panel en el mismo sitio**, así que el usuario se enteraba igual. El
        indicador decía 25 donde había 12.

        Importa porque este documento se usa para decidir en qué trabajar: un
        indicador que exagera manda a arreglar lo que no está roto, que es la
        misma clase de error que un pendiente falso en un megaplan.
        """
        return self.backend - self.interfaz - self.acompanados

    @property
    def comandos_conectados(self) -> set[str]:
        """La UI los manda y hay handler. El cable de ida, completo."""
        return self.interfaz & self.atendidos

    @property
    def eventos_conectados(self) -> set[str]:
        """El backend los emite y la UI los nombra. El cable de vuelta."""
        return self.interfaz & self.emitidos

    def render(self) -> str:
        L = ["# Mapa de la interfaz de MAGI",
             "",
             "Generado por `magi.modules.gui.mapa`. No arranca la aplicación:",
             "mapea el cableado por topics entre `magi-gui/src` y `magi/`.",
             "",
             "| | |",
             "|---|---:|",
             f"| Comandos conectados (UI → handler) | {len(self.comandos_conectados)} |",
             f"| Eventos conectados (backend → UI) | {len(self.eventos_conectados)} |",
             f"| Sin nadie al otro lado | {len(self.paneles_muertos)} |",
             f"| Capacidades invisibles | {len(self.capacidades_invisibles)} |",
             f"| Se ven por otro canal | {len(self.acompanados)} |",
             ""]
        for titulo, conjunto, nota in (
            ("Comandos conectados", self.comandos_conectados,
             "la UI los manda y hay `register_handler` que los atiende"),
            ("Eventos conectados", self.eventos_conectados,
             "el backend los emite y la UI los nombra"),
            ("Sin nadie al otro lado", self.paneles_muertos,
             "la UI los nombra y el backend ni los emite ni los atiende"),
            ("Capacidades invisibles", self.capacidades_invisibles,
             "trabajo que se hace y el usuario NO ve por ningún canal"),
            ("Se ven por otro canal", self.acompanados,
             "la UI no los nombra, pero salen junto a un TERMINAL_OUT o a un "
             "log de panel: el usuario se entera igual"),
        ):
            L += [f"## {titulo}", "", f"_{nota}_", ""]
            L += [f"- `{t}`" for t in sorted(conjunto)] or ["- (ninguno)"]
            L += [""]
        return "\n".join(L)


#: Canales que la interfaz SÍ pinta. Un topic emitido junto a uno de estos
#: llega al usuario aunque la UI no lo nombre.
_CANALES_VISIBLES = ("TERMINAL_OUT", "naoko.log", "ritsuko.log", "AGENT_POST",
                     "task.cancelled")

#: Líneas de distancia para considerar dos publicaciones "el mismo sitio".
#: Doce cubre un bloque con su payload sin cruzar a la función siguiente.
_VENTANA = 12


def _acompanados(raiz: Path, candidatos: set[str]) -> set[str]:
    """De los topics que la UI no nombra, los que salen con un canal visible."""
    vistos: set[str] = set()
    for fichero in (raiz / "magi").rglob("*.py"):
        if any(p in fichero.parts for p in ("__pycache__", "_attic")):
            continue
        try:
            lineas = fichero.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, linea in enumerate(lineas):
            if "subscribe" in linea or not re.search(r"publish|emit|topic", linea):
                continue
            for topic in candidatos - vistos:
                if f'"{topic}"' not in linea:
                    continue
                ctx = "\n".join(lineas[max(0, i - _VENTANA):i + _VENTANA])
                if any(c in ctx for c in _CANALES_VISIBLES):
                    vistos.add(topic)
    return vistos


def mapa(inicio: str | Path | None = None) -> Mapa:
    r = _raiz_repo(inicio)
    if r is None:
        return Mapa()
    m = Mapa(interfaz=_topics_interfaz(r),
             emitidos=_topics_emitidos(r),
             atendidos=_topics_atendidos(r))
    m.acompanados = _acompanados(r, m.backend - m.interfaz)
    return m


if __name__ == "__main__":       # pragma: no cover
    # La consola de Windows es cp1252 y este mapa lleva flechas: sin
    # esto, regenerarlo revienta con UnicodeEncodeError y el documento
    # se queda desfasado sin que el fallo diga por que. Es la regla 6
    # del traspaso, que este generador no cumplia.
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(mapa().render())
