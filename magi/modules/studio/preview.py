"""
Vista previa de lo que MAGI produce.

EL FALLO QUE ESTO CIERRA
========================
La pestaña «Vista previa» de la interfaz tenía un iframe con la URL fijada en
el código:

    <iframe src="http://localhost:3000" ... />

Nadie levanta nada en ese puerto. Lo que veía el usuario era la página de error
del propio navegador: un cuadro blanco con un icono de nube. Y como el fondo de
la aplicación es negro, ese rectángulo blanco era además lo primero que se veía
al abrir la pestaña.

El error de diseño de fondo es que la vista previa asumía que MAGI construye
servidores web. MAGI construye ARTEFACTOS —un script, una imagen, una página de
manga, un informe, un juego— y los deja en el workspace. La vista previa debería
enseñar eso.

Este módulo enumera y lee esos artefactos. La URL sigue estando disponible en la
interfaz como modo secundario, para cuando de verdad haya un servidor.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from ...core.paths import workspace_dir

__all__ = ["listar_artefactos", "leer_artefacto", "clasificar"]

#: Tope de lectura. Un artefacto más grande se anuncia pero no se manda entero
#: por el websocket: bloquearía la interfaz por enseñar algo que no se puede
#: leer de una vez.
MAX_TEXTO = 400_000
MAX_BINARIO = 8_000_000

IMAGENES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
WEB = {".html", ".htm"}
VIDEOS = {".mp4", ".webm", ".mkv", ".mov"}
AUDIO = {".mp3", ".wav", ".ogg", ".flac"}
DOCUMENTOS = {".pdf"}
TEXTO = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".css",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh", ".ps1", ".bat", ".sql",
    ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".java", ".cs", ".rb", ".lua",
    ".xml", ".csv", ".env", ".gitignore", ".dockerfile",
}

#: Carpetas que nunca son un resultado que el usuario quiera ver.
RUIDO = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache",
         ".ruff_cache", ".journal", "dist", "build", ".mypy_cache"}


def clasificar(ruta: Path) -> str:
    ext = ruta.suffix.lower()
    if ext in IMAGENES:
        return "imagen"
    if ext in WEB:
        return "web"
    if ext in VIDEOS:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext in DOCUMENTOS:
        return "documento"
    if ext in TEXTO or not ext:
        return "texto"
    return "binario"


def _seguro(rel: str) -> Path:
    """
    Resuelve una ruta relativa DENTRO del workspace, o lanza.

    La interfaz manda la ruta que el usuario ha pulsado; sin esta comprobación,
    un `../../` serviría cualquier fichero de la máquina por el websocket.
    """
    raiz = workspace_dir().resolve()
    destino = (raiz / rel).resolve()
    if raiz not in destino.parents and destino != raiz:
        raise ValueError("ruta fuera del workspace")
    return destino


def listar_artefactos(limite: int = 200) -> dict:
    """
    Los artefactos del workspace, del más reciente al más antiguo.

    Ordenados por fecha de modificación a propósito: lo que acaba de generar el
    enjambre es lo que el usuario quiere ver, y sin ese orden la vista previa
    abriría por el primero alfabético.
    """
    raiz = workspace_dir()
    items: list[dict] = []
    try:
        for p in raiz.rglob("*"):
            if not p.is_file():
                continue
            if any(parte in RUIDO for parte in p.parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            items.append({
                "path": p.relative_to(raiz).as_posix(),
                "nombre": p.name,
                "tipo": clasificar(p),
                "bytes": st.st_size,
                "mtime": st.st_mtime,
            })
    except OSError as e:
        return {"raiz": str(raiz), "items": [], "error": str(e)}

    items.sort(key=lambda d: d["mtime"], reverse=True)
    return {"raiz": str(raiz), "total": len(items), "items": items[:limite]}


def leer_artefacto(rel: str) -> dict:
    """
    Contenido listo para pintar: texto tal cual, binario como data URL.

    Las imágenes y los PDF viajan como data URL porque el iframe de la interfaz
    no puede cargar `file://` desde una página servida por pywebview. Mandarlo
    embebido evita montar un servidor de ficheros solo para esto.
    """
    if not rel:
        return {"error": "no se indicó ningún fichero"}
    try:
        p = _seguro(rel)
    except ValueError as e:
        return {"error": str(e)}
    if not p.exists() or not p.is_file():
        return {"error": "el fichero ya no está"}

    tipo = clasificar(p)
    tam = p.stat().st_size
    base = {"path": rel, "nombre": p.name, "tipo": tipo, "bytes": tam}

    if tipo in ("texto", "web"):
        if tam > MAX_TEXTO:
            return {**base, "error": f"demasiado grande para previsualizar "
                                     f"({tam:,} bytes)"}
        try:
            datos = p.read_bytes()
        except OSError as e:
            return {**base, "error": f"no se pudo leer: {e}"}
        if b"\x00" in datos[:8192]:
            return {**base, "tipo": "binario",
                    "error": "es binario pese a la extensión"}
        texto = datos.decode("utf-8", "replace")
        # CRLF -> LF: en Windows si no, cada línea del código previsualizado
        # arrastra un retorno de carro visible en el <pre>.
        return {**base, "contenido": texto.replace("\r\n", "\n")}

    if tam > MAX_BINARIO:
        return {**base, "error": f"demasiado grande para previsualizar "
                                 f"({tam:,} bytes)"}
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    try:
        crudo = p.read_bytes()
    except OSError as e:
        return {**base, "error": f"no se pudo leer: {e}"}
    return {**base, "mime": mime,
            "data_url": f"data:{mime};base64,{base64.b64encode(crudo).decode()}"}


# ------------------------------------------------------------------- RPC
#
# Los handlers viven aqui y no en el kernel desde el 13-sep-2026: el kernel
# iba 1069 lineas de un techo de 1070 y L5 no cabia. Es el mismo criterio que
# ya se aplico a Ritsuko — la superficie RPC de una pieza, al lado de la pieza.

async def rpc_listar(payload, websocket=None):
    """Ficheros que MAGI ha generado en el workspace, para la vista previa."""
    return listar_artefactos(limite=int((payload or {}).get("limite", 200)))


async def rpc_leer(payload, websocket=None):
    """Contenido de un artefacto, listo para pintarlo en la vista previa."""
    return leer_artefacto((payload or {}).get("path", ""))

