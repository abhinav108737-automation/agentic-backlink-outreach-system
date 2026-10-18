"""SEC-01: localhost API security (ADR-0014). Exercises the real app via ASGI transport."""

import io
import json
import logging

import httpx
import pytest
from kb_api.app import create_app
from kb_api.health import NamedProbe
from kb_contracts.config import Settings
from kb_contracts.logging import REDACTOR, JsonFormatter

pytestmark = pytest.mark.req("SEC-01")
TOKEN = "T0ken-" + "a1b2c3d4" * 6  # 54 chars, high-entropy-looking test value
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PRIVATE = [
    ("GET", "/health/ready"),
    ("POST", "/campaigns"),
    ("GET", "/prospects"),
    ("GET", "/settings"),
    ("GET", "/approvals"),
    ("GET", "/agent-runs"),
]


async def _ok() -> None: ...


async def _boom() -> None:
    raise ConnectionError("nope")


def make_app(probes=None):
    return create_app(Settings(_env_file=None), TOKEN, probes=probes if probes is not None else [])


def client(app, host="127.0.0.1", **headers):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url=f"http://{host}",
        headers=headers,
    )


@pytest.mark.parametrize(("method", "path"), PRIVATE)
async def test_missing_token_is_401_problem_json(method, path):
    async with client(make_app()) as c:
        r = await c.request(method, path)
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 401 and r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "bad",
    [
        "Bearer wrong",
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN}x",
        f"Basic {TOKEN}",
        TOKEN,
        "Bearer ",
        f"bearer  {TOKEN}",
    ],
)
async def test_wrong_or_malformed_token_is_401(bad):
    async with client(make_app(), Authorization=bad) as c:
        r = await c.get("/health/ready")
    assert r.status_code == 401


async def test_token_in_query_string_is_not_accepted():
    async with client(make_app()) as c:
        assert (await c.get(f"/health/ready?token={TOKEN}")).status_code == 401


async def test_correct_token_is_200_and_stubs_are_501_not_open():
    probes = [NamedProbe("postgresql", _ok, True)]
    async with client(make_app(probes), **AUTH) as c:
        assert (await c.get("/health/ready")).status_code == 200
        # A route that is still a stub must say so, and must never serve data.
        assert (await c.get("/providers")).status_code == 501
        # /prospects IS implemented now, but this app has no persistence configured. It must
        # fail closed with 503 — never 200, and never 501, which would hide a misconfiguration
        # behind "not implemented".
        assert (await c.get("/prospects")).status_code == 503


async def test_live_is_public_and_minimal():
    async with client(make_app()) as c:
        r = await c.get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_token_comparison_goes_through_hmac_compare_digest(monkeypatch):
    import hmac

    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    async with client(make_app()) as c:
        await c.get("/health/ready", headers={"Authorization": "Bearer wrong"})
        await c.get("/health/ready", headers=AUTH)
    assert len(calls) == 2, "compare_digest must run for wrong AND correct tokens"
    assert calls[0][1] == TOKEN.encode()


def test_short_token_is_refused_at_construction():
    with pytest.raises(ValueError):
        create_app(Settings(_env_file=None), "short", probes=[])


@pytest.mark.parametrize(
    "host", ["evil.com", "attacker.example:8080", "127.0.0.1.evil.com", "192.168.1.5"]
)
async def test_host_allowlist_blocks_dns_rebinding(host):
    async with client(make_app(), **AUTH) as c:
        r = await c.get("/health/live", headers={"Host": host})
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:51234", "127.0.0.1"])
async def test_only_the_loopback_ip_host_is_allowed(host):
    async with client(make_app(), **AUTH) as c:
        assert (await c.get("/health/live", headers={"Host": host})).status_code == 200


async def test_any_origin_header_is_rejected_even_with_valid_token():
    async with client(make_app(), **AUTH) as c:
        r = await c.get("/health/live", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert "access-control-allow-origin" not in r.headers


async def test_cors_preflight_is_denied_without_allow_headers():
    async with client(make_app()) as c:
        r = await c.options(
            "/campaigns",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
        )
    assert r.status_code == 403
    assert not any(h.lower().startswith("access-control-") for h in r.headers)


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
async def test_no_unauthenticated_schema_or_docs_surface(path):
    async with client(make_app()) as c:
        assert (await c.get(path)).status_code in (401, 404)


async def test_correlation_id_is_propagated_generated_and_sanitized():
    async with client(make_app()) as c:
        r = await c.get("/health/live", headers={"X-Correlation-ID": "my-corr_1.2"})
        assert r.headers["x-correlation-id"] == "my-corr_1.2"
        r = await c.get("/health/live")
        assert len(r.headers["x-correlation-id"]) == 32
        r = await c.get("/health/live", headers={"X-Correlation-ID": "bad id with spaces"})
        assert " " not in r.headers["x-correlation-id"] and len(r.headers["x-correlation-id"]) == 32
        r = await c.get("/campaigns/xyz", headers={"X-Correlation-ID": "cid-9"})
    assert r.json()["correlation_id"] == "cid-9"  # also inside the problem body


async def test_unhandled_errors_are_problem_json_without_internals():
    app = make_app()

    @app.get("/boom-test")
    async def boom() -> None:
        raise RuntimeError("secret internal detail /etc/passwd")

    async with client(app, **AUTH) as c:
        r = await c.get("/boom-test")
    assert r.status_code == 500 and "passwd" not in r.text
    assert r.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    ("down", "expected_status", "http"),
    [
        ((), "ok", 200),
        (("qdrant",), "degraded", 200),
        (("ollama",), "degraded", 200),
        (("qdrant", "ollama"), "degraded", 200),
        (("postgresql",), "down", 503),
    ],
)
async def test_readiness_semantics(down, expected_status, http):
    probes = [
        NamedProbe(n, _boom if n in down else _ok, n == "postgresql")
        for n in ("postgresql", "qdrant", "ollama")
    ]
    async with client(make_app(probes), **AUTH) as c:
        r = await c.get("/health/ready")
    assert r.status_code == http and r.json()["status"] == expected_status
    assert {x["name"] for x in r.json()["components"]} == {"postgresql", "qdrant", "ollama"}
    assert "nope" not in r.text  # exception text never leaks, only the type name


async def test_token_never_appears_in_logs_even_when_a_handler_logs_it():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        app = make_app()

        @app.get("/leaky-test")
        async def leaky() -> None:
            logging.getLogger("t").error("raw token %s", TOKEN)
            logging.getLogger("t").error("hdr %s", AUTH["Authorization"])
            raise RuntimeError(f"failed with {TOKEN}")

        async with client(app, **AUTH) as c:
            await c.get("/leaky-test")
            await c.get("/prospects")
        async with client(app, Authorization="Bearer nope") as c:
            await c.get("/prospects")
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    out = stream.getvalue()
    assert out, "expected some log output"
    assert TOKEN not in out
    for line in out.splitlines():
        json.loads(line)  # every line is valid structured JSON


def test_settings_repr_does_not_expose_token():
    s = Settings(_env_file=None, api_token=TOKEN)
    assert TOKEN not in repr(s) and TOKEN not in str(s.model_dump())


async def test_a_hanging_probe_cannot_stall_readiness():
    import asyncio
    import time

    async def hang() -> None:
        await asyncio.sleep(30)

    probes = [
        NamedProbe("postgresql", _ok, True),
        NamedProbe("qdrant", hang, False),
        NamedProbe("ollama", hang, False),
    ]
    async with client(make_app(probes), **AUTH) as c:
        t0 = time.perf_counter()
        r = await c.get("/health/ready")
        elapsed = time.perf_counter() - t0
    assert elapsed < 4, "probes must be time-bounded and concurrent (2s each, in parallel)"
    assert r.status_code == 200 and r.json()["status"] == "degraded"
    assert {c["name"] for c in r.json()["components"] if c["status"] == "down"} == {
        "qdrant",
        "ollama",
    }


def test_registered_secret_is_scrubbed_from_arbitrary_text():
    make_app()
    assert TOKEN not in REDACTOR.scrub_text(f"x {TOKEN} y")
