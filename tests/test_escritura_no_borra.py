"""
Una escritura que deja el fichero en un cuarto no es una edición: es un borrado.

QUÉ PASÓ, Y ESTÁ MEDIDO
=======================
En la ronda del 8-sep-2026 se dejó al enjambre tocar el repositorio del
emulador. `src/vita/vidgpu.c` pasó de **417 líneas a 15**; de 14.267 bytes a
523. Se restauró con `git checkout` y no llegó a commitearse, pero el agujero
siguió abierto: nada impedía que `write_file` sustituyera un fichero entero por
un fragmento. Está escrito en `docs/TRASPASO-ASTRA.md` §5.1 como lo URGENTE que
había que cerrar antes de volver a soltarle el repositorio al enjambre.

El journal lo hacía reversible, sí. Pero reversible no es lo mismo que avisado:
nadie se enteró hasta que alguien abrió el fichero.

POR QUÉ UN CUARTO Y NO OTRA COSA
================================
No es una constante bonita: es el orden de magnitud del caso real. `vidgpu.c`
quedó en el **3,7 %** de su tamaño. Un umbral del 25 % deja pasar las
reescrituras normales —compactar, quitar código muerto, reemplazar una
implementación por otra más corta— y para el borrado disfrazado de edición, que
es lo que ocurrió.

Y solo se mira en ficheros de más de 1 KB: por debajo, cualquier cambio es un
porcentaje enorme y el aviso sería ruido constante.

NO ES UNA PROHIBICIÓN
=====================
`truncar=True` lo permite. La diferencia no es poder o no poder: es que reducir
un fichero a la cuarta parte pase de ser un accidente silencioso a un acto
declarado, que se lee en la traza de herramientas.
"""

from __future__ import annotations

import pytest

from magi.core.tools.builtin import ToolContext, build_registry

#: El fichero real que se destruyó, con sus tamaños reales.
BYTES_ORIGINAL = 14_267
BYTES_DESTRUIDO = 523


def _fichero(tmp_path, nombre: str, tamano: int):
    p = tmp_path / nombre
    # Líneas plausibles de código C, para que el tamaño sea el del caso real.
    linea = "    VIDGPUVdp2LogTiming(drawn, presented, dropped);\n"
    p.write_text(linea * (tamano // len(linea) + 1), encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_reducir_un_fichero_a_un_cuarto_se_rechaza(tmp_path):
    """El caso exacto del 8-sep: 14.267 bytes -> 523."""
    reg = build_registry()
    ctx = ToolContext(task_id="t-borrado", cwd=tmp_path)
    p = _fichero(tmp_path, "vidgpu.c", BYTES_ORIGINAL)
    antes = p.read_text(encoding="utf-8")

    res = await reg.execute(
        "write_file", {"path": str(p), "content": "x" * BYTES_DESTRUIDO}, ctx=ctx
    )

    assert not res.ok, "se permitió dejar el fichero en el 3,7 % de su tamaño"
    assert p.read_text(encoding="utf-8") == antes, "el fichero se tocó igualmente"
    assert (
        "truncar" in (res.error or "").lower()
    ), f"el error no dice cómo hacerlo a propósito: {res.error!r}"


@pytest.mark.asyncio
async def test_con_truncar_explicito_se_permite(tmp_path):
    """No es una prohibición: es una declaración de intención."""
    reg = build_registry()
    ctx = ToolContext(task_id="t-borrado", cwd=tmp_path)
    p = _fichero(tmp_path, "vidgpu.c", BYTES_ORIGINAL)

    res = await reg.execute(
        "write_file", {"path": str(p), "content": "x" * BYTES_DESTRUIDO, "truncar": True}, ctx=ctx
    )

    assert res.ok, res.error
    assert len(p.read_text(encoding="utf-8")) == BYTES_DESTRUIDO


@pytest.mark.asyncio
async def test_una_reescritura_normal_no_se_estorba(tmp_path):
    """
    Control, en la misma corrida.

    Sin esto, la guarda pasaría igual si rechazara TODA escritura sobre un
    fichero existente — y entonces el remedio sería peor: el enjambre no podría
    trabajar y alguien la quitaría entera.
    """
    reg = build_registry()
    ctx = ToolContext(task_id="t-borrado", cwd=tmp_path)
    p = _fichero(tmp_path, "modulo.py", 10_000)

    res = await reg.execute("write_file", {"path": str(p), "content": "y" * 6_000}, ctx=ctx)

    assert res.ok, f"una reducción al 60 % no es un borrado: {res.error}"


@pytest.mark.asyncio
async def test_los_ficheros_pequenos_no_disparan_el_aviso(tmp_path):
    """
    Por debajo de 1 KB cualquier cambio es un porcentaje enorme.

    Un aviso que salta siempre deja de leerse, que es como se pierden los
    avisos que sí importan.
    """
    reg = build_registry()
    ctx = ToolContext(task_id="t-borrado", cwd=tmp_path)
    p = tmp_path / "conf.ini"
    p.write_text("a" * 300, encoding="utf-8")

    res = await reg.execute("write_file", {"path": str(p), "content": "b"}, ctx=ctx)

    assert res.ok, f"molestó con un fichero de 300 bytes: {res.error}"


@pytest.mark.asyncio
async def test_crear_un_fichero_nuevo_no_se_compara_con_nada(tmp_path):
    """Un fichero que no existe no puede encogerse."""
    reg = build_registry()
    ctx = ToolContext(task_id="t-borrado", cwd=tmp_path)

    res = await reg.execute(
        "write_file", {"path": str(tmp_path / "nuevo.txt"), "content": "hola"}, ctx=ctx
    )

    assert res.ok, res.error
