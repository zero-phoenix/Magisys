"""
Backend Groq — el motor de los SUBAGENTES de MAGI (megaplan v5.28.0).

POR QUÉ EXISTE
==============
La regla del proyecto sigue siendo §I.3 — solo nube, jamás modelos locales —
pero el usuario ha hecho UNA excepción explícita y con nombre: su clave de
Groq. Groq corre inferencia sobre LPU: cientos de tokens por segundo con
latencias de centenares de milisegundos, un orden de magnitud por debajo de
cualquier proveedor gratuito de g4f.

REGLA DE USO (mandato 2026-09-27): Groq alimenta SOLO el registro de
subagentes — trabajos de solo lectura, aceleración (mielina), visión rápida,
síntesis de contexto. NUNCA el enjambre principal: Melchior, Balthasar,
Casper, Naoko y Ritsuko siguen debatiendo con las familias gratuitas y
diversas de g4f, porque el valor epistemológico del debate está en los sesgos
distintos, no en la velocidad.

Lo que NO cambia:
  · Sin clave, `available()` es False y este backend desaparece del reparto:
    los subagentes caen solos a g4f y el sistema sigue 100 % operativo.
    La clave es una aceleración, no un requisito.
  · Sin modelos locales: no hay tercera vía. Groq (nube) o g4f (nube).

QUÉ NO HACE ESTE FICHERO
========================
No llama a `groq` (el paquete SDK): la API es OpenAI-compatible y `httpx`
ya es dependencia. Un SDK más es una dependencia más que auditar, sin nada
a cambio.

El catálogo de modelos se puede sobreescribir con GROQ_MODELS
("familia:modelo,familia:modelo") sin tocar código: los nombres de modelo de
Groq cambian sin avisar, y un catálogo hardcodeado es un binario roto esperando
su turno. La lista de aquí abajo es el valor por defecto verificado.

LA CLAVE (decisión del propietario, 2026-09-27): se lee del entorno
GROQ_API_KEY y, si no está, de magi/data/groq_key.txt — fichero que vive en el
repositorio POR DECISIÓN EXPLÍCITA del dueño (el repo pasa a privado al cierre
de cada ciclo de trabajo). El exe publicado la lleva dentro y funciona en
cualquier PC sin configurar nada. Para rotarla: cambia el fichero (o pon la
variable de entorno, que SIEMPRE gana) y re-publica.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from ..base import (
    BaseProvider,
    CompletionRequest,
    CompletionResponse,
    Delta,
    ProviderError,
    ToolCall,
    Usage,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.groq.com/openai/v1"
API_KEY_ENV = "GROQ_API_KEY"

#: Fichero de clave del repo (viaja dentro del exe: magi/data se empaqueta).
#: `Path(__file__)` = magi/core/providers/backends/ → tres padres = magi/.
_RUTA_CLAVE = Path(__file__).resolve().parents[3] / "data" / "groq_key.txt"


def _clave_de_fichero() -> str | None:
    try:
        texto = _RUTA_CLAVE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return texto or None

#: Familias por defecto: (familia, modelo). El ORDEN es el orden de prioridad
#: con que se registran — la sonda y el mérito medido pueden reordenarlas.
#: MEDIDO el 2026-09-27 contra la cuenta real (11 modelos): estas tres son
#: las familias de CHAT distintas disponibles; los llama-3.x y el scout
#: multimodal de la documentación general de Groq NO están en esta cuenta, y
#: ponerlos aquí sería un catálogo que miente. Se sobreescribe con GROQ_MODELS.
_MODELOS_POR_DEFECTO: list[tuple[str, str]] = [
    ("groq-oss", "openai/gpt-oss-120b"),      # el razonador grande
    ("groq-qwen", "qwen/qwen3.8-27b"),        # familia distinta: diversidad
    ("groq-flash", "openai/gpt-oss-20b"),     # el pequeño instantáneo
]

#: Modelas con visión: NINGUNO en esta cuenta (medido: gpt-oss rechaza
#: content-array con 400 «content must be a string»). La visión de los
#: subagentes cae a las familias g4f con visión — que ya la tenían.
_VISION_POR_DEFECTO: set[str] = set()


def _modelos() -> list[tuple[str, str]]:
    """La lista de familias, con el override de entorno aplicado."""
    crudo = os.environ.get("GROQ_MODELS", "")
    if not crudo.strip():
        return list(_MODELOS_POR_DEFECTO)
    fuera: list[tuple[str, str]] = []
    for par in crudo.split(","):
        par = par.strip()
        if not par or ":" not in par:
            continue
        fam, modelo = par.split(":", 1)
        fuera.append((fam.strip(), modelo.strip()))
    return fuera or list(_MODELOS_POR_DEFECTO)


class GroqProvider(BaseProvider):
    """Una familia Groq = un modelo. Mira `GROQ_API_KEY` en CADA llamada,
    para que los tests y el arranque puedan activarlo sin recrear el registro."""

    supports_tools = True      # Groq sí expone tool-calling nativo.
    supports_stream = True
    is_local = False

    def __init__(self, family: str, model: str, *,
                 api_base: str = API_BASE,
                 api_key_env: str = API_KEY_ENV,
                 client: httpx.AsyncClient | None = None):
        self.family = family
        self.id = family                     # "groq-oss"; único en el registro
        self.default_model = model
        self._api_base = api_base.rstrip("/")
        self._api_key_env = api_key_env
        self._client = client                # inyección para tests (MockTransport)
        self.supports_vision = family in _VISION_POR_DEFECTO

    # ------------------------------------------------------------ infraestructura

    def _key(self) -> str | None:
        #: El entorno GANA al fichero: rotar la clave sin recompilar es
        #: `set GROQ_API_KEY=...`, y el fichero queda como el valor de repositorio.
        return os.environ.get(self._api_key_env) or _clave_de_fichero()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        key = self._key()
        if not key:
            # Sin clave no se llega aquí (available() filtra), pero un mensaje
            # claro en el fallo vale más que un 401 críptico.
            raise ProviderError(f"{self.id}: falta {self._api_key_env}")
        return {"Authorization": f"Bearer {key}",
                "Content-Type": "application/json"}

    async def available(self) -> bool:
        """Disponible ⇔ hay clave. Sin red: la salud REAL la mide la sonda
        y el cortacircuitos; aquí basta con «puede intentarse»."""
        return bool(self._key())

    def mejor_latencia_ms(self) -> float | None:
        return None    # «no lo sé» no es «es rápido»: que mida la sonda

    # ------------------------------------------------------------------ payload

    def _payload(self, req: CompletionRequest) -> dict:
        mensajes = [{"role": m.role, "content": m.content} for m in req.messages]
        p: dict = {"model": self.default_model, "messages": mensajes,
                   "temperature": req.temperature}
        if req.max_tokens is not None:
            p["max_tokens"] = req.max_tokens
        if req.seed is not None:
            p["seed"] = req.seed
        if req.tools:
            p["tools"] = req.tools
        # gpt-oss es un RAZONADOR: con el esfuerzo por defecto se come el
        # presupuesto en razonamiento y el CONTENT sale vacío (medido: 8
        # tokens de techo -> contenido ''). Para un motor de SUBAGENTES el
        # razonamiento profundo no es el trabajo: se pide el esfuerzo bajo.
        # Solo se envía a los modelos que lo admiten; a los demás un parámetro
        # desconocido les da un 400 que aquí no haría más que ruido.
        if "gpt-oss" in self.default_model:
            p["reasoning_effort"] = os.environ.get("GROQ_REASONING_EFFORT",
                                                   "low")
            # Y MIDE TAMBIÉN el techo: gpt-oss razona SIEMPRE (medido: 16
            # tokens de techo -> completion_tokens=16 y contenido ''). Un
            # techo menor que el razonamiento mínimo devuelve vacío y hace
            # fallar al llamador con un 'sin respuesta' que es del API, no
            # del prompt. El techo del LLAMADOR era sobre la RESPUESTA; el
            # razonamiento interno no es la respuesta.
            if p.get("max_tokens") is not None and p["max_tokens"] < 128:
                p["max_tokens"] = 128
        return p

    def _parsear_error(self, status: int, cuerpo: str) -> ProviderError:
        # 429 (rate limit de la capa gratuita) es el fallo ESPERADO del que
        # habla el megaplan: el registro debe poder fallover a g4f, y para eso
        # la excepción tiene que ser ProviderError, no httpx.HTTPStatusError.
        return ProviderError(f"{self.id}: HTTP {status} {cuerpo[:180]}")

    # ---------------------------------------------------------------- complete

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        import time
        started = time.monotonic()
        try:
            r = await self._get_client().post(
                f"{self._api_base}/chat/completions",
                headers=self._headers(),
                content=json.dumps(self._payload(req) | {"stream": False}),
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.id}: red — {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise self._parsear_error(r.status_code, r.text)
        datos = r.json()
        try:
            msg = datos["choices"][0]["message"]
            contenido = msg.get("content") or ""
            llamadas = [
                ToolCall(id=tc.get("id", ""),
                         name=tc["function"]["name"],
                         arguments=json.loads(tc["function"]["arguments"] or "{}"))
                for tc in msg.get("tool_calls") or []
            ]
            uso = datos.get("usage") or {}
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError(f"{self.id}: respuesta ilegible — {e}") from e
        return self._mk_response(
            contenido, self.default_model, started,
            usage=Usage(prompt_tokens=uso.get("prompt_tokens", 0),
                        completion_tokens=uso.get("completion_tokens", 0)),
            tool_calls=llamadas,
        )

    # ----------------------------------------------------------------- visión

    async def complete_vision(
            self, req: CompletionRequest, image_data_url: str
    ) -> CompletionResponse:
        """Multimodal para la familia scout (Naoko lee capturas con esto)."""
        import time
        started = time.monotonic()
        mensajes = [
            {"role": m.role if i else "user",
             "content": (
                 [{"type": "text", "text": str(m.content)},
                  {"type": "image_url", "image_url": {"url": image_data_url}}]
                 if i == 0 else m.content)}
            for i, m in enumerate(req.messages)
        ]
        try:
            r = await self._get_client().post(
                f"{self._api_base}/chat/completions",
                headers=self._headers(),
                content=json.dumps({"model": self.default_model,
                                    "messages": mensajes, "stream": False}),
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.id}: visión — {e}") from e
        if r.status_code != 200:
            raise self._parsear_error(r.status_code, r.text)
        datos = r.json()
        return self._mk_response(
            datos["choices"][0]["message"].get("content") or "",
            self.default_model, started)

    # ----------------------------------------------------------------- stream

    async def stream(self, req: CompletionRequest) -> AsyncIterator[Delta]:
        seq = 0
        try:
            async with self._get_client().stream(
                "POST",
                f"{self._api_base}/chat/completions",
                headers=self._headers(),
                content=json.dumps(self._payload(req) | {"stream": True}),
            ) as r:
                if r.status_code != 200:
                    cuerpo = (await r.aread()).decode("utf-8", "replace")
                    raise self._parsear_error(r.status_code, cuerpo)
                async for linea in r.aiter_lines():
                    if not linea.startswith("data:"):
                        continue
                    dato = linea[5:].strip()
                    if dato == "[DONE]":
                        break
                    try:
                        trozo = json.loads(dato)
                    except ValueError:
                        continue
                    texto = (trozo.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if texto:
                        yield Delta(text=texto, seq=seq, provider_id=self.id)
                        seq += 1
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.id}: stream — {e}") from e
        if seq == 0:
            # Un stream que abre y cierra sin decir nada es un fallo con forma
            # de éxito; mejor declararlo y que el registro pruebe al siguiente.
            raise ProviderError(f"{self.id}: stream sin contenido")


def build_groq_providers() -> list[GroqProvider]:
    """Una instancia por familia. Se registran SIEMPRE: sin clave, `available()`
    las deja fuera del reparto sin que nadie tenga que acordarse de quitarlas."""
    return [GroqProvider(family=fam, model=model) for fam, model in _modelos()]
