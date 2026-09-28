"""
Persistencia de telemetría de proveedores (megaplan v5.29, Fase 1).

Las medidas de latencia y los fallos por proveedor sobreviven al reinicio:
si Perplexity devolvió basura veinte veces en la sesión anterior, el kernel
recién arrancado ya lo sabe. JSON atómico en el data dir — sin tocar
magi_brain.db, cuyo esquema gestiona el store de tareas.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from magi.core import paths

logger = logging.getLogger(__name__)

_NOMBRE = "provider_stats.json"


def _ruta() -> Path:
    return paths.data_dir() / _NOMBRE


def guardar_provider_stats(stats: dict[str, dict[str, Any]]) -> None:
    """Vuelca `{proveedor: {n_ok, n_fail, p50_ema, p95_ema}}` de forma atómica."""
    try:
        ruta = _ruta()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, ruta)
    except Exception as e:  # la telemetría jamás tumba el kernel
        logger.debug("[obs] no se pudo persistir provider_stats: %s", e)


def cargar_provider_stats() -> dict[str, dict[str, Any]]:
    """Lee lo persistido; `{}` si no existe o está corrupto."""
    try:
        ruta = _ruta()
        if not ruta.is_file():
            return {}
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except Exception as e:
        logger.debug("[obs] provider_stats ilegible: %s", e)
        return {}
