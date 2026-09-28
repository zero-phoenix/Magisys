"""
Entregables de los bloques auto-exec (megaplan v5.29, Fase 4).

La auditoría del Tetris cazó el caso: un bloque html se guardó como
.ps1 — y los .ps1 se EJECUTAN con la política de PowerShell saltada. Un
artefacto no es un script: aquí se decide qué se corre y qué solo se
guarda, con qué extensión, y se auto-chequea lo que se anuncia como
entregable.
"""
from __future__ import annotations

#: Mapa lenguaje→extensión de los bloques ARTEFACTO: se guardan con su
#: extensión y NO se ejecutan. Todo lo que no está aquí ni es
#: python/shell se trata como artefacto de texto.
EXT_ARTEFACTO = {"html": ".html", "css": ".css", "json": ".json",
                 "javascript": ".js", "js": ".js", "typescript": ".ts",
                 "markdown": ".md", "md": ".md", "svg": ".svg",
                 "xml": ".xml", "csv": ".csv"}


def parece_html(code: str) -> bool:
    """
    Auto-chequeo mínimo de entregables HTML: el stub vacío y el bloque que
    no es HTML no se anuncian como entregables. No valida el DOM — separa
    "es una página" de "es otra cosa".
    """
    bajo = code.lower()
    return ("<html" in bajo or "<!doctype" in bajo or "<canvas" in bajo)


def guardar_bloque(scratch_dir, i: int, lang: str, code: str, journal):
    """Guarda un bloque ARTEFACTO con su extensión real y sin ejecutarlo.
    Devuelve (ruta, aviso) — el aviso se pinta en la terminal, nunca en
    silencio."""
    ext = EXT_ARTEFACTO.get(lang, f".{lang}")
    ruta = scratch_dir / f"auto_script_{i}{ext}"
    journal.record(ruta, "create", tool="auto_exec")
    ruta.write_text(code, encoding="utf-8")
    aviso = (f"[ARTEFACTO] Bloque {i+1} guardado como {ruta.name} "
             f"(los bloques {lang} no se ejecutan).")
    if ext == ".html" and not parece_html(code):
        aviso += " AVISO: no parece HTML completo."
    return ruta, aviso
