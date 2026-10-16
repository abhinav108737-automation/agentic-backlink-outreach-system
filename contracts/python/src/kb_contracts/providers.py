"""C5: provider protocols and the shared error taxonomy (docs/14, ADR-0004/0015/0017)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from kb_contracts.domain import DTO


class ProviderError(Exception):
    """Base of the typed error taxonomy. Providers raise only these."""


class ProviderUnavailable(ProviderError): ...


class RateLimited(ProviderError): ...


class AuthRequired(ProviderError): ...


class InvalidResponse(ProviderError): ...


class NotConfigured(ProviderError): ...


class ProviderHealth(DTO):
    name: str
    status: str = Field(pattern="^(ok|degraded|down|disabled)$")
    latency_ms: int | None = None
    detail: str | None = None


@runtime_checkable
class Provider(Protocol):
    name: str
    is_paid: bool  # a paid provider must never be enabled silently

    async def health(self) -> ProviderHealth: ...


class SearchResult(DTO):
    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0
    query: str = ""


class FetchedPage(DTO):
    url: str
    final_url: str
    status: int
    html: str = ""
    text: str = ""
    fetched_at: datetime
    robots_allowed: bool = True


class SEOMetric(DTO):
    domain: str
    metric: str
    value: float | None  # None = unavailable; never fabricate (ADR-0015)
    source: str
    fetched_at: datetime


class OutboundEmail(DTO):
    idempotency_key: str
    to: str
    subject: str
    body: str
    thread_ref: str | None = None


class SendReceipt(DTO):
    provider_message_id: str
    idempotency_key: str
    delivery_mode: str
    duplicate: bool = False


class InboundEmail(DTO):
    provider_message_id: str
    thread_ref: str | None = None
    from_address: str
    received_at: datetime
    subject: str = ""
    body: str = ""


class ModelRequest(DTO):
    system: str
    prompt: str
    output_schema: dict[str, Any] | None = None
    model: str | None = None


class ModelResponse(DTO):
    text: str
    structured: dict[str, Any] | None = None
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


class VectorPoint(DTO):
    id: str
    vector: tuple[float, ...]
    payload: dict[str, Any]  # must include pg_id, embedding_model, model_version, content_hash


class ModelProvider(Provider, Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class EmbeddingProvider(Provider, Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorProvider(Provider, Protocol):
    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None: ...
    async def search(
        self, collection: str, vector: Sequence[float], limit: int
    ) -> list[VectorPoint]: ...
    async def delete_collection(self, collection: str) -> None: ...


class SearchProvider(Provider, Protocol):
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...


class CrawlerProvider(Provider, Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


class BrowserProvider(Provider, Protocol):
    async def render(self, url: str) -> FetchedPage: ...


class SEOMetricsProvider(Provider, Protocol):
    async def get_metrics(self, domain: str, metrics: Sequence[str]) -> list[SEOMetric]: ...


class EmailProvider(Provider, Protocol):
    async def send(self, message: OutboundEmail) -> SendReceipt: ...  # idempotent by key
    async def fetch_replies(self, since: datetime | None = None) -> list[InboundEmail]: ...


class AnalyticsSourceProvider(Provider, Protocol):
    async def fetch_outcomes(self, start: datetime, end: datetime) -> list[dict[str, Any]]: ...


class ReportExporter(Provider, Protocol):
    async def export(self, payload: dict[str, Any], destination: str) -> str: ...


class SecretStore(Protocol):
    def get(self, ref: str) -> str | None: ...
    def set(self, ref: str, value: str) -> None: ...


class JobQueue(Protocol):
    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        run_at: datetime,
        idempotency_key: str,
        concurrency_key: str | None = None,
    ) -> str: ...
    async def cancel(self, idempotency_key: str) -> bool: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
