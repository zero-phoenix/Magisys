"""
Una herramienta registrada y fuera de toda caja es inalcanzable.

QUÉ PASÓ
========
`registry_for_role` acota el catálogo por dominio: `CORE_TOOLS` más la caja del
dominio que active el encargo. Una herramienta que no esté en ninguna de las dos
existe en el registry, cuenta para las cifras del README y **no llega a ningún
prompt** en cuanto la tarea menciona un dominio.

Medido el 13-sep-2026 con «portar el dynarec del SH2 a la Vita»:

    67 herramientas en el registry
    26 visibles para MELCHIOR
    18 fuera de CORE y de toda caja      <- el 27 % del catálogo

Entre esas 18 estaba la capa Lilim entera (seis herramientas, tres versiones de
trabajo), la memoria, los sentidos y `web_search`/`web_read` — justo las que más
falta hacen en un encargo técnico. Ninguna estaba rota: estaban fuera del
alcance del enjambre y nadie lo veía, porque el trinquete de huérfanos busca el
NOMBRE en cualquier fichero del repositorio y todas aparecían en el suyo.

Es la tercera vez que pasa lo mismo: ya le ocurrió a `git`/`gh` y al dominio del
mundo con «gasto militar», y está escrito en el comentario de `_DOMAIN_HINTS`
como lección aprendida. Lo que faltaba era quien lo vigilara.

POR QUÉ HAY TECHO Y NO CERO
===========================
El catálogo entra en cada prompt y tiene dos límites medidos por
`test_catalog_stays_within_a_free_provider_window`: 2700 caracteres para un
dominio y 3500 cuando el encargo activa dos. El peor caso de un dominio va hoy a
2617 y el multidominio a 3160: quedan 83 y 340 caracteres, y cada herramienta
cuesta entre 75 y 163.

O sea que **no caben todas**, y forzarlas rompería el techo que protege la
ventana de 8k de los proveedores gratuitos. Lo que sí se puede exigir es que el
número no crezca sin que alguien lo decida: eso es este fichero.
"""

from __future__ import annotations

import pytest

from magi.core.tools import builtin as b

#: Herramientas registradas que no están en CORE_TOOLS ni en ninguna caja de
#: dominio. Medido: eran 18 el 13-sep-2026; el reparto por dominio bajó a 12.
#: Este número NO se sube. Si una herramienta nueva se queda fuera, o entra en
#: la caja que le toca o se justifica bajando otra.
TECHO_INALCANZABLES = 12


def _inalcanzables() -> list[str]:
    todas = set(b.build_registry().names())
    cubiertas = set(b.CORE_TOOLS)
    for conjunto in b._DOMAIN_TOOLSETS.values():
        cubiertas |= set(conjunto)
    return sorted(todas - cubiertas)


def test_el_numero_de_herramientas_inalcanzables_no_crece():
    """
    El trinquete. Una herramienta nueva que nazca fuera de toda caja lo dispara.
    """
    fuera = _inalcanzables()
    assert len(fuera) <= TECHO_INALCANZABLES, (
        f"hay {len(fuera)} herramientas inalcanzables con pista de dominio y el "
        f"techo es {TECHO_INALCANZABLES}:\n  "
        + "\n  ".join(fuera)
        + "\n\nMétela en la caja de su dominio (_DOMAIN_TOOLSETS) o en "
        "CORE_TOOLS. Si no cabe por el techo del catálogo, dilo en el "
        "comentario y baja otra."
    )


def test_si_bajan_las_inalcanzables_se_baja_el_techo():
    """
    Un techo que se queda por encima de la realidad deja hueco para colar.

    Es el mismo mecanismo que `test_sin_huerfanos`: el número solo puede ir
    hacia abajo, y cuando baja se escribe.
    """
    fuera = _inalcanzables()
    assert len(fuera) >= TECHO_INALCANZABLES - 3, (
        f"quedan {len(fuera)} inalcanzables y el techo sigue en "
        f"{TECHO_INALCANZABLES}. Bájalo en este fichero: un techo con holgura "
        f"es una puerta abierta."
    )


@pytest.mark.parametrize(
    "herramienta,hint",
    [
        ("repos_clonar", "portar el dynarec de PPSSPP a Vita"),
        ("web_search", "haz commit y push del repositorio"),
        ("web_read", "haz commit y push del repositorio"),
        ("repos_desregistrar", "haz commit y push del repositorio"),
    ],
)
def test_las_herramientas_de_v5_25_a_v5_27_llegan_a_su_dominio(herramienta, hint):
    """
    Las que se añadieron en las tres últimas versiones y no llegaban a nadie.

    No comprueba la lista de conjuntos: comprueba lo que ve el nodo, que es lo
    que importa. `registry_for_role` es el mismo camino que usa el orquestador.
    """
    visibles = set(b.registry_for_role("MELCHIOR", task_hint=hint).names())
    assert herramienta in visibles, (
        f"«{herramienta}» no llega a MELCHIOR con el encargo «{hint}»; ve "
        f"{len(visibles)} herramientas y esa no está"
    )


def test_el_catalogo_sigue_cabiendo_en_la_ventana_gratuita():
    """
    El reparto no puede pagarse rompiendo el techo que lo limita.

    Se repite aquí —y no solo en `test_reverse.py`— porque es la restricción
    que decide qué entra y qué no: si alguien mueve una herramienta de caja,
    esto se lo dice en el mismo fichero donde está la tentación.
    """
    peor = 0
    for hint in (
        "portar el dynarec de PPSSPP a Vita",
        "dibuja una página de manga",
        "analiza los fundamentales de Apple",
        "reparar el código",
        "haz commit y push del repositorio",
    ):
        for rol in ("MELCHIOR", "BALTHASAR", "CASPER"):
            peor = max(peor, len(b.registry_for_role(rol, task_hint=hint).catalog()))
    assert peor < 2700, f"el peor catálogo de un dominio va a {peor} caracteres"

    multi = max(
        len(
            b.registry_for_role(
                rol, task_hint="escribe un juego y analiza su rendimiento macro"
            ).catalog()
        )
        for rol in ("MELCHIOR", "BALTHASAR", "CASPER")
    )
    assert multi < 3500, f"el catálogo multidominio va a {multi} caracteres"
