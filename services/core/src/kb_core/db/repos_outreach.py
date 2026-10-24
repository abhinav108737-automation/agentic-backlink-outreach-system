"""Outreach repositories: sequences, messages, threads, replies, opportunities, backlinks and
provider accounts (Wave 2A).

Written by the Lead when both Wave 2A lanes blocked on their absence. The tables, the DTOs and
the state machines already existed; only these were missing.

Two rules hold throughout:

* `set_status` is a transition writer. It takes `expected_version`, bumps `version` in the same
  UPDATE and raises `OptimisticLockError` when the version is stale, so two concurrent
  transitions cannot both win. `TransitionService` is the intended caller.
* `backlink_verifications` is append-only in the database, so every check is a new row. The
  MISSING -> LOST threshold is a service decision; this layer only maintains the counters.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from kb_contracts.domain import (
    Backlink,
    BacklinkVerification,
    GuestPostOpportunity,
    OutreachMessage,
    OutreachSequence,
    Reply,
    ReplyClassification,
)
from kb_contracts.enums import (
    BacklinkStatus,
    MessageStatus,
    OpportunityStatus,
    SequenceStatus,
)
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from kb_core.db import tables as t
from kb_core.db._common import dto, fetch_all, fetch_one, keyset_page, stamp, ts_now, utc
from kb_core.db.page import CursorPage
from kb_core.db.protocols import OptimisticLockError

# A message in one of these states has left the system, so it consumes send quota and counts
# as a live contact for duplicate detection.
_SENT_STATES = (
    MessageStatus.SENT.value,
    MessageStatus.DELIVERED.value,
    MessageStatus.BOUNCED.value,
)

# "Live" = not terminal. Terminal message states per docs/13 2b.
_TERMINAL_MESSAGE = (
    MessageStatus.DELIVERED.value,
    MessageStatus.BOUNCED.value,
    MessageStatus.FAILED.value,
    MessageStatus.REJECTED.value,
    MessageStatus.CANCELLED.value,
    MessageStatus.POLICY_BLOCKED.value,
)


async def _lock_or_missing(
    session: AsyncSession, table: sa.Table, row_id: UUID, expected: int
) -> Exception:
    exists = await fetch_one(session, sa.select(table.c.id).where(table.c.id == row_id))
    if exists is None:
        return LookupError(f"{table.name} {row_id} not found")
    return OptimisticLockError(table.name, row_id, expected)


def _row(row: sa.RowMapping | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class SqlSequenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, sequence_id: UUID) -> OutreachSequence | None:
        row = await fetch_one(
            self._s, sa.select(t.outreach_sequences).where(t.outreach_sequences.c.id == sequence_id)
        )
        return dto(OutreachSequence, row) if row else None

    async def create(
        self,
        *,
        campaign_id: UUID,
        prospect_id: UUID,
        contact_channel_id: UUID,
        policy_version_id: UUID | None = None,
    ) -> OutreachSequence:
        stmt = (
            sa.insert(t.outreach_sequences)
            .values(
                stamp(
                    campaign_id=campaign_id,
                    prospect_id=prospect_id,
                    contact_channel_id=contact_channel_id,
                    policy_version_id=policy_version_id,
                )
            )
            .returning(t.outreach_sequences)
        )
        return dto(OutreachSequence, (await self._s.execute(stmt)).mappings().one())

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        status: SequenceStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[OutreachSequence]:
        where: list[sa.ColumnElement[bool]] = []
        if campaign_id is not None:
            where.append(t.outreach_sequences.c.campaign_id == campaign_id)
        if status is not None:
            where.append(t.outreach_sequences.c.status == SequenceStatus(status).value)
        return await keyset_page(
            self._s,
            t.outreach_sequences,
            where,
            cursor=cursor,
            limit=limit,
            build=lambda r: dto(OutreachSequence, r),
        )

    async def set_status(
        self, sequence_id: UUID, status: SequenceStatus, *, expected_version: int
    ) -> OutreachSequence:
        s = t.outreach_sequences
        stmt = (
            sa.update(s)
            .where(s.c.id == sequence_id, s.c.version == expected_version)
            .values(
                status=SequenceStatus(status).value, version=s.c.version + 1, updated_at=ts_now()
            )
            .returning(s)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise await _lock_or_missing(self._s, s, sequence_id, expected_version)
        return dto(OutreachSequence, row)

    async def set_next_due(self, sequence_id: UUID, *, run_at: datetime | None) -> None:
        s = t.outreach_sequences
        await self._s.execute(
            sa.update(s)
            .where(s.c.id == sequence_id)
            .values(
                next_due_at=utc(run_at, field="run_at") if run_at else None, updated_at=ts_now()
            )
        )

    async def advance_step(self, sequence_id: UUID) -> int:
        s = t.outreach_sequences
        stmt = (
            sa.update(s)
            .where(s.c.id == sequence_id)
            .values(current_step=s.c.current_step + 1, updated_at=ts_now())
            .returning(s.c.current_step)
        )
        value = (await self._s.execute(stmt)).scalar_one_or_none()
        if value is None:
            raise LookupError(f"outreach_sequences {sequence_id} not found")
        return int(value)


class SqlMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, message_id: UUID) -> OutreachMessage | None:
        row = await fetch_one(
            self._s, sa.select(t.outreach_messages).where(t.outreach_messages.c.id == message_id)
        )
        return dto(OutreachMessage, row) if row else None

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
    ) -> OutreachMessage:
        stmt = (
            sa.insert(t.outreach_messages)
            .values(
                stamp(
                    sequence_id=sequence_id,
                    thread_id=thread_id,
                    step_no=step_no,
                    message_version=message_version,
                    subject=subject,
                    body=body,
                    idempotency_key=idempotency_key,
                    claims=claims or {},
                    agent_run_id=agent_run_id,
                )
            )
            .returning(t.outreach_messages)
        )
        return dto(OutreachMessage, (await self._s.execute(stmt)).mappings().one())

    async def get_by_idempotency_key(self, key: str) -> OutreachMessage | None:
        row = await fetch_one(
            self._s,
            sa.select(t.outreach_messages).where(t.outreach_messages.c.idempotency_key == key),
        )
        return dto(OutreachMessage, row) if row else None

    async def live_for_step(self, sequence_id: UUID, step_no: int) -> OutreachMessage | None:
        m = t.outreach_messages
        row = await fetch_one(
            self._s,
            sa.select(m)
            .where(
                m.c.sequence_id == sequence_id,
                m.c.step_no == step_no,
                m.c.status.notin_(_TERMINAL_MESSAGE),
            )
            .order_by(m.c.message_version.desc()),
        )
        return dto(OutreachMessage, row) if row else None

    async def live_for_channel(
        self, campaign_id: UUID, contact_channel_id: UUID
    ) -> Sequence[OutreachMessage]:
        m, s = t.outreach_messages, t.outreach_sequences
        rows = await fetch_all(
            self._s,
            sa.select(m)
            .join(s, m.c.sequence_id == s.c.id)
            .where(
                s.c.campaign_id == campaign_id,
                s.c.contact_channel_id == contact_channel_id,
                m.c.status.notin_(_TERMINAL_MESSAGE),
            ),
        )
        return tuple(dto(OutreachMessage, r) for r in rows)

    async def list_for_sequence(self, sequence_id: UUID) -> Sequence[OutreachMessage]:
        m = t.outreach_messages
        rows = await fetch_all(
            self._s,
            sa.select(m)
            .where(m.c.sequence_id == sequence_id)
            .order_by(m.c.step_no, m.c.message_version),
        )
        return tuple(dto(OutreachMessage, r) for r in rows)

    async def count_sent_since(
        self,
        *,
        since: datetime,
        campaign_id: UUID | None = None,
        domain_id: UUID | None = None,
        contact_channel_id: UUID | None = None,
    ) -> int:
        m, s, p = t.outreach_messages, t.outreach_sequences, t.prospects
        stmt = (
            sa.select(sa.func.count())
            .select_from(m.join(s, m.c.sequence_id == s.c.id))
            .where(m.c.status.in_(_SENT_STATES), m.c.updated_at >= utc(since, field="since"))
        )
        if campaign_id is not None:
            stmt = stmt.where(s.c.campaign_id == campaign_id)
        if contact_channel_id is not None:
            stmt = stmt.where(s.c.contact_channel_id == contact_channel_id)
        if domain_id is not None:
            stmt = stmt.where(
                s.c.prospect_id.in_(sa.select(p.c.id).where(p.c.domain_id == domain_id))
            )
        return int((await self._s.execute(stmt)).scalar_one())

    async def set_status(
        self, message_id: UUID, status: MessageStatus, *, expected_version: int
    ) -> OutreachMessage:
        m = t.outreach_messages
        stmt = (
            sa.update(m)
            .where(m.c.id == message_id, m.c.version == expected_version)
            .values(
                status=MessageStatus(status).value, version=m.c.version + 1, updated_at=ts_now()
            )
            .returning(m)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise await _lock_or_missing(self._s, m, message_id, expected_version)
        return dto(OutreachMessage, row)

    async def record_qa(self, message_id: UUID, qa_result: dict[str, Any]) -> None:
        m = t.outreach_messages
        await self._s.execute(
            sa.update(m)
            .where(m.c.id == message_id)
            .values(qa_result=qa_result, updated_at=ts_now())
        )

    async def mark_sent(
        self, message_id: UUID, *, provider_message_id: str, expected_version: int
    ) -> OutreachMessage:
        m = t.outreach_messages
        stmt = (
            sa.update(m)
            .where(m.c.id == message_id, m.c.version == expected_version)
            .values(
                status=MessageStatus.SENT.value,
                provider_message_id=provider_message_id,
                version=m.c.version + 1,
                updated_at=ts_now(),
            )
            .returning(m)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise await _lock_or_missing(self._s, m, message_id, expected_version)
        return dto(OutreachMessage, row)

    async def bump_attempts(self, message_id: UUID) -> int:
        m = t.outreach_messages
        stmt = (
            sa.update(m)
            .where(m.c.id == message_id)
            .values(attempts=m.c.attempts + 1, updated_at=ts_now())
            .returning(m.c.attempts)
        )
        value = (await self._s.execute(stmt)).scalar_one_or_none()
        if value is None:
            raise LookupError(f"outreach_messages {message_id} not found")
        return int(value)


class SqlThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, thread_id: UUID) -> dict[str, Any] | None:
        return _row(
            await fetch_one(
                self._s, sa.select(t.outreach_threads).where(t.outreach_threads.c.id == thread_id)
            )
        )

    async def get_or_create_for_sequence(
        self, sequence_id: UUID, *, subject: str, provider_thread_id: str | None = None
    ) -> dict[str, Any]:
        th = t.outreach_threads
        existing = await fetch_one(
            self._s, sa.select(th).where(th.c.sequence_id == sequence_id).order_by(th.c.created_at)
        )
        if existing is not None:
            return dict(existing)
        stmt = (
            sa.insert(th)
            .values(
                stamp(
                    sequence_id=sequence_id,
                    subject=subject,
                    provider_thread_id=provider_thread_id,
                )
            )
            .returning(th)
        )
        return dict((await self._s.execute(stmt)).mappings().one())

    async def list_replies(self, thread_id: UUID) -> Sequence[Reply]:
        r = t.replies
        rows = await fetch_all(
            self._s, sa.select(r).where(r.c.thread_id == thread_id).order_by(r.c.received_at)
        )
        return tuple(dto(Reply, row) for row in rows)


class SqlReplyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def ingest(
        self,
        *,
        sequence_id: UUID,
        thread_id: UUID | None,
        provider_message_id: str,
        from_normalized: str,
        received_at: datetime,
        body_sanitized: str,
        raw_hash: str,
    ) -> tuple[Reply, bool]:
        r = t.replies
        value = from_normalized.strip().lower()
        ins = (
            pg.insert(r)
            .values(
                stamp(
                    sequence_id=sequence_id,
                    thread_id=thread_id,
                    provider_message_id=provider_message_id,
                    from_normalized=value,
                    received_at=utc(received_at, field="received_at"),
                    body_sanitized=body_sanitized,
                    raw_hash=raw_hash,
                )
            )
            .on_conflict_do_nothing(index_elements=[r.c.provider_message_id])
            .returning(r)
        )
        row = (await self._s.execute(ins)).mappings().first()
        if row is not None:
            return dto(Reply, row), True
        # Already ingested: re-polling a mailbox must not duplicate a received fact.
        existing = await fetch_one(
            self._s, sa.select(r).where(r.c.provider_message_id == provider_message_id)
        )
        assert existing is not None
        return dto(Reply, existing), False

    async def get(self, reply_id: UUID) -> Reply | None:
        row = await fetch_one(self._s, sa.select(t.replies).where(t.replies.c.id == reply_id))
        return dto(Reply, row) if row else None

    async def list_for_sequence(self, sequence_id: UUID) -> Sequence[Reply]:
        r = t.replies
        rows = await fetch_all(
            self._s, sa.select(r).where(r.c.sequence_id == sequence_id).order_by(r.c.received_at)
        )
        return tuple(dto(Reply, row) for row in rows)

    async def record_classification(
        self,
        *,
        reply_id: UUID,
        reply_class: str,
        confidence: float,
        next_action: str,
        deterministic_rule_applied: str | None,
        return_date: datetime | None = None,
        agent_run_id: UUID | None = None,
    ) -> ReplyClassification:
        stmt = (
            sa.insert(t.reply_classifications)
            .values(
                stamp(
                    reply_id=reply_id,
                    **{"class": reply_class},
                    confidence=confidence,
                    next_action=next_action,
                    deterministic_rule_applied=deterministic_rule_applied,
                    return_date=utc(return_date, field="return_date") if return_date else None,
                    agent_run_id=agent_run_id,
                )
            )
            .returning(t.reply_classifications)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(ReplyClassification, row, reply_class=row["class"])

    async def latest_classification(self, reply_id: UUID) -> ReplyClassification | None:
        c = t.reply_classifications
        row = await fetch_one(
            self._s,
            sa.select(c).where(c.c.reply_id == reply_id).order_by(c.c.created_at.desc()),
        )
        return dto(ReplyClassification, row, reply_class=row["class"]) if row else None


class SqlOpportunityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, opportunity_id: UUID) -> GuestPostOpportunity | None:
        o = t.guest_post_opportunities
        row = await fetch_one(self._s, sa.select(o).where(o.c.id == opportunity_id))
        return dto(GuestPostOpportunity, row) if row else None

    async def create(
        self,
        *,
        prospect_id: UUID,
        sequence_id: UUID | None = None,
        target_url: str | None = None,
    ) -> GuestPostOpportunity:
        stmt = (
            sa.insert(t.guest_post_opportunities)
            .values(stamp(prospect_id=prospect_id, sequence_id=sequence_id, target_url=target_url))
            .returning(t.guest_post_opportunities)
        )
        return dto(GuestPostOpportunity, (await self._s.execute(stmt)).mappings().one())

    async def list(
        self,
        *,
        status: OpportunityStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[GuestPostOpportunity]:
        where: list[sa.ColumnElement[bool]] = []
        if status is not None:
            where.append(t.guest_post_opportunities.c.status == OpportunityStatus(status).value)
        return await keyset_page(
            self._s,
            t.guest_post_opportunities,
            where,
            cursor=cursor,
            limit=limit,
            build=lambda r: dto(GuestPostOpportunity, r),
        )

    async def set_status(
        self, opportunity_id: UUID, status: OpportunityStatus, *, expected_version: int
    ) -> GuestPostOpportunity:
        o = t.guest_post_opportunities
        stmt = (
            sa.update(o)
            .where(o.c.id == opportunity_id, o.c.version == expected_version)
            .values(
                status=OpportunityStatus(status).value,
                version=o.c.version + 1,
                updated_at=ts_now(),
            )
            .returning(o)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise await _lock_or_missing(self._s, o, opportunity_id, expected_version)
        return dto(GuestPostOpportunity, row)

    async def set_terms(
        self, opportunity_id: UUID, *, agreed_terms: dict[str, Any], requires_approval: bool
    ) -> GuestPostOpportunity:
        o = t.guest_post_opportunities
        stmt = (
            sa.update(o)
            .where(o.c.id == opportunity_id)
            .values(
                agreed_terms=agreed_terms,
                requires_approval=requires_approval,
                updated_at=ts_now(),
            )
            .returning(o)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise LookupError(f"guest_post_opportunities {opportunity_id} not found")
        return dto(GuestPostOpportunity, row)

    async def record_negotiation(
        self,
        *,
        opportunity_id: UUID,
        draft: str,
        recommendation: str,
        risk_level: str,
        approval_request_id: UUID | None = None,
        agent_run_id: UUID | None = None,
    ) -> dict[str, Any]:
        n = t.negotiations
        nxt = sa.select(sa.func.coalesce(sa.func.max(n.c.round_no), 0) + 1).where(
            n.c.opportunity_id == opportunity_id
        )
        stmt = (
            sa.insert(n)
            .values(
                stamp(
                    opportunity_id=opportunity_id,
                    round_no=nxt.scalar_subquery(),
                    draft=draft,
                    recommendation=recommendation,
                    risk_level=risk_level,
                    approval_request_id=approval_request_id,
                    agent_run_id=agent_run_id,
                )
            )
            .returning(n)
        )
        return dict((await self._s.execute(stmt)).mappings().one())

    async def record_submission(
        self,
        *,
        opportunity_id: UUID,
        title: str,
        content_ref: str,
        published_url: str | None = None,
    ) -> dict[str, Any]:
        c = t.content_submissions
        nxt = sa.select(sa.func.coalesce(sa.func.max(c.c.version_no), 0) + 1).where(
            c.c.opportunity_id == opportunity_id
        )
        stmt = (
            sa.insert(c)
            .values(
                stamp(
                    opportunity_id=opportunity_id,
                    version_no=nxt.scalar_subquery(),
                    title=title,
                    content_ref=content_ref,
                    published_url=published_url,
                )
            )
            .returning(c)
        )
        return dict((await self._s.execute(stmt)).mappings().one())


class SqlBacklinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, backlink_id: UUID) -> Backlink | None:
        row = await fetch_one(
            self._s, sa.select(t.backlinks).where(t.backlinks.c.id == backlink_id)
        )
        return dto(Backlink, row) if row else None

    async def create(
        self,
        *,
        opportunity_id: UUID | None,
        source_url: str,
        target_url: str,
        expected_anchor: str | None = None,
    ) -> Backlink:
        stmt = (
            sa.insert(t.backlinks)
            .values(
                stamp(
                    opportunity_id=opportunity_id,
                    source_url=source_url,
                    target_url=target_url,
                    expected_anchor=expected_anchor,
                )
            )
            .returning(t.backlinks)
        )
        return dto(Backlink, (await self._s.execute(stmt)).mappings().one())

    async def list(
        self,
        *,
        status: BacklinkStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[Backlink]:
        where: list[sa.ColumnElement[bool]] = []
        if status is not None:
            where.append(t.backlinks.c.status == BacklinkStatus(status).value)
        return await keyset_page(
            self._s,
            t.backlinks,
            where,
            cursor=cursor,
            limit=limit,
            build=lambda r: dto(Backlink, r),
        )

    async def set_status(
        self, backlink_id: UUID, status: BacklinkStatus, *, expected_version: int
    ) -> Backlink:
        b = t.backlinks
        stmt = (
            sa.update(b)
            .where(b.c.id == backlink_id, b.c.version == expected_version)
            .values(
                status=BacklinkStatus(status).value, version=b.c.version + 1, updated_at=ts_now()
            )
            .returning(b)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise await _lock_or_missing(self._s, b, backlink_id, expected_version)
        return dto(Backlink, row)

    async def record_verification(
        self,
        *,
        backlink_id: UUID,
        checked_at: datetime,
        link_found: bool,
        result: str,
        verifier_version: str,
        http_status: int | None = None,
        final_url: str | None = None,
        canonical_url: str | None = None,
        anchor_text: str | None = None,
        rel_attrs: Sequence[str] = (),
        context_excerpt: str | None = None,
    ) -> BacklinkVerification:
        stmt = (
            sa.insert(t.backlink_verifications)
            .values(
                stamp(
                    backlink_id=backlink_id,
                    checked_at=utc(checked_at, field="checked_at"),
                    link_found=link_found,
                    result=result,
                    verifier_version=verifier_version,
                    http_status=http_status,
                    final_url=final_url,
                    canonical_url=canonical_url,
                    anchor_text=anchor_text,
                    rel_attrs=list(rel_attrs),
                    context_excerpt=context_excerpt,
                )
            )
            .returning(t.backlink_verifications)
        )
        return dto(BacklinkVerification, (await self._s.execute(stmt)).mappings().one())

    async def list_verifications(self, backlink_id: UUID) -> Sequence[BacklinkVerification]:
        v = t.backlink_verifications
        rows = await fetch_all(
            self._s, sa.select(v).where(v.c.backlink_id == backlink_id).order_by(v.c.checked_at)
        )
        return tuple(dto(BacklinkVerification, r) for r in rows)

    async def set_miss_counters(
        self, backlink_id: UUID, *, consecutive_misses: int, seen: bool, checked_at: datetime
    ) -> None:
        b = t.backlinks
        when = utc(checked_at, field="checked_at")
        values: dict[str, Any] = {
            "consecutive_misses": consecutive_misses,
            "last_checked_at": when,
            "updated_at": ts_now(),
        }
        if seen:
            values["last_seen_at"] = when
            values["first_seen_at"] = sa.func.coalesce(b.c.first_seen_at, when)
        await self._s.execute(sa.update(b).where(b.c.id == backlink_id).values(**values))


class SqlProviderAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, provider_account_id: UUID) -> dict[str, Any] | None:
        a = t.provider_accounts
        return _row(await fetch_one(self._s, sa.select(a).where(a.c.id == provider_account_id)))

    async def list_enabled(self, provider_type: str) -> Sequence[dict[str, Any]]:
        a = t.provider_accounts
        rows = await fetch_all(
            self._s,
            sa.select(a)
            .where(a.c.provider_type == provider_type, a.c.enabled.is_(True))
            .order_by(a.c.created_at),
        )
        return tuple(dict(r) for r in rows)
