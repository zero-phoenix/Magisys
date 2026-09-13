"""
LILIM — la capa local superveloz de MAGI (megaplan v12).

QUÉ ES
======
La capa LOCAL, determinista y sin red que responde al instante lo que el
sistema ya sabe, CON PROCEDENCIA, y escala al enjambre cuando no lo sabe.

NO es un LLM y no razona: es un índice vivo sobre memoria versionada
(controles.json, repos_top.json, AUTOMODELO). Su honestidad es su valor:

  · Lo que sabe → respuesta en ms con `fuente:` y `falsable contra: <URL>`.
  · Lo que no sabe → "NO LO SÉ (local) — escala al enjambre". NUNCA inventa.

La misión Tetris (5-sep) midió por qué esto importa: los proveedores
gratuitos tardan 3-22 s por llamada, fallan a menudo, y una tarea trivial
puede gastar 20 minutos. Cada pregunta que Lilim responde local es una
llamada de red que no se gasta — y cada respuesta lleva su evidencia.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .brazos import exportar_a_docx, exportar_a_markdown, recortar_region_imagen
from .cliente_kobold import ClienteKobold
from .mielina import (
    clasificar_intencion_local,
    lubricar_arbitraje,
    lubricar_critica,
    lubricar_propuesta,
    lubricar_vision,
    pre_auditoria_estatica,
)
from .oidos import escuchar_subsistema_emulador, inspeccionar_fichero_audio
from .ojos import ResultadoLens, analizar_documento_escaneado, rasterizar_pagina_pdf

__all__ = ["pregunta", "repos_de", "NO_LO_SE", "responde_si_sabe",
           "registrar_conocimiento", "repos_clonar", "desregistrar_clon",
           "enciclopedia", "verificar_novedades_fuente", "ClienteKobold",
           "lubricar_propuesta", "lubricar_critica", "lubricar_arbitraje",
           "lubricar_vision", "clasificar_intencion_local",
           "pre_auditoria_estatica", "analizar_documento_escaneado",
           "rasterizar_pagina_pdf", "ResultadoLens",
           "inspeccionar_fichero_audio", "escuchar_subsistema_emulador",
           "exportar_a_markdown", "exportar_a_docx", "recortar_region_imagen"]

NO_LO_SE = "NO LO SÉ (local) — escala al enjambre: razonamiento y verificación de nube."

_RAIZ = Path(__file__).resolve().parents[2] / "data" / "memoria"


def _cargar(nombre: str) -> dict:
    ruta = _RAIZ / nombre
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _plano(s: str) -> str:
    import unicodedata
    sin = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in sin if not unicodedata.combining(c))


def repos_de(tema: str) -> str:
    """Los repos curados de un tema, con su URL (metadatos, no clones)."""
    datos = _cargar("repos_top.json")
    t = _plano(tema)
    if not t:
        return ("temas del índice: "
                + ", ".join(sorted({r["tema"] for r in datos.get("repos", [])})))
    hits = [r for r in datos.get("repos", [])
            if t in _plano(r.get("tema", "")) or t in _plano(r.get("nombre", ""))
            or any(w in _plano(r.get("para_que", "")) for w in t.split() if len(w) > 3)]
    if not hits:
        return (NO_LO_SE + " Temas disponibles: "
                + ", ".join(sorted({r["tema"] for r in datos.get("repos", [])})))
    filas = [f"- {r['nombre']} — {r['para_que']}\n  {r['url']}"
             f"  (curado {datos.get('curado', '?')}; falsable contra la URL)"
             for r in hits]
    return ("REPOS DEL ÍNDICE PARA '" + tema + "' (memoria local, "
            + str(datos.get("curado", "?")) + "):\n" + "\n".join(filas))


def pregunta(pregunta_texto: str) -> str:
    """
    La respuesta local más rápida posible, o NO_LO_SE. Nada intermedio.
    """
    if not isinstance(pregunta_texto, str) or not pregunta_texto.strip():
        return NO_LO_SE
    t = _plano(pregunta_texto)

    datos = _cargar("controles.json")

    # 1. decomp / puertos
    if re.search(r"\b(decomp|dusklight|ghidra|objdiff|byte.?match|recompil)\w*",
                 t):
        d = datos.get("decompilacion_y_puertos") or {}
        if d:
            pasos = "; ".join(f"{k}: {v[:90]}" for k, v in
                              (d.get("flujo_en_5_pasos") or {}).items())
            return (f"DECOMP (memoria local, {datos.get('actualizado', '?')}): "
                    f"{d.get('que_es', '')} Flujo: {pasos}. "
                    "fuente: controles.json#decompilacion_y_puertos — "
                    "falsable contra: los repos del flujo (ghidra, objdiff).")

    # 2. controles de consola / PC
    consolas = datos.get("consolas") or {}
    for nombre, valor in consolas.items():
        if _plano(nombre).replace("_", " ") in t or _plano(nombre) in t:
            cuerpo = valor if isinstance(valor, str) else json.dumps(
                valor, ensure_ascii=False)
            return (f"CONTROLES {nombre} (memoria local, "
                    f"{datos.get('actualizado', '?')}): {cuerpo[:600]} "
                    "fuente: controles.json — falsable contra: "
                    "la documentación del mando de la consola.")
    if re.search(r"\b(teclado|pc|ordenador|computadora|gamepad|xinput)\b", t):
        pc = datos.get("pc_jugando") or {}
        if pc:
            return ("PC PARA JUGAR (memoria local, "
                    f"{datos.get('actualizado', '?')}): "
                    + json.dumps(pc, ensure_ascii=False)[:700]
                    + " fuente: controles.json#pc_jugando")

    # 3. repos
    if re.search(r"\b(repo|repositorio|github|libreria|biblioteca)\b", t):
        for palabra in t.split():
            if len(palabra) > 3:
                r = repos_de(palabra)
                if NO_LO_SE not in r:
                    return r

    # 4. enciclopedia técnica (L4: SH2, VDP, Vita, Decomp)
    e = enciclopedia(t)
    if e != NO_LO_SE:
        return e

    # 5. lo que no está en memoria NO se inventa
    return NO_LO_SE


def enciclopedia(dominio: str = "") -> str:
    """L4: Enciclopedia técnica por dominios curados (SH2, VDP, Vita, Decomp)."""
    datos = _cargar("enciclopedia.json")
    doms = datos.get("dominios") or {}
    if not dominio or not dominio.strip():
        return "Dominios técnicos en enciclopedia: " + ", ".join(sorted(doms.keys()))

    t = _plano(dominio)
    for k, v in doms.items():
        if (_plano(k) in t or t in _plano(k)
                or any(w in t for w in _plano(v.get("titulo", "")).split() if len(w) > 4)):
            puntos = "\n".join(f"  · {p}" for p in v.get("puntos_clave", []))
            marca = "verificado" if v.get("verificado") else "SIN COMPROBAR"
            return (
                f"ENCICLOPEDIA LILIM [{k.upper()} · {marca}] (curado {datos.get('curado', '?')}):\n"
                f"{v.get('titulo', '')}\n"
                f"{v.get('resumen', '')}\n"
                f"Puntos clave:\n{puntos}\n"
                f"fuente: {v.get('fuente', '')} — falsable contra la documentación técnica."
            )
    return NO_LO_SE


def verificar_novedades_fuente(
    tema_o_titulo: str = "",
    _lector: Any = None,
) -> tuple[int, int, str]:
    """
    L3/L4: Verifica novedades contra sus URLs fuente usando lectura web sin navegador.
    Retorna (verificados_nuevos, total_procesados, reporte).
    """
    datos = _cargar("novedades.json")
    entradas = datos.get("entradas") or []
    t = _plano(tema_o_titulo)

    from datetime import datetime, timezone
    fecha_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    verificados = 0
    procesados = 0
    detalles = []

    for e in entradas:
        if t and (t not in _plano(e.get("tema", "")) and t not in _plano(e.get("titulo", ""))):
            continue
        if e.get("verificado"):
            continue

        procesados += 1
        url = e.get("fuente_verificar", "")
        if not url:
            continue

        contenido = ""
        ok_lectura = False
        if _lector is not None:
            ok_lectura, contenido, _ = _lector(url)
        else:
            try:
                from ..percepcion.web import web_read
                ok_lectura, contenido, _ = web_read(url, max_chars=2000)
            except Exception:
                ok_lectura = False

        if ok_lectura and any(w in _plano(contenido) for w in _plano(e.get("titulo", "")).split() if len(w) > 4):
            e["verificado"] = True
            e["verificado_en"] = fecha_hoy
            verificados += 1
            detalles.append(f"✅ Verificado: '{e.get('titulo')}' contra {url}")
        else:
            detalles.append(f"⏳ SIN COMPROBAR: '{e.get('titulo')}' (sin coincidencia de fuente)")

    reporte = f"Verificación de novedades: {verificados}/{procesados} confirmadas con evidencia.\n" + "\n".join(detalles)
    return verificados, procesados, reporte


# ------------------------------------------------- L3: conocimiento verificado

#: La enciclopedia que CRECE: cada respuesta del enjambre que trae URL se
#: guarda aquí con su fecha, y Lilim pasa a responderla localmente. Es la
#: memoria M4 del plan v12 — conocimiento verificado por nube, servido local.
_CONOCIMIENTO = _RAIZ / "conocimiento.jsonl"


def registrar_conocimiento(tema: str, afirmacion: str, url: str,
                           quien: str = "enjambre") -> bool:
    """
    Guarda una afirmación CON su evidencia (URL). Sin URL no entra: una
    enciclopedia sin procedencia es el sistema inventando más rápido.
    """
    if not (tema and afirmacion and url):
        return False
    from datetime import datetime
    fila = {"tema": _plano(tema), "afirmacion": afirmacion[:800],
            "url": url, "quien": quien,
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")}
    with _CONOCIMIENTO.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fila, ensure_ascii=False) + "\n")
    return True


def _conocimiento_de(tema_plano: str) -> str | None:
    if not _CONOCIMIENTO.exists():
        return None
    mejor = None
    for linea in _CONOCIMIENTO.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(linea)
        except Exception:
            continue
        if e.get("tema") and (e["tema"] in tema_plano
                              or tema_plano in e["tema"]):
            mejor = e                    # la última manda
    if not mejor:
        return None
    return (f"CONOCIMIENTO VERIFICADO ({mejor['fecha']}, por {mejor['quien']}): "
            f"{mejor['afirmacion']} "
            f"fuente: {mejor['url']} — falsable contra "
            "la URL y contra el enjambre si lo pides.")


def responde_si_sabe(texto: str) -> str | None:
    """
    El puente (v12 §3): lo que Lilim sabe, respondido con procedencia;
    None si no lo sabe — y entonces quien llamó escala al enjambre.

    Consulta en orden: conocimiento verificado (M4), controles/PC,
    decomp, repos. Nada de esto inventa; lo que no está, no se responde.
    """
    if not isinstance(texto, str) or not texto.strip():
        return None
    t = _plano(texto)
    palabras = {p for p in re.findall(r"\w{4,}", t)}
    if palabras:
        for p in palabras:
            r = _conocimiento_de(p)
            if r:
                return r
    r = pregunta(texto)
    return None if r == NO_LO_SE else r


# ----------------------------------------------------------------- idiomas

#: Los seis idiomas del contrato (v12): traducción de terminología al
#: instante desde memoria; frases completas por el puente de nube.
IDIOMAS = ("es", "en", "de", "ru", "ja", "zh")


def detecta_idioma(texto: str) -> str:
    """Por escritura primero (no falla), y por palabras de par después."""
    t = texto or ""
    if any("぀" <= c <= "ヿ" for c in t):
        return "ja"
    if any("Ѐ" <= c <= "ӿ" for c in t):
        return "ru"
    if any("一" <= c <= "鿿" for c in t):
        return "zh"                      # kanji puro: chino (ja lo canta antes)
    if any(c in "äöüßÄÖÜ" for c in t):
        return "de"
    en = {"the", "and", "is", "of", "to", "with", "for"}
    es = {"el", "la", "los", "de", "que", "con", "para", "una", "es"}
    palabras = set(_plano(t).split())
    if palabras & en and not palabras & es:
        return "en"
    if palabras & es:
        return "es"
    return "es"


def traduce(texto: str, idioma_destino: str) -> str:
    """
    Traduce lo que está en la memoria de terminología AL INSTANTE y con
    procedencia. Las palabras que no están se dejan tal cual y se marcan
    [desconocido] — NUNCA se inventa una traducción. Si el texto es una
    frase que la memoria no cubre, el puente manda: se dice explícitamente
    que la traducción completa va por el enjambre de nube.
    """
    idioma_destino = (idioma_destino or "").strip().lower()[:2]
    if idioma_destino not in IDIOMAS:
        return (f"idioma '{idioma_destino}' no está en el contrato. "
                f"Idiomas: {', '.join(IDIOMAS)}")
    origen = detecta_idioma(texto)
    if origen == idioma_destino:
        return f"[mismo idioma: {origen}] {texto}"
    datos = _cargar("idiomas.json")
    terminos = datos.get("terminos") or {}
    fuera, desconocidas = [], 0
    for palabra in texto.split():
        limpia = palabra.strip(".,;:!?¿¡()«»")
        clave = _plano(limpia).replace(" ", "_")
        entrada = terminos.get(clave) or next(
            (v for k, v in terminos.items()
             if _plano(k).replace("_", " ") == clave), None)
        if entrada and entrada.get(idioma_destino):
            fuera.append(entrada[idioma_destino])
        else:
            fuera.append(palabra)
            if limpia:
                desconocidas += 1
    resultado = " ".join(fuera)
    pieza = (f"TRADUCCIÓN LOCAL ({origen}→{idioma_destino}, memoria "
             f"{datos.get('curado', '?')}): {resultado}")
    if desconocidas > len(fuera) // 2:
        pieza += ("  — la mayoría de las palabras no están en la memoria de "
                  "terminología: para la FRASE completa, el puente la manda "
                  "al enjambre de nube (esta traducción es solo de términos "
                  "conocidos).")
    return pieza


def novedades(tema: str = "") -> str:
    """
    Las novedades tecnológicas de la memoria (2023-2026). Cada entrada es
    FALSABLE: lleva fuente_verificar y su bandera `verificado` — lo que no
    se ha contrastado con internet se dice, que es la regla de la casa.
    """
    datos = _cargar("novedades.json")
    entradas = datos.get("entradas") or []
    t = _plano(tema)
    elegidas = [e for e in entradas
                if not t or t in _plano(e.get("tema", ""))
                or any(w in _plano(e.get("titulo", "") + " "
                                   + e.get("detalle", ""))
                       for w in t.split() if len(w) > 3)] or entradas
    if not elegidas:
        return NO_LO_SE
    filas = []
    for e in elegidas[:8]:
        marca = "verificado" if e.get("verificado") else "SIN COMPROBAR"
        filas.append(f"- [{e.get('anio', '?')} · {e.get('tema', '?')} · "
                     f"{marca}] {e.get('titulo', '?')}: "
                     f"{e.get('detalle', '')} "
                     f"verificar: {e.get('fuente_verificar', '')}")
    return ("NOVEDADES EN MEMORIA (curado " + str(datos.get("curado", "?"))
            + "):\n" + "\n".join(filas))


def contexto(encargo: str) -> str:
    """
    El paquete local para los tres nodos, Naoko y Ritsuko: los hechos de
    memoria que tocan a ESTE encargo, en ms. Es la infraestructura que hace
    que el enjambre empiece sabiendo en vez de descubriendo.
    """
    piezas = []
    t = _plano(encargo)
    if re.search(r"\b(juego|jugar|mando|teclado|consola|exe)\b", t):
        r = repos_de("gamedev")
        if NO_LO_SE not in r:
            piezas.append(r)
    if re.search(r"\b(ia|llm|modelo|bateria|novedad)\b", t):
        n = novedades("ia") or novedades("baterias")
        if n and NO_LO_SE not in n:
            piezas.append(n)
    if re.search(r"\b(decomp|dusklight|ghidra|objdiff|port|puerto)\b", t):
        d = pregunta("como funciona una decompilacion dusklight")
        if NO_LO_SE not in d:
            piezas.append(d)
    if not piezas:
        return ""
    return "CONTEXTO LILIM (local, en ms):\n" + "\n".join(piezas)


def repos_clonar(
    nombre_o_url: str,
    destino: Path | str | None = None,
    task_id: str | None = None,
    journal: Any = None,
    profundidad: int = 1,
) -> tuple[bool, str, dict]:
    """
    L2: Clon shallow (--depth 1) de un repo del índice repos_top.json al workspace.

    Registra cada fichero importado en el journal de la tarea para cumplir la
    compuerta A3 (código ANTES del build: ningún ejecutable nace sin fuentes).
    """
    import subprocess

    if not isinstance(nombre_o_url, str) or not nombre_o_url.strip():
        return False, "Indica qué repositorio o URL clonar.", {}

    nombre_n = nombre_o_url.strip()
    datos = _cargar("repos_top.json")
    url = ""
    meta_repo: dict = {}

    # ¿Es URL directa de git o ruta de repositorio en disco?
    if (nombre_n.startswith(("http://", "https://", "git@", "ssh://", "file://"))
            or Path(nombre_n).is_dir()):
        url = nombre_n
        repo_leaf = Path(nombre_n).name or "repo"
        if repo_leaf.endswith(".git"):
            repo_leaf = repo_leaf[:-4]
        meta_repo = {"nombre": repo_leaf, "url": url, "tema": "externo"}
    else:
        # Búsqueda en el índice curado
        t = _plano(nombre_n)
        for r in datos.get("repos", []):
            if t == _plano(r.get("nombre", "")) or t in _plano(r.get("nombre", "")):
                url = r.get("url", "")
                meta_repo = r
                break
        if not url:
            for r in datos.get("repos", []):
                if t == _plano(r.get("tema", "")):
                    url = r.get("url", "")
                    meta_repo = r
                    break
        if not url:
            return (
                False,
                f"El repositorio '{nombre_n}' no está en repos_top.json ni es una URL válida. "
                f"Usa repos_de() para ver los disponibles.",
                {},
            )

    # Destino en disco
    if destino is None:
        from ...core.paths import workspace_dir
        dest_dir = workspace_dir() / (meta_repo.get("nombre", "repo").split("/")[-1])
    else:
        dest_dir = Path(destino)

    dest_dir = dest_dir.resolve()
    if dest_dir.exists() and any(dest_dir.iterdir()):
        return (
            False,
            f"El directorio de destino ya existe y no está vacío: {dest_dir}",
            {},
        )

    # 1. Registrar directorio raíz en el journal antes de mutar
    if journal and hasattr(journal, "record"):
        try:
            journal.record(dest_dir, kind="create", tool="lilim_repos_clonar")
        except Exception:
            pass

    # 2. Clon shallow
    try:
        cmd = ["git", "clone", "--depth", str(max(1, profundidad)), url, str(dest_dir)]
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
        if res.returncode != 0:
            if dest_dir.exists():
                _rmtree_force(dest_dir)
            return (
                False,
                f"git clone falló con código {res.returncode}: {res.stderr.strip()}",
                {},
            )
    except Exception as e:
        if dest_dir.exists():
            _rmtree_force(dest_dir)
        return False, f"No se pudo ejecutar git clone: {e}", {}

    # 3. Registrar cada fichero en el journal (compuerta A3)
    registrados = 0
    if journal and hasattr(journal, "record"):
        for f in dest_dir.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                try:
                    journal.record(f, kind="create", tool="lilim_repos_clonar")
                    registrados += 1
                except Exception:
                    pass

    return (
        True,
        f"Clon shallow exitoso de '{meta_repo.get('nombre', url)}' en {dest_dir} "
        f"({registrados} ficheros registrados en journal para compuerta A3). "
        f"falsable contra: {url}",
        {
            "nombre": meta_repo.get("nombre"),
            "url": url,
            "destino": str(dest_dir),
            "ficheros_registrados": registrados,
        },
    )


def _rmtree_force(path: Path) -> None:
    """Elimina un árbol asegurando permisos de escritura en Windows."""
    import os
    import shutil
    import stat

    def _handle_readonly(func, fpath, exc_info):
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except Exception:
            pass

    if path.exists():
        shutil.rmtree(path, onerror=_handle_readonly)


def desregistrar_clon(
    destino: Path | str,
    task_id: str | None = None,
    journal: Any = None,
) -> tuple[bool, str]:
    """Des-registro limpio: elimina la copia clonada y deshace entradas de journal."""
    p = Path(destino).resolve()
    if not p.exists():
        return False, f"El directorio no existe: {p}"

    # Si hay journal, deshacer entradas correspondientes
    if journal and hasattr(journal, "all_entries") and hasattr(journal, "_restore"):
        p_str = str(p).replace("\\", "/").lower()
        for e in reversed(journal.all_entries()):
            target_str = str(e.target).replace("\\", "/").lower()
            if target_str == p_str or target_str.startswith(p_str + "/"):
                if not e.undone:
                    journal._restore(e)

    if p.exists():
        _rmtree_force(p)

    return True, f"Clon en {p} des-registrado y eliminado limpiamente."


# ------------------------------------------------------------------- RPC (L5)
#
# La capa local existia solo como herramienta del enjambre: para preguntarle
# algo habia que abrir una tarea y esperar a que un nodo decidiera usarla. Una
# respuesta de 0-3,5 ms detras de una ronda de debate.
#
# `lilim.pregunta` la pone al alcance de la interfaz. Devuelve la respuesta y
# su PROCEDENCIA por separado: sin fuente, una respuesta local es indistinguible
# de una inventada, que es justo lo que Lilim no hace.

async def rpc_pregunta(payload, websocket=None) -> dict:
    """Pregunta directa a la capa local. Sin red, sin GPU, sin ronda."""
    import time

    texto = str((payload or {}).get("pregunta", "")).strip()
    if not texto:
        return {"ok": False, "error": "pregunta vacia"}
    t0 = time.perf_counter()
    try:
        respuesta = pregunta(texto)
    except Exception as e:                      # nunca tumba la interfaz
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    ms = (time.perf_counter() - t0) * 1000.0
    return {
        "ok": True,
        "respuesta": respuesta,
        "ms": round(ms, 2),
        # El propio motor imprime su metrica MoE dentro del texto; aqui se
        # separa lo que la interfaz necesita para decir "esto es local".
        "local": True,
    }

