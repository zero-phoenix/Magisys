"""
TRIPWIRE: jamás modelos locales (mandato del usuario, 2026-09-27; §I.3 siempre).

La v5.27.0-1 llevaba KoboldCpp + Qwen 1.5B corriendo EN LA MÁQUINA del
usuario. Eso es un modelo local: fuera de la política del proyecto y fuera
del mandato. Este test existe para que volver a colarse cueste un CI rojo, no
un descubrimiento tardío.

QUÉ VIGILA
==========
1. El código activo (`magi/` sin `_attic`) y las dependencias declaradas no
   referencian motor local alguno: kobold, ollama, llama.cpp, puertos de
   servidor local de LLM, ni claves de API que no sean la excepción Groq.
2. Ninguno de los dos registros que arma el sistema (enjambre y subagentes)
   registra un proveedor `is_local`.

`magi/_attic/` queda fuera a propósito: es el cementerio documental de código
retirado, no código que corre. Si algún día se vacía, este test no cambia.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Motores locales prohibidos, por patrón. «llama» a secas NO está: llama es
#: también un modelo de NUBE (Groq llama-3.3, g4f llama) y prohibir la palabra
#: cazaría la mitad del catálogo legítimo. Lo que se prohíbe es el MOTOR
#: local, no el nombre del modelo.
#: OJO con la forma de buscar (ver el test): «kobold» y «ollama» aparecen
#: LEGÍTIMAMENTE en comentarios y docstrings que documentan esta prohibición
#: (y el catálogo de g4f excluye Ollama POR ser local). Un escaneo de texto
#: plano pelearía con la documentación para siempre. Se vigila el USO real:
#: identificadores, imports y atributos (ningún uso puede evitar nombrarlos),
#: más las cadenas operativas (puertos y claves), que no aparecen en prosa.
_MOTORES_IDENTIFICADOR = (
    "kobold",           # KoboldCpp (el que vivía en magi/modules/lilim/)
    "ollama",
    "llamacpp",
    "llama_cpp",
    "clientekobold",
)

_CADENAS_OPERATIVAS = (
    "llama-server",
    "localhost:5001",   # el puerto del KoboldCpp de la v5.27.x
    "127.0.0.1:5001",
    # La ÚNICA clave permitida es Groq (GROQ_API_KEY). Estas dos movían la
    # vieja mielina/puente flash y quedaron retiradas con ella.
    "ZAI_API_KEY",
    "LILIM_API_KEY",
)


def _py_activos() -> list[Path]:
    return [p for p in (ROOT / "magi").rglob("*.py")
            if "_attic" not in p.parts]


def _identificadores_y_imports(fichero: Path) -> set[str]:
    """Nombres que el CÓDIGO usa de verdad: imports, nombres y atributos.
    Los docstrings y comentarios quedan fuera: documentar la prohibición no
    es violarla."""
    import ast
    try:
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return set()

    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            nombres.add(nodo.module or "")
        elif isinstance(nodo, ast.Name):
            nombres.add(nodo.id)
        elif isinstance(nodo, ast.Attribute):
            nombres.add(nodo.attr)
    return {n.lower() for n in nombres if n}


def test_el_codigo_activo_no_usa_motores_locales():
    hallazgos = []
    for fichero in _py_activos():
        nombres = _identificadores_y_imports(fichero)
        for motor in _MOTORES_IDENTIFICADOR:
            # coincidencia por prefijo de identificador: cliente_kobold,
            # import ollama, from koboldcpp import ...
            if any(motor in n for n in nombres):
                hallazgos.append(
                    f"{fichero.relative_to(ROOT)}: identificador '{motor}'")
        texto = fichero.read_text(encoding="utf-8", errors="replace")
        for patron in _CADENAS_OPERATIVAS:
            if patron.lower() in texto.lower():
                hallazgos.append(f"{fichero.relative_to(ROOT)}: {patron}")
    assert not hallazgos, (
        f"Motor local o clave no autorizada en uso — mandato §I.3: jamás "
        f"modelos locales. Hallazgos: {hallazgos}")


def test_las_dependencias_no_arrastran_motores_locales():
    for fichero in (ROOT / "requirements.txt", ROOT / "requirements-dev.txt",
                    ROOT / "requirements.lock", ROOT / "Magisys.spec"):
        if not fichero.exists():
            continue
        texto = fichero.read_text(encoding="utf-8").lower()
        for patron in ("kobold", "ollama", "llama.cpp", "llamacpp"):
            assert patron not in texto, f"{fichero.name} arrastra {patron}"


async def test_el_registro_del_enjambre_no_tiene_nada_local():
    from magi.core.providers.backends import build_default_registry
    reg = await build_default_registry(probe=False)
    locales = [r.id for r in reg.all() if r.provider.is_local]
    assert not locales, f"el registro del enjambre registró locales: {locales}"


async def test_el_registro_de_subagentes_no_tiene_nada_local():
    from magi.core.providers.backends import build_subagent_registry
    reg = await build_subagent_registry(probe=False)
    locales = [r.id for r in reg.all() if r.provider.is_local]
    assert not locales, f"el registro de subagentes registró locales: {locales}"


async def test_ningun_proveedor_de_fabrica_declara_ser_local():
    """Los proveedores que el sistema arma por defecto, activos o no, son de
    nube. El único is_local=True del repo es EchoProvider, que solo existe
    para tests y nadie registra por defecto."""
    from magi.core.providers.backends import build_groq_providers
    from magi.core.providers.backends.g4f_backend import FAMILY_SPECS, G4FProvider
    for p in build_groq_providers():
        assert not p.is_local
    for familia in FAMILY_SPECS:
        assert not G4FProvider(family=familia).is_local


def test_el_catalogo_no_ofrece_familias_locales():
    import json
    cat = json.loads(
        (ROOT / "magi" / "data" / "catalogo_proveedores.json")
        .read_text(encoding="utf-8"))
    for familia, datos in cat.get("familias", {}).items():
        for candidato in datos.get("candidatos", []):
            proveedor = candidato.get("proveedor", "").lower()
            for patron in ("kobold", "ollama", "llama.cpp"):
                assert patron not in proveedor, (
                    f"familia {familia}: candidato local {proveedor}")
