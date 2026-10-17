"""Repositories for idempotency, the PostgreSQL job queue, approvals, settings and agent runs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from kb_contracts.domain import ApprovalRequest, ScheduledJob
from kb_contracts.enums import ApprovalKind, ApprovalStatus, JobStatus
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from kb_core.db import tables as t
from kb_core.db._common import dto, fetch_all, fetch_one, keyset_page, stamp, ts_now, utc
from kb_core.db.page import CursorPage
from kb_core.db.protocols import OptimisticLockError


class InvalidJobTransition(ValueError):
    """The job was not in a state from which the requested outcome is legal (docs/13 §6)."""


class ApprovalAlreadyDecided(ValueError):
    """An approval request is decided exactly once."""


class SqlIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def claim(self, *, scope: str, key: str, request_hash: str) -> bool:
        """`INSERT ... ON CONFLICT DO NOTHING` on UNIQUE(scope, key).

        Under concurrency the second inserter blocks on the first's uncommitted unique-index
        entry, then sees the conflict and gets False; if the first rolls back it wins instead.
        There is no read-then-write window.
        """
        r = t.idempotency_records
        stmt = (
            pg.insert(r)
            .values(stamp(scope=scope, key=key, request_hash=request_hash))
            .on_conflict_do_nothing(index_elements=[r.c.scope, r.c.key])
            .returning(r.c.id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def get(self, *, scope: str, key: str) -> dict[str, Any] | None:
        r = t.idempotency_records
        row = await fetch_one(self._s, sa.select(r).where(r.c.scope == scope, r.c.key == key))
        return dict(row) if row else None

    async def record_response(self, *, scope: str, key: str, response_ref: str) -> None:
        r = t.idempotency_records
        res = await self._s.execute(
            sa.update(r)
            .where(r.c.scope == scope, r.c.key == key)
            .values(response_ref=response_ref, updated_at=ts_now())
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            raise LookupError(f"idempotency record {scope}/{key} not found")


def _backoff(attempts: int) -> timedelta:
    """Bounded exponential backoff: 30s, 60s, 120s ... capped at one hour."""
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


class SqlJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        run_at: datetime,
        idempotency_key: str,
        concurrency_key: str | None = None,
        max_attempts: int = 5,
        workflow_run_id: UUID | None = None,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
    ) -> ScheduledJob:
        j = t.scheduled_jobs
        ins = (
            pg.insert(j)
            .values(
                stamp(
                    job_type=job_type,
                    payload=payload,
                    run_at=utc(run_at, field="run_at"),
                    idempotency_key=idempotency_key,
                    concurrency_key=concurrency_key,
                    max_attempts=max_attempts,
                    workflow_run_id=workflow_run_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                )
            )
            .on_conflict_do_nothing(index_elements=[j.c.idempotency_key])
            .returning(j)
        )
        row = (await self._s.execute(ins)).mappings().first()
        if row is None:
            row = await fetch_one(
                self._s, sa.select(j).where(j.c.idempotency_key == idempotency_key)
            )
        assert row is not None
        return dto(ScheduledJob, row)

    async def claim_due(
        self, *, now: datetime, lease_owner: str, lease_seconds: int, limit: int = 1
    ) -> list[ScheduledJob]:
        now = utc(now, field="now")
        j = t.scheduled_jobs
        # Backoff elapsed: RETRY_WAIT -> SCHEDULED (docs/13 §6), then claim from SCHEDULED only.
        await self._s.execute(
            sa.update(j)
            .where(j.c.status == "RETRY_WAIT", j.c.run_at <= now)
            .values(status="SCHEDULED", updated_at=ts_now())
        )
        running = sa.alias(j, "running")
        busy = (
            sa.select(sa.literal(1))
            .where(
                running.c.status == "RUNNING",
                running.c.concurrency_key == j.c.concurrency_key,
                j.c.concurrency_key.is_not(None),
            )
            .exists()
        )
        candidates = (
            sa.select(j.c.id, j.c.concurrency_key)
            .where(j.c.status == "SCHEDULED", j.c.run_at <= now, ~busy)
            .order_by(j.c.priority.desc(), j.c.run_at, j.c.created_at, j.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        picked: list[UUID] = []
        seen_keys: set[str] = set()
        for r in await fetch_all(self._s, candidates):
            key = r["concurrency_key"]
            if key is not None:
                if key in seen_keys:
                    continue  # one job per concurrency_key in a single claim
                seen_keys.add(key)
            picked.append(r["id"])
        if not picked:
            return []
        stmt = (
            sa.update(j)
            .where(j.c.id.in_(picked))
            .values(
                status="RUNNING",
                lease_owner=lease_owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=ts_now(),
            )
            .returning(j)
        )
        rows = (await self._s.execute(stmt)).mappings().all()
        return [dto(ScheduledJob, r) for r in sorted(rows, key=lambda r: picked.index(r["id"]))]

    async def complete(self, job_id: UUID, *, status: JobStatus, last_error: str | None) -> None:
        outcome = JobStatus(status)
        if outcome not in (
            JobStatus.SUCCEEDED,
            JobStatus.RETRY_WAIT,
            JobStatus.DEAD,
            JobStatus.SCHEDULED,
        ):
            raise InvalidJobTransition(f"{outcome} is not a legal outcome from RUNNING")
        j = t.scheduled_jobs
        values: dict[str, Any] = {
            "status": outcome.value,
            "last_error": last_error,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": ts_now(),
        }
        if outcome in (JobStatus.RETRY_WAIT, JobStatus.SCHEDULED):
            values["attempts"] = j.c.attempts + 1
        stmt = (
            sa.update(j)
            .where(j.c.id == job_id, j.c.status == "RUNNING")
            .values(**values)
            .returning(j)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise InvalidJobTransition(f"job {job_id} is not RUNNING")
        if outcome is JobStatus.RETRY_WAIT:
            attempts = int(row["attempts"])
            if attempts >= int(row["max_attempts"]):  # retry-bounded: dead-letter, never loop
                await self._s.execute(
                    sa.update(j).where(j.c.id == job_id).values(status="DEAD", updated_at=ts_now())
                )
            else:
                await self._s.execute(
                    sa.update(j)
                    .where(j.c.id == job_id)
                    .values(run_at=ts_now() + _backoff(attempts), updated_at=ts_now())
                )

    async def cancel(self, *, idempotency_key: str) -> bool:
        j = t.scheduled_jobs
        res = await self._s.execute(
            sa.update(j)
            .where(
                j.c.idempotency_key == idempotency_key, j.c.status.in_(("SCHEDULED", "RETRY_WAIT"))
            )
            .values(status="CANCELLED", updated_at=ts_now())
            .returning(j.c.id)
        )
        return res.first() is not None

    async def cancel_for_subject(self, *, subject_type: str, subject_id: UUID) -> int:
        j = t.scheduled_jobs
        res = await self._s.execute(
            sa.update(j)
            .where(
                j.c.subject_type == subject_type,
                j.c.subject_id == subject_id,
                j.c.status.in_(("SCHEDULED", "RETRY_WAIT")),
            )
            .values(status="CANCELLED", updated_at=ts_now())
            .returning(j.c.id)
        )
        return len(res.all())

    async def reclaim_expired(self, *, now: datetime) -> int:
        """Lease expiry: RUNNING -> SCHEDULED with attempts += 1; DEAD once attempts are spent."""
        now = utc(now, field="now")
        j = t.scheduled_jobs
        res = await self._s.execute(
            sa.update(j)
            .where(j.c.status == "RUNNING", j.c.lease_expires_at < now)
            .values(
                status=sa.case((j.c.attempts + 1 >= j.c.max_attempts, "DEAD"), else_="SCHEDULED"),
                attempts=j.c.attempts + 1,
                lease_owner=None,
                lease_expires_at=None,
                last_error="lease expired",
                updated_at=ts_now(),
            )
            .returning(j.c.id)
        )
        return len(res.all())


class SqlApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        kind: str,
        subject_type: str,
        subject_id: UUID,
        requested_by: str,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> ApprovalRequest:
        stmt = (
            sa.insert(t.approval_requests)
            .values(
                stamp(
                    kind=ApprovalKind(kind).value,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    requested_by=requested_by,
                    reason=reason,
                    correlation_id=correlation_id,
                )
            )
            .returning(t.approval_requests)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(ApprovalRequest, row)

    async def get(self, approval_id: UUID) -> ApprovalRequest | None:
        a = t.approval_requests
        row = await fetch_one(self._s, sa.select(a).where(a.c.id == approval_id))
        return dto(ApprovalRequest, row) if row else None

    async def decide(
        self, approval_id: UUID, *, status: ApprovalStatus, decided_by: str, reason: str | None
    ) -> ApprovalRequest:
        new = ApprovalStatus(status)
        if new is ApprovalStatus.PENDING:
            raise ValueError("an approval cannot be decided as PENDING")
        a = t.approval_requests
        stmt = (
            sa.update(a)
            .where(a.c.id == approval_id, a.c.status == "PENDING")
            .values(
                status=new.value,
                decided_by=decided_by,
                decided_at=ts_now(),
                reason=reason,
                updated_at=ts_now(),
            )
            .returning(a)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            if await self.get(approval_id) is None:
                raise LookupError(f"approval {approval_id} not found")
            raise ApprovalAlreadyDecided(f"approval {approval_id} is not PENDING")
        return dto(ApprovalRequest, row)

    async def list_pending(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> CursorPage[ApprovalRequest]:
        return await keyset_page(
            self._s,
            t.approval_requests,
            [t.approval_requests.c.status == "PENDING"],
            cursor=cursor,
            limit=limit,
            build=lambda r: dto(ApprovalRequest, r),
        )

    async def latest_for_subject(
        self, *, subject_type: str, subject_id: UUID, kind: str
    ) -> ApprovalRequest | None:
        a = t.approval_requests
        stmt = (
            sa.select(a)
            .where(
                a.c.subject_type == subject_type,
                a.c.subject_id == subject_id,
                a.c.kind == ApprovalKind(kind).value,
            )
            .order_by(a.c.created_at.desc(), a.c.id.desc())
            .limit(1)
        )
        row = await fetch_one(self._s, stmt)
        return dto(ApprovalRequest, row) if row else None


class SqlSettingsRepository:
    """`get` returns `{"key", "value", "version"}` so callers can pass `version` back as
    `expected_version`; the frozen protocol returns only a dict, so the version rides in it."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, key: str) -> dict[str, Any] | None:
        s = t.system_settings
        row = await fetch_one(self._s, sa.select(s).where(s.c.key == key))
        if row is None:
            return None
        return {"key": row["key"], "value": row["value"], "version": row["version"]}

    async def set(self, key: str, value: dict[str, Any], *, expected_version: int | None) -> int:
        """`expected_version=None`: unconditional upsert (last writer wins, version bumps).
        An int: compare-and-set in a single UPDATE; 0 means "must not exist yet"."""
        s = t.system_settings
        if expected_version is None:
            stmt = (
                pg.insert(s)
                .values(stamp(key=key, value=value))
                .on_conflict_do_update(
                    index_elements=[s.c.key],
                    set_={"value": value, "version": s.c.version + 1, "updated_at": ts_now()},
                )
                .returning(s.c.version)
            )
            return int((await self._s.execute(stmt)).scalar_one())
        if expected_version == 0:
            ins = (
                pg.insert(s)
                .values(stamp(key=key, value=value))
                .on_conflict_do_nothing(index_elements=[s.c.key])
                .returning(s.c.version)
            )
            created = (await self._s.execute(ins)).scalar()
            if created is None:
                raise OptimisticLockError("system_settings", UUID(int=0), 0)
            return int(created)
        upd = (
            sa.update(s)
            .where(s.c.key == key, s.c.version == expected_version)
            .values(value=value, version=s.c.version + 1, updated_at=ts_now())
            .returning(s.c.version)
        )
        new_version = (await self._s.execute(upd)).scalar()
        if new_version is None:
            raise OptimisticLockError("system_settings", UUID(int=0), expected_version)
        return int(new_version)


class SqlAgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def start(
        self,
        *,
        agent: str,
        task_type: str,
        campaign_id: UUID | None,
        input_refs: dict[str, Any],
        model: str | None,
        provider: str | None,
        correlation_id: str | None,
        workflow_run_id: UUID | None = None,
        policy_version_id: UUID | None = None,
    ) -> UUID:
        stmt = (
            sa.insert(t.agent_runs)
            .values(
                stamp(
                    agent=agent,
                    task_type=task_type,
                    campaign_id=campaign_id,
                    input_refs=input_refs,
                    model=model,
                    provider=provider,
                    correlation_id=correlation_id,
                    workflow_run_id=workflow_run_id,
                    policy_version_id=policy_version_id,
                    started_at=ts_now(),
                )
            )
            .returning(t.agent_runs.c.id)
        )
        run_id: UUID = (await self._s.execute(stmt)).scalar_one()
        return run_id

    async def finish(
        self,
        agent_run_id: UUID,
        *,
        structured_result: dict[str, Any] | None,
        confidence: float | None,
        latency_ms: int | None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        failure: str | None = None,
        fallback_used: bool = False,
    ) -> None:
        r = t.agent_runs
        res = await self._s.execute(
            sa.update(r)
            .where(r.c.id == agent_run_id)
            .values(
                structured_result=structured_result,
                confidence=confidence,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                failure=failure,
                fallback_used=fallback_used,
                finished_at=ts_now(),
                updated_at=ts_now(),
            )
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            raise LookupError(f"agent run {agent_run_id} not found")

    async def record_tool_call(
        self,
        *,
        agent_run_id: UUID,
        tool_name: str,
        args_redacted: dict[str, Any],
        result_summary: dict[str, Any] | None,
        status: str,
        policy_decision: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        correlation_id: str | None = None,
    ) -> UUID:
        stmt = (
            sa.insert(t.tool_calls)
            .values(
                stamp(
                    agent_run_id=agent_run_id,
                    tool_name=tool_name,
                    args_redacted=args_redacted,
                    result_summary=result_summary,
                    status=status,
                    policy_decision=policy_decision,
                    latency_ms=latency_ms,
                    correlation_id=correlation_id,
                )
            )
            .returning(t.tool_calls.c.id)
        )
        call_id: UUID = (await self._s.execute(stmt)).scalar_one()
        return call_id

    async def get(self, agent_run_id: UUID) -> dict[str, Any] | None:
        r = t.agent_runs
        row = await fetch_one(self._s, sa.select(r).where(r.c.id == agent_run_id))
        return dict(row) if row else None

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        agent: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[dict[str, Any]]:
        where: list[sa.ColumnElement[bool]] = []
        if campaign_id is not None:
            where.append(t.agent_runs.c.campaign_id == campaign_id)
        if agent is not None:
            where.append(t.agent_runs.c.agent == agent)
        return await keyset_page(
            self._s,
            t.agent_runs,
            where,
            cursor=cursor,
            limit=limit,
            build=lambda r: dict(r),
        )
