import json

import httpx
import pytest
from kb_contracts.config import Settings
from kb_contracts.providers import (
    AuthRequired,
    EmbeddingProvider,
    InvalidResponse,
    ModelProvider,
    ModelRequest,
    ProviderUnavailable,
    RateLimited,
)
from kb_providers.embedding import FakeEmbeddingProvider, OllamaEmbeddingProvider
from kb_providers.model import OllamaModelProvider


def _model(handler: httpx.MockTransport | None = None, **kw: object) -> OllamaModelProvider:
    client = httpx.AsyncClient(transport=handler) if handler else None
    return OllamaModelProvider(client=client, **kw)  # type: ignore[arg-type]


def _transport(status: int = 200, body: object = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle)


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


REQ = ModelRequest(system="s", prompt="p", output_schema={"type": "object", "title": "T"})


def test_defaults_come_from_settings_and_are_free() -> None:
    s = Settings()
    p = OllamaModelProvider.from_settings(s)
    e = OllamaEmbeddingProvider.from_settings(s)
    assert p.model == s.reasoning_model and e.model == s.embedding_model
    assert p.is_paid is False and e.is_paid is False
    assert isinstance(p, ModelProvider) and isinstance(e, EmbeddingProvider)


async def test_unreachable_is_provider_unavailable_and_health_down() -> None:
    p = _model(httpx.MockTransport(_refuse))
    with pytest.raises(ProviderUnavailable):
        await p.generate(REQ)
    assert (await p.health()).status == "down"


async def test_structured_success_parses_and_counts_tokens() -> None:
    body = {
        "model": "m",
        "message": {"content": '{"a": 1}'},
        "prompt_eval_count": 3,
        "eval_count": 5,
    }
    sent: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=body)

    r = await _model(httpx.MockTransport(handle)).generate(REQ)
    assert r.structured == {"a": 1} and r.tokens_in == 3 and r.tokens_out == 5
    assert sent["format"] == REQ.output_schema and sent["stream"] is False


async def test_non_json_content_leaves_structured_unset() -> None:
    body = {"message": {"content": "sorry, plain words"}}
    r = await _model(_transport(200, body)).generate(REQ)
    assert r.structured is None and r.text == "sorry, plain words"


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (500, ProviderUnavailable),
        (429, RateLimited),
        (401, AuthRequired),
        (404, InvalidResponse),
        (200, InvalidResponse),  # missing message
    ],
)
async def test_http_errors_map_to_taxonomy(status: int, exc: type[Exception]) -> None:
    with pytest.raises(exc):
        await _model(_transport(status, {"oops": True})).generate(REQ)


async def test_health_ok_and_degraded() -> None:
    assert (await _model(_transport(200, {})).health()).status == "ok"
    assert (await _model(_transport(503, {})).health()).status == "degraded"


async def test_embedding_ollama_paths() -> None:
    ok = OllamaEmbeddingProvider(
        client=httpx.AsyncClient(transport=_transport(200, {"embeddings": [[1, 2]]}))
    )
    assert await ok.embed(["a"]) == [[1.0, 2.0]]
    bad = OllamaEmbeddingProvider(
        client=httpx.AsyncClient(transport=_transport(200, {"embeddings": []}))
    )
    with pytest.raises(InvalidResponse):
        await bad.embed(["a"])
    down = OllamaEmbeddingProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(_refuse)))
    with pytest.raises(ProviderUnavailable):
        await down.embed(["a"])
    assert (await down.health()).status == "down"


async def test_fake_embedding_deterministic() -> None:
    p = FakeEmbeddingProvider()
    a, b = await p.embed(["alpha beta"]), await p.embed(["alpha beta"])
    assert a == b and len(a[0]) == 32
    with pytest.raises(ProviderUnavailable):
        await FakeEmbeddingProvider(unavailable=True).embed(["x"])
