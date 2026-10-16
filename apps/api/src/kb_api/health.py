"""Liveness (unauthenticated, minimal) and readiness (authenticated, detailed)."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kb_contracts.config import Settings
from pydantic import BaseModel

PROBE_TIMEOUT_S = 2.0
Probe = Callable[[], Awaitable[None]]  # raises on failure


class ComponentHealth(BaseModel):
    name: str
    status: str  # ok | down
    latency_ms: int | None = None
    detail: str | None = None
    critical: bool


class ReadinessReport(BaseModel):
    status: str  # ok | degraded | down
    components: list[ComponentHealth]


@dataclass(frozen=True)
class NamedProbe:
    name: str
    probe: Probe
    critical: bool  # critical failure => 503; non-critical => degraded (still serves)


def default_probes(s: Settings) -> list[NamedProbe]:
    dsn = s.database_url.replace("+asyncpg", "")

    async def pg() -> None:
        conn = await asyncpg.connect(dsn, timeout=2)
        try:
            await conn.fetchval("select 1")
        finally:
            await conn.close()

    async def http_ok(url: str) -> None:
        async with httpx.AsyncClient(timeout=2) as c:
            (await c.get(url)).raise_for_status()

    async def qdrant() -> None:
        await http_ok(f"{s.qdrant_url.rstrip('/')}/readyz")

    async def ollama() -> None:
        await http_ok(f"{s.ollama_url.rstrip('/')}/api/tags")

    # PostgreSQL is authoritative (critical). Qdrant is derived/rebuildable (ADR-0003) and Ollama
    # is optional at runtime, so both degrade rather than fail.
    return [
        NamedProbe("postgresql", pg, True),
        NamedProbe("qdrant", qdrant, False),
        NamedProbe("ollama", ollama, False),
    ]


async def run_probes(probes: list[NamedProbe]) -> ReadinessReport:
    async def one(p: NamedProbe) -> ComponentHealth:
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(p.probe(), timeout=PROBE_TIMEOUT_S)  # hard total bound
            return ComponentHealth(
                name=p.name,
                status="ok",
                critical=p.critical,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 - any failure means "down"; type only, no secrets
            return ComponentHealth(
                name=p.name, status="down", critical=p.critical, detail=type(exc).__name__
            )

    comps = list(await asyncio.gather(*(one(p) for p in probes)))  # concurrent, order preserved
    if any(c.status == "down" and c.critical for c in comps):
        status = "down"
    elif any(c.status == "down" for c in comps):
        status = "degraded"
    else:
        status = "ok"
    return ReadinessReport(status=status, components=comps)


public_router = APIRouter(tags=["health"])
private_router = APIRouter(tags=["health"])


@public_router.get("/health/live", operation_id="healthLive")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@private_router.get("/health/ready", response_model=ReadinessReport, operation_id="healthReady")
async def ready(request: Request) -> JSONResponse:
    report = await run_probes(request.app.state.probes)
    code = 503 if report.status == "down" else 200
    return JSONResponse(report.model_dump(), status_code=code)
