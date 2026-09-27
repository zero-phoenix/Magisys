"""
OJOS DE LILIM — Percepción visual y documental avanzada estilo Google Lens (megaplan v13).

QUÉ HACE
========
Convierte a Lilim en un analizador visual de alta fidelidad:
  1. IMÁGENES ESCANEADAS: PNG, JPEG, WEBP, BMP, TIFF.
  2. DOCUMENTOS PDF ESCANEADOS: Rasterización a alta resolución (DPI configurable),
     extracción de bloques de texto, tablas, sellos, firmas y metadatos.
  3. VISION PROFUNDA POR NUBE DE SUBAGENTES (Groq scout con GROQ_API_KEY;
     g4f sin clave): lee el texto incrustado y somete las páginas escaneadas
     a visión para extraer tablas, formularios, notas manuscritas o diagramas.
     Hasta v5.27.1 esto lo hacía un VLM local (KoboldCpp): prohibido los
     modelos locales desde el mandato de 2026-09-27.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rapida import hechos_de_imagen

logger = logging.getLogger(__name__)


@dataclass
class ResultadoLens:
    """Resultado estructurado de análisis visual/documental."""
    tipo: str                          # "pdf" | "imagen"
    ruta: str
    total_paginas: int = 1
    dimensiones: list[int] = field(default_factory=list)
    texto_crudo: str = ""
    bloques_layout: list[dict[str, Any]] = field(default_factory=list)
    analisis_vlm: str = ""
    tablas_detectadas: list[str] = field(default_factory=list)
    entidades: dict[str, list[str]] = field(default_factory=dict)

    def a_dict(self) -> dict[str, Any]:
        return {
            "tipo": self.tipo,
            "ruta": self.ruta,
            "total_paginas": self.total_paginas,
            "dimensiones": self.dimensiones,
            "texto_longitud": len(self.texto_crudo),
            "bloques_count": len(self.bloques_layout),
            "analisis_vlm": self.analisis_vlm,
            "tablas_detectadas": self.tablas_detectadas,
            "entidades": self.entidades,
        }


def rasterizar_pagina_pdf(pdf_path: str | Path, num_pagina: int = 0, dpi: int = 150) -> bytes | None:
    """
    Rasteriza una página de un documento PDF a formato PNG en bytes.
    Requiere PyMuPDF (fitz). Si no está disponible, degrada a None.
    """
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        if num_pagina >= len(doc):
            return None
        pagina = doc[num_pagina]
        zoom = dpi / 72.0
        matriz = fitz.Matrix(zoom, zoom)
        pix = pagina.get_pixmap(matrix=matriz, alpha=False)
        return pix.tobytes(output="png")
    except Exception as err:
        logger.debug("No se pudo rasterizar página %d de %s: %s", num_pagina, pdf_path, err)
        return None


def extraer_texto_y_layout_pdf(pdf_path: str | Path, max_paginas: int = 10) -> tuple[str, list[dict[str, Any]], int]:
    """
    Extrae texto y bloques espaciales (x0, y0, x1, y1) de un archivo PDF.
    """
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        texto_total: list[str] = []
        bloques_totales: list[dict[str, Any]] = []

        limite = min(len(doc), max_paginas)
        for i in range(limite):
            p = doc[i]
            t = p.get_text()
            if t.strip():
                texto_total.append(f"--- PÁGINA {i+1} ---\n{t.strip()}")

            bloques = p.get_text("blocks")
            for b in bloques:
                # b = (x0, y0, x1, y1, texto, block_no, block_type)
                if len(b) >= 5 and str(b[4]).strip():
                    bloques_totales.append({
                        "pagina": i + 1,
                        "bbox": [round(float(coord), 1) for coord in b[:4]],
                        "texto": str(b[4]).strip(),
                    })

        return "\n\n".join(texto_total), bloques_totales, len(doc)
    except ImportError:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            texto_total = []
            limite = min(len(reader.pages), max_paginas)
            for i in range(limite):
                t = reader.pages[i].extract_text() or ""
                if t.strip():
                    texto_total.append(f"--- PÁGINA {i+1} ---\n{t.strip()}")
            return "\n\n".join(texto_total), [], len(reader.pages)
        except Exception as err2:
            logger.debug("Fallback pypdf falló en %s: %s", pdf_path, err2)
            return "", [], 1
    except Exception as err:
        logger.debug("Extracción directa de texto falló en %s: %s", pdf_path, err)
        return "", [], 1


async def _vlm_nube(prompt: str, datos_png: bytes) -> str:
    """Visión por el registro de SUBAGENTES (Groq scout → g4f). Cadena vacía
    si no hay motor: el llamador deja el análisis VLM en blanco y el
    resultado determinista (texto/layout) sigue valiendo por sí mismo."""
    from ...core.providers.cloud import FreeCloudLLM, get_subagent_registry
    url = "data:image/png;base64," + base64.b64encode(datos_png).decode("ascii")
    try:
        desc, motor = await FreeCloudLLM(
            registry=await get_subagent_registry()).generate_vision(
            "Analiza la imagen como un escáner de precisión.", prompt, url)
    except Exception as e:  # noqa: BLE001 — degrada sin romper el análisis
        logger.debug("[ojos] visión de subagentes no disponible: %s", e)
        return ""
    return desc if motor and not motor.startswith("SYSTEM") else ""


async def analizar_documento_escaneado(
    ruta_archivo: str | Path,
    instruccion: str = "Extrae todo el texto visible, tablas, firmas y datos clave con precisión de Google Lens",
) -> ResultadoLens:
    """
    Función de percepción visual de alta gama para imágenes o PDFs escaneados.
    Análisis determinista de capas + visión de nube de subagentes si hay motor.
    """
    p = Path(ruta_archivo)
    if not p.exists():
        return ResultadoLens(tipo="error", ruta=str(p), analisis_vlm="El archivo no existe")

    extension = p.suffix.lower()
    if extension == ".pdf":
        texto, bloques, total_pags = extraer_texto_y_layout_pdf(p)
        resultado = ResultadoLens(
            tipo="pdf",
            ruta=str(p),
            total_paginas=total_pags,
            texto_crudo=texto,
            bloques_layout=bloques,
        )

        # Si el PDF no tiene texto digital (es escaneado puro): la página
        # rasterizada pasa por la visión de subagentes.
        if not texto.strip() and total_pags > 0:
            png_bytes = rasterizar_pagina_pdf(p, num_pagina=0, dpi=150)
            if png_bytes:
                prompt = (
                    f"Actúa como un analizador visual de documentos estilo Google Lens.\n"
                    f"Instrucción: {instruccion}\n"
                    f"Analiza la siguiente página escaneada. Identifica el título, remitente/autor, "
                    f"fechas, importes, tablas de datos y sellos o firmas presentes."
                )
                resultado.analisis_vlm = await _vlm_nube(prompt, png_bytes)
        return resultado

    # Si es imagen (PNG, JPG, BMP, etc.)
    hechos = hechos_de_imagen(p)
    resultado = ResultadoLens(
        tipo="imagen",
        ruta=str(p),
        total_paginas=1,
        dimensiones=hechos.get("dimensiones", []),
    )

    try:
        datos = p.read_bytes()
    except OSError:
        datos = b""
    if datos:
        prompt = (
            f"Como escáner OCR y clasificador visual avanzado (Google Lens):\n"
            f"Instrucción: {instruccion}\n"
            f"Transcribe con exactitud el texto de la imagen, preservando tablas, listas y datos relevantes."
        )
        vlm_resp = await _vlm_nube(prompt, datos)
        if vlm_resp:
            resultado.analisis_vlm = vlm_resp
            resultado.texto_crudo = vlm_resp

    return resultado
