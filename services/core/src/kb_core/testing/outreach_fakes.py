"""In-memory outreach repositories (sequences, messages, provider accounts, replies, ...) layered
over `fakes.FakeStore` without editing it. Test support only.

`OutreachUnitOfWork` snapshots the extra tables on entry and restores them on an exception, so
atomicity tests over the outreach machines exercise real rollback semantics.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

from kb_contracts.domain import (
    Backlink,
    GuestPostOpportunity,
    OutreachMessage,
    OutreachSequence,
    Reply,
)
from kb_contracts.enums import (
    BacklinkStatus,
    MessageStatus,
    OpportunityStatus,
    SequenceStatus,
)

from kb_core.db.protocols import OptimisticLockError
from kb_core.testing.fakes import FakeStore, FakeUnitOfWork, _page, _Repo

_TERMINAL_MESSAGE = frozenset(
    {
        MessageStatus.DELIVERED,
        MessageStatus.BOUNCED,
        MessageStatus.FAILED,
        MessageStatus.REJECTED,
        MessageStatus.CANCELLED,
        MessageStatus.POLICY_BLOCKED,
    }
)
_SENT = frozenset({MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED})


@dataclass
class OutreachStore(FakeStore):
    sequences: dict[UUID, OutreachSequence] = field(default_factory=dict)
    messages: dict[UUID, OutreachMessage] = field(default_factory=dict)
    provider_ids: dict[UUID, str] = field(default_factory=dict)
    attempts: dict[UUID, int] = field(default_factory=dict)
    threads: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    replies: dict[UUID, Reply] = field(default_factory=dict)
    opportunities: dict[UUID, GuestPostOpportunity] = field(default_factory=dict)
    backlinks: dict[UUID, Backlink] = field(default_factory=dict)
    provider_accounts: dict[UUID, dict[str, Any]] = field(default_factory=dict)


_EXTRA = tuple(
    f.name for f in fields(OutreachStore) if f.name not in {x.name for x in fields(FakeStore)}
)


class FakeSequences(_Repo):
    s: OutreachStore

    async def get(self, sequence_id: UUID) -> OutreachSequence | None:
        return self.s.sequences.get(sequence_id)

    async def create(
        self,
        *,
        campaign_id: UUID,
        prospect_id: UUID,
        contact_channel_id: UUID,
        policy_version_id: UUID | None = None,
    ) -> OutreachSequence:
        now = self.s.now()
        seq = OutreachSequence(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            campaign_id=campaign_id,
            prospect_id=prospect_id,
            contact_channel_id=contact_channel_id,
            status=SequenceStatus.PLANNED,
            policy_version_id=policy_version_id,
        )
        self.s.sequences[seq.id] = seq
        return seq

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        status: SequenceStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Any:
        rows = [
            r
            for r in self.s.sequences.values()
            if (campaign_id is None or r.campaign_id == campaign_id)
            and (status is None or r.status is status)
        ]
        return _page(rows, cursor, limit)

    async def set_status(
        self, sequence_id: UUID, status: SequenceStatus, *, expected_version: int
    ) -> OutreachSequence:
        cur = self.s.sequences.get(sequence_id)
        if cur is None:
            raise LookupError(f"outreach_sequences {sequence_id} not found")
        if cur.version != expected_version:
            raise OptimisticLockError("outreach_sequences", sequence_id, expected_version)
        new = cur.model_copy(
            update={"status": status, "version": cur.version + 1, "updated_at": self.s.now()}
        )
        self.s.sequences[sequence_id] = new
        return new

    async def set_next_due(self, sequence_id: UUID, *, run_at: datetime | None) -> None:
        cur = self.s.sequences[sequence_id]
        self.s.sequences[sequence_id] = cur.model_copy(update={"next_due_at": run_at})

    async def advance_step(self, sequence_id: UUID) -> int:
        cur = self.s.sequences[sequence_id]
        new = cur.model_copy(update={"current_step": cur.current_step + 1})
        self.s.sequences[sequence_id] = new
        return new.current_step


class FakeMessages(_Repo):
    s: OutreachStore

    async def get(self, message_id: UUID) -> OutreachMessage | None:
        return self.s.messages.get(message_id)

    async def create(
        self,
        *,
        sequence_id: UUID,
        step_no: int,
        subject: str,
        body: str,
        idempotency_key: str,
        claims: dict[str, Any] | None = None,
        thread_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        message_version: int = 1,
        delivery_mode: str = "mock",
    ) -> OutreachMessage:
        for m in self.s.messages.values():
            if m.idempotency_key == idempotency_key:
                raise ValueError("duplicate idempotency_key")
            if (
                m.sequence_id == sequence_id
                and m.step_no == step_no
                and (m.message_version == message_version or m.status not in _TERMINAL_MESSAGE)
            ):
                raise ValueError("one live message per step")
        now = self.s.now()
        msg = OutreachMessage(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            sequence_id=sequence_id,
            thread_id=thread_id,
            step_no=step_no,
            message_version=message_version,
            status=MessageStatus.DRAFT,
            subject=subject,
            body=body,
            delivery_mode=delivery_mode,
            idempotency_key=idempotency_key,
            claims={k: tuple(v) for k, v in (claims or {}).items()},
        )
        self.s.messages[msg.id] = msg
        return msg

    async def get_by_idempotency_key(self, key: str) -> OutreachMessage | None:
        return next((m for m in self.s.messages.values() if m.idempotency_key == key), None)

    async def live_for_step(self, sequence_id: UUID, step_no: int) -> OutreachMessage | None:
        live = [
            m
            for m in self.s.messages.values()
            if m.sequence_id == sequence_id
            and m.step_no == step_no
            and m.status not in _TERMINAL_MESSAGE
        ]
        return max(live, key=lambda m: m.message_version) if live else None

    async def live_for_channel(
        self, campaign_id: UUID, contact_channel_id: UUID
    ) -> Sequence[OutreachMessage]:
        seqs = {
            q.id
            for q in self.s.sequences.values()
            if q.campaign_id == campaign_id and q.contact_channel_id == contact_channel_id
        }
        return [
            m
            for m in self.s.messages.values()
            if m.sequence_id in seqs and m.status not in _TERMINAL_MESSAGE
        ]

    async def list_for_sequence(self, sequence_id: UUID) -> Sequence[OutreachMessage]:
        rows = [m for m in self.s.messages.values() if m.sequence_id == sequence_id]
        return sorted(rows, key=lambda m: (m.step_no, m.message_version))

    async def count_sent_since(
        self,
        *,
        since: datetime,
        campaign_id: UUID | None = None,
        domain_id: UUID | None = None,
        contact_channel_id: UUID | None = None,
    ) -> int:
        n = 0
        for m in self.s.messages.values():
            if m.status not in _SENT or m.updated_at < since:
                continue
            q = self.s.sequences[m.sequence_id]
            if campaign_id is not None and q.campaign_id != campaign_id:
                continue
            if contact_channel_id is not None and q.contact_channel_id != contact_channel_id:
                continue
            if domain_id is not None and self.s.prospects[q.prospect_id].domain_id != domain_id:
                continue
            n += 1
        return n

    async def set_status(
        self, message_id: UUID, status: MessageStatus, *, expected_version: int
    ) -> OutreachMessage:
        cur = self.s.messages.get(message_id)
        if cur is None:
            raise LookupError(f"outreach_messages {message_id} not found")
        if cur.version != expected_version:
            raise OptimisticLockError("outreach_messages", message_id, expected_version)
        new = cur.model_copy(
            update={"status": status, "version": cur.version + 1, "updated_at": self.s.now()}
        )
        self.s.messages[message_id] = new
        return new

    async def record_qa(self, message_id: UUID, qa_result: dict[str, Any]) -> None:
        cur = self.s.messages[message_id]
        self.s.messages[message_id] = cur.model_copy(update={"qa_result": qa_result})

    async def mark_sent(
        self, message_id: UUID, *, provider_message_id: str, expected_version: int
    ) -> OutreachMessage:
        new = await self.set_status(
            message_id, MessageStatus.SENT, expected_version=expected_version
        )
        self.s.provider_ids[message_id] = provider_message_id
        return new

    async def bump_attempts(self, message_id: UUID) -> int:
        self.s.attempts[message_id] = self.s.attempts.get(message_id, 0) + 1
        return self.s.attempts[message_id]


class FakeThreads(_Repo):
    s: OutreachStore

    async def get(self, thread_id: UUID) -> dict[str, Any] | None:
        return self.s.threads.get(thread_id)

    async def get_or_create_for_sequence(
        self, sequence_id: UUID, *, subject: str, provider_thread_id: str | None = None
    ) -> dict[str, Any]:
        for th in self.s.threads.values():
            if th["sequence_id"] == sequence_id:
                return th
        th = {
            "id": uuid4(),
            "sequence_id": sequence_id,
            "subject": subject,
            "provider_thread_id": provider_thread_id,
        }
        self.s.threads[th["id"]] = th
        return th

    async def list_replies(self, thread_id: UUID) -> Sequence[Reply]:
        return [r for r in self.s.replies.values() if getattr(r, "thread_id", None) == thread_id]


class FakeReplies(_Repo):
    s: OutreachStore

    async def get(self, reply_id: UUID) -> Reply | None:
        return self.s.replies.get(reply_id)

    async def list_for_sequence(self, sequence_id: UUID) -> Sequence[Reply]:
        return [r for r in self.s.replies.values() if r.sequence_id == sequence_id]


class FakeOpportunities(_Repo):
    s: OutreachStore

    async def get(self, opportunity_id: UUID) -> GuestPostOpportunity | None:
        return self.s.opportunities.get(opportunity_id)

    async def set_status(
        self, opportunity_id: UUID, status: OpportunityStatus, *, expected_version: int
    ) -> GuestPostOpportunity:
        cur = self.s.opportunities[opportunity_id]
        if cur.version != expected_version:
            raise OptimisticLockError("guest_post_opportunities", opportunity_id, expected_version)
        new = cur.model_copy(update={"status": status, "version": cur.version + 1})
        self.s.opportunities[opportunity_id] = new
        return new


class FakeBacklinks(_Repo):
    s: OutreachStore

    async def get(self, backlink_id: UUID) -> Backlink | None:
        return self.s.backlinks.get(backlink_id)

    async def set_status(
        self, backlink_id: UUID, status: BacklinkStatus, *, expected_version: int
    ) -> Backlink:
        cur = self.s.backlinks[backlink_id]
        if cur.version != expected_version:
            raise OptimisticLockError("backlinks", backlink_id, expected_version)
        new = cur.model_copy(update={"status": status, "version": cur.version + 1})
        self.s.backlinks[backlink_id] = new
        return new


class FakeProviderAccounts(_Repo):
    s: OutreachStore

    async def get(self, provider_account_id: UUID) -> dict[str, Any] | None:
        return self.s.provider_accounts.get(provider_account_id)

    async def list_enabled(self, provider_type: str) -> Sequence[dict[str, Any]]:
        return [
            a
            for a in self.s.provider_accounts.values()
            if a["provider_type"] == provider_type and a["enabled"]
        ]


class OutreachUnitOfWork(FakeUnitOfWork):
    def __init__(self, store: OutreachStore) -> None:
        super().__init__(store)
        self.sequences: Any = FakeSequences(store)
        self.messages: Any = FakeMessages(store)
        self.threads: Any = FakeThreads(store)
        self.replies: Any = FakeReplies(store)
        self.opportunities: Any = FakeOpportunities(store)
        self.backlinks: Any = FakeBacklinks(store)
        self.provider_accounts: Any = FakeProviderAccounts(store)
        self._extra: dict[str, Any] | None = None

    async def __aenter__(self) -> OutreachUnitOfWork:
        await super().__aenter__()
        self._extra = {f: copy.deepcopy(getattr(self.store, f)) for f in _EXTRA}
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await super().__aexit__(exc_type, exc, tb)
        if exc_type is not None and self._extra is not None:
            for name, value in self._extra.items():
                setattr(self.store, name, value)


class OutreachUowFactory:
    def __init__(self, store: OutreachStore | None = None) -> None:
        self.store = store or OutreachStore()

    def __call__(self) -> OutreachUnitOfWork:
        return OutreachUnitOfWork(self.store)
