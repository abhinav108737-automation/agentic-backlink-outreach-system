"""``MockEmailProvider``: the only email provider in the default configuration (C5 + C12).

Nothing here opens a socket, speaks SMTP or IMAP, or needs credentials. ``send`` records the
message in memory and returns a receipt whose ``delivery_mode`` is always ``"mock"``.

Idempotency (the contract): ``send`` is idempotent by ``OutboundEmail.idempotency_key``. A repeat
send with the same key and the same payload returns the *original* receipt with
``duplicate=True`` and delivers nothing a second time. Reusing a key for a different payload is a
caller bug and raises ``InvalidResponse`` rather than silently dropping the second message.

Reply simulation (C12): ``fixtures/email/scenarios/<id>.json`` describe a reply that appears
``after_seconds`` after the n-th message sent to a scripted recipient (``trigger.sequence_step``
is that n). The reply is derived from the send time, not from when the mailbox is polled, so
polling twice yields the same reply (the ingestion layer must dedupe on ``provider_message_id``).
``{recipient}`` and ``{thread_ref}`` in a scenario reply are substituted textually.

Failure routes: ``simulate=<ProviderError subclass>`` makes ``send``/``fetch_replies`` raise that
taxonomy error, optionally only for the first ``simulate_times`` calls so retry paths are testable.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kb_contracts.domain import DTO
from kb_contracts.enums import DeliveryMode, ReplyClass
from kb_contracts.providers import (
    Clock,
    InboundEmail,
    InvalidResponse,
    NotConfigured,
    OutboundEmail,
    ProviderError,
    ProviderHealth,
    SendReceipt,
)
from pydantic import Field, ValidationError

from kb_providers.fixtures import load_json_object, require_dir, resolve_root

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ScenarioTrigger(DTO):
    sequence_step: int = Field(ge=1)


class ReplyScenario(DTO):
    """Frozen C12 reply-scenario format (``labels`` is the ADR-0020 marker on injection files)."""

    trigger: ScenarioTrigger
    after_seconds: int = Field(ge=0)
    reply: InboundEmail
    expected_class: ReplyClass
    labels: dict[str, bool] = Field(default_factory=dict)


@dataclass(frozen=True)
class RecordedDelivery:
    message: OutboundEmail
    receipt: SendReceipt
    sent_at: datetime
    ordinal: int  # 1-based count of distinct messages sent to this recipient


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _fingerprint(message: OutboundEmail) -> str:
    raw = "\x1f".join((message.to.strip().lower(), message.subject, message.body))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MockEmailProvider:
    """Fixture-backed, in-memory :class:`kb_contracts.providers.EmailProvider`."""

    name = "mock_email"
    is_paid = False

    def __init__(
        self,
        fixture_root: Path | str | None = None,
        *,
        clock: Clock | None = None,
        simulate: type[ProviderError] | None = None,
        simulate_times: int | None = None,
        reply_scenarios: Mapping[str, str] | None = None,
    ) -> None:
        self._root = resolve_root(fixture_root)
        self._clock: Clock = clock or _SystemClock()
        self._simulate = simulate
        self._simulate_left = simulate_times
        self._scripts: dict[str, str] = {}
        self._by_key: dict[str, RecordedDelivery] = {}
        self._fingerprints: dict[str, str] = {}
        self._deliveries: list[RecordedDelivery] = []
        for recipient, scenario_id in (reply_scenarios or {}).items():
            self.script_reply(recipient, scenario_id)

    # -- observation (tests and reconciliation) -----------------------------------------------
    @property
    def deliveries(self) -> tuple[RecordedDelivery, ...]:
        """Every distinct message actually delivered, in order. Duplicates never appear."""
        return tuple(self._deliveries)

    @property
    def scenarios_dir(self) -> Path:
        return self._root / "email" / "scenarios"

    def lookup_sent(self, idempotency_key: str) -> SendReceipt | None:
        """Was this key already delivered? Used to reconcile a message found in SENDING after a
        crash, before any retry. Returns the original receipt (``duplicate`` False) or None."""
        found = self._by_key.get(idempotency_key)
        return found.receipt if found else None

    # -- scripting ------------------------------------------------------------------------------
    def script_reply(self, recipient: str, scenario_id: str) -> None:
        """Make ``recipient`` answer per ``fixtures/email/scenarios/<scenario_id>.json``."""
        self.load_scenario(scenario_id)  # fail fast on an unknown or malformed scenario
        self._scripts[recipient.strip().lower()] = scenario_id

    def list_scenarios(self) -> list[str]:
        directory = require_dir(self.scenarios_dir, "email scenario directory")
        return sorted(p.stem for p in directory.glob("*.json"))

    def load_scenario(self, scenario_id: str) -> ReplyScenario:
        if not _ID.match(scenario_id):
            raise InvalidResponse(f"invalid scenario id {scenario_id!r}")
        path = require_dir(self.scenarios_dir, "email scenario directory") / f"{scenario_id}.json"
        if not path.is_file():
            raise NotConfigured(f"no reply scenario {scenario_id!r} at {path}")
        try:
            return ReplyScenario.model_validate(load_json_object(path))
        except ValidationError as exc:
            raise InvalidResponse(f"scenario {scenario_id!r} violates C12: {exc}") from exc

    # -- Provider -------------------------------------------------------------------------------
    async def health(self) -> ProviderHealth:
        if self._simulate is not None and self._simulate_left is None:
            return ProviderHealth(
                name=self.name, status="down", detail=f"simulated {self._simulate.__name__}"
            )
        started = time.perf_counter()
        return ProviderHealth(
            name=self.name,
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
            detail="mock delivery only; nothing leaves this process",
        )

    def _maybe_fail(self) -> None:
        if self._simulate is None:
            return
        if self._simulate_left is not None:
            if self._simulate_left <= 0:
                return
            self._simulate_left -= 1
        raise self._simulate(f"simulated failure for {self.name}")

    # -- EmailProvider --------------------------------------------------------------------------
    async def send(self, message: OutboundEmail) -> SendReceipt:
        self._maybe_fail()
        if not message.idempotency_key.strip():
            raise InvalidResponse("idempotency_key must not be empty")
        if not _EMAIL.match(message.to.strip()):
            raise InvalidResponse(f"not a deliverable address: {message.to!r}")
        if not message.subject.strip() or not message.body.strip():
            raise InvalidResponse("subject and body must not be empty")
        prior = self._by_key.get(message.idempotency_key)
        if prior is not None:
            if self._fingerprints[message.idempotency_key] != _fingerprint(message):
                raise InvalidResponse("idempotency key reused with a different payload")
            return prior.receipt.model_copy(update={"duplicate": True})
        digest = hashlib.sha256(message.idempotency_key.encode("utf-8")).hexdigest()[:16]
        receipt = SendReceipt(
            provider_message_id=f"mock-{digest}",
            idempotency_key=message.idempotency_key,
            delivery_mode=DeliveryMode.MOCK.value,
            duplicate=False,
        )
        recipient = message.to.strip().lower()
        ordinal = 1 + sum(1 for d in self._deliveries if d.message.to.strip().lower() == recipient)
        record = RecordedDelivery(message, receipt, self._clock.now(), ordinal)
        self._by_key[message.idempotency_key] = record
        self._fingerprints[message.idempotency_key] = _fingerprint(message)
        self._deliveries.append(record)
        return receipt

    async def fetch_replies(self, since: datetime | None = None) -> list[InboundEmail]:
        self._maybe_fail()
        now = self._clock.now()
        replies: list[InboundEmail] = []
        for record in self._deliveries:
            recipient = record.message.to.strip().lower()
            scenario_id = self._scripts.get(recipient)
            if scenario_id is None:
                continue
            scenario = self.load_scenario(scenario_id)
            if scenario.trigger.sequence_step != record.ordinal:
                continue
            received = record.sent_at + timedelta(seconds=scenario.after_seconds)
            if received > now or (since is not None and received <= since):
                continue
            replies.append(self._materialise(scenario, record, received))
        return sorted(replies, key=lambda r: (r.received_at, r.provider_message_id))

    @staticmethod
    def _materialise(
        scenario: ReplyScenario, record: RecordedDelivery, received: datetime
    ) -> InboundEmail:
        recipient = record.message.to.strip().lower()
        thread_ref = record.message.thread_ref or ""

        def fill(text: str) -> str:
            return text.replace("{recipient}", recipient).replace("{thread_ref}", thread_ref)

        base = scenario.reply
        suffix = record.receipt.provider_message_id.removeprefix("mock-")[:8]
        return InboundEmail(
            provider_message_id=f"{base.provider_message_id}.{suffix}",
            thread_ref=fill(base.thread_ref) if base.thread_ref else None,
            from_address=fill(base.from_address),
            received_at=received,
            subject=fill(base.subject),
            body=fill(base.body),
        )


def load_thread(thread_id: str, fixture_root: Path | str | None = None) -> dict[str, Any]:
    """Load ``fixtures/email/threads/<id>.json`` and type its messages.

    Returns ``{"thread_ref": str, "messages": [InboundEmail | OutboundEmail, ...]}``; a message
    with ``from_address`` is inbound, one with ``to`` is outbound.
    """
    if not _ID.match(thread_id):
        raise InvalidResponse(f"invalid thread id {thread_id!r}")
    path = resolve_root(fixture_root) / "email" / "threads" / f"{thread_id}.json"
    if not path.is_file():
        raise NotConfigured(f"no email thread fixture {thread_id!r} at {path}")
    data = load_json_object(path)
    raw = data.get("messages")
    if not isinstance(raw, list) or not isinstance(data.get("thread_ref"), str):
        raise InvalidResponse(f"{path} needs 'thread_ref' and a 'messages' list")
    messages: list[InboundEmail | OutboundEmail] = []
    try:
        for item in raw:
            if not isinstance(item, dict):
                raise InvalidResponse(f"{path}: every message must be an object")
            messages.append(
                InboundEmail.model_validate(item)
                if "from_address" in item
                else OutboundEmail.model_validate(item)
            )
    except ValidationError as exc:
        raise InvalidResponse(f"{path} violates C12: {exc}") from exc
    return {"thread_ref": data["thread_ref"], "messages": messages}
