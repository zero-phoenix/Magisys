"""
Tests del backend Groq (v5.28.0) — sin red, con httpx.MockTransport.

El contrato que se prueba:
  1. `available()` sigue a la clave (entorno GROQ_API_KEY → fichero del repo),
     sin tocar la red: la salud real la mide la sonda, esto es «puede intentarse».
  2. `complete` parsea la respuesta OpenAI-compatible y los tool_calls.
  3. Un 429 (la cuota gratuita, el fallo ESPERADO) es ProviderError — para que
     el registro pueda hacer failover a g4f, no una excepción de httpx.
  4. `stream` emite deltas y un stream sin contenido se declara, no finge.
"""
from __future__ import annotations

import httpx
import pytest

from magi.core.providers.backends import groq_backend
from magi.core.providers.backends.groq_backend import (
    GroqProvider,
    build_groq_providers,
)
from magi.core.providers.base import CompletionRequest, Message, ProviderError


@pytest.fixture(autouse=True)
def sin_clave_de_fichero(monkeypatch):
    """Aísla cada test del fichero de clave del repo (env GROQ_API_KEY incluido):
    cada test DECLARA qué clave hay, heredar la de la máquina es describir la
    máquina, no probar el código (misma regla que conftest aplica al resto)."""
    monkeypatch.setattr(groq_backend, "_clave_de_fichero", lambda: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


def _provider(transport: httpx.MockTransport) -> GroqProvider:
    return GroqProvider(
        "groq-oss", "openai/gpt-oss-120b",
        client=httpx.AsyncClient(transport=transport,
                                 base_url="https://api.groq.com"))


def _req(**kw) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message("system", "s"), Message("user", "hola")], **kw)


# --------------------------------------------------------------- disponibilidad

async def test_disponible_solo_con_clave_de_entorno(monkeypatch):
    p = GroqProvider("groq-oss", "openai/gpt-oss-120b")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert not await p.available()
    monkeypatch.setenv("GROQ_API_KEY", "k-de-prueba")
    assert await p.available()


async def test_el_entorno_gana_al_fichero(monkeypatch, tmp_path):
    fichero = tmp_path / "groq_key.txt"
    fichero.write_text("k-del-fichero", encoding="utf-8")
    monkeypatch.setattr(groq_backend, "_RUTA_CLAVE", fichero)

    def _lee_fichero_real():
        texto = groq_backend._RUTA_CLAVE.read_text(encoding="utf-8").strip()
        return texto or None

    monkeypatch.setattr(groq_backend, "_clave_de_fichero", _lee_fichero_real)
    p = GroqProvider("groq-oss", "openai/gpt-oss-120b")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert p._key() == "k-del-fichero"
    monkeypatch.setenv("GROQ_API_KEY", "k-de-entorno")
    assert p._key() == "k-de-entorno", "el entorno debe ganar al fichero"


# -------------------------------------------------------------------- complete

async def test_complete_parsea_contenido_y_usage(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["auth"] = request.headers.get("Authorization")
        visto["modelo"] = __import__("json").loads(request.content)["model"]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Hola de Groq"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        })

    p = _provider(httpx.MockTransport(handler))
    resp = await p.complete(_req())
    assert resp.content == "Hola de Groq"
    assert resp.provider_id == "groq-oss"
    assert resp.family == "groq-oss"
    assert resp.usage.prompt_tokens == 7
    assert visto["auth"] == "Bearer k"
    assert visto["modelo"] == "openai/gpt-oss-120b"


async def test_complete_parsea_tool_calls(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")

    def handler(_):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "", "tool_calls": [{
                "id": "call_1",
                "function": {"name": "leer_fichero",
                             "arguments": "{\"ruta\": \"a.py\"}"},
            }]}}],
        })

    p = _provider(httpx.MockTransport(handler))
    resp = await p.complete(_req(tools=[{"type": "function"}]))
    assert resp.wants_tools
    assert resp.tool_calls[0].name == "leer_fichero"
    assert resp.tool_calls[0].arguments == {"ruta": "a.py"}


async def test_el_429_es_providererror_para_que_el_registro_falle(monkeypatch):
    """La cuota gratuita se agota a diario: ese fallo debe dejar a g4f tomar
    el relevo. Si sube una httpx.HTTPStatusError, el failover muere aquí."""
    monkeypatch.setenv("GROQ_API_KEY", "k")

    def handler(_):
        return httpx.Response(429, json={"error": {"message": "Rate limit reached"}})

    p = _provider(httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as err:
        await p.complete(_req())
    assert "429" in str(err.value)


# --------------------------------------------------------------------- stream

async def test_stream_emite_deltas(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cuerpo = (
        'data: {"choices":[{"delta":{"content":"Ho"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"la"}}]}\n\n'
        'data: [DONE]\n\n'
    )

    def handler(_):
        return httpx.Response(200, content=cuerpo.encode())

    p = _provider(httpx.MockTransport(handler))
    trozos = [d.text async for d in p.stream(_req())]
    assert trozos == ["Ho", "la"]


async def test_stream_vacio_se_declara(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")

    def handler(_):
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    p = _provider(httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        async for _ in p.stream(_req()):
            pass


# -------------------------------------------------------------- el catálogo

def test_las_familias_por_defecto_son_las_medidas():
    """El catálogo por defecto es el MEDIDO contra la cuenta real el
    2026-09-27 (los llama-3.x de la docs NO están en ella). Tres familias de
    chat, tres modelos distintos, ninguna local."""
    provs = build_groq_providers()
    assert [(p.family, p.default_model) for p in provs] == [
        ("groq-oss", "openai/gpt-oss-120b"),
        ("groq-qwen", "qwen/qwen3.8-27b"),
        ("groq-flash", "openai/gpt-oss-20b"),
    ]
    for p in provs:
        assert not p.is_local
    # Ninguna familia por defecto promete visión: esta cuenta no la tiene
    # (medido), y prometerla haría fallar la visión en el primer uso.
    assert not any(p.supports_vision for p in provs)


def test_groq_models_override_sin_codigo(monkeypatch):
    monkeypatch.setenv("GROQ_MODELS", "groq-a:model-a, groq-b:model-b")
    provs = build_groq_providers()
    assert [(p.family, p.default_model) for p in provs] == [
        ("groq-a", "model-a"), ("groq-b", "model-b")]


def test_override_mal_formado_cae_al_defecto(monkeypatch):
    monkeypatch.setenv("GROQ_MODELS", "basura-sin-dos-puntos")
    assert len(build_groq_providers()) == 3


async def test_gpt_oss_recibe_esfuerzo_de_razonamiento_bajo(monkeypatch):
    """Sin esto, un subagente gpt-oss con techo corto de tokens devuelve
    contenido VACÍO (todo se lo come el razonamiento interno). Medido."""
    import json as _json
    monkeypatch.setenv("GROQ_API_KEY", "k")
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["payload"] = _json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}]})

    p = _provider(httpx.MockTransport(handler))
    await p.complete(_req())
    assert visto["payload"]["reasoning_effort"] == "low"
    # Y el techo de tokens tiene un SUELO para gpt-oss: 16 tokens de techo
    # devolvían completion_tokens=16 y contenido '' (medido con el canario
    # real). El llamador pedía una respuesta corta, no una respuesta vacía.
    await p.complete(_req(max_tokens=16))
    assert visto["payload"]["max_tokens"] == 128
