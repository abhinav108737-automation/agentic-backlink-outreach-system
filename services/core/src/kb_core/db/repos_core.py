"""Repositories for the audit trail, policy, campaigns, domains, metrics, prospects and URLs.

None of these commit. They run inside the `SqlUnitOfWork` transaction, which is what makes a
state transition and its audit row atomic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from kb_contracts.domain import (
    Campaign,
    Domain,
    DomainMetric,
    PolicyBody,
    PolicyVersion,
    Prospect,
    ProspectUrl,
)
from kb_contracts.enums import CampaignStatus, ProspectStatus
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from kb_core.db import tables as t
from kb_core.db._common import dto, fetch_all, fetch_one, keyset_page, stamp, ts_now, utc
from kb_core.db.page import CursorPage
from kb_core.db.protocols import OptimisticLockError


class SqlAuditRepository:
    """Append-only. There is intentionally no update or delete method; PostgreSQL also rejects
    UPDATE/DELETE/TRUNCATE on `audit_events` with a trigger."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def append(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        subject_type: str,
        subject_id: UUID | None,
        correlation_id: str,
        old_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        reason: str | None = None,
        causation_id: str | None = None,
        agent_run_id: UUID | None = None,
        policy_version_id: UUID | None = None,
    ) -> UUID:
        stmt = (
            sa.insert(t.audit_events)
            .values(
                stamp(
                    occurred_at=ts_now(),
                    actor_type=actor_type,
                    actor_id=actor_id,
                    action=action,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    old_state=old_state,
                    new_state=new_state,
                    reason=reason,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    agent_run_id=agent_run_id,
                    policy_version_id=policy_version_id,
                )
            )
            .returning(t.audit_events.c.id)
        )
        new_id: UUID = (await self._s.execute(stmt)).scalar_one()
        return new_id

    async def list_for_subject(
        self, subject_type: str, subject_id: UUID, *, limit: int = 100
    ) -> Sequence[dict[str, Any]]:
        a = t.audit_events
        stmt = (
            sa.select(a)
            .where(a.c.subject_type == subject_type, a.c.subject_id == subject_id)
            .order_by(a.c.occurred_at, a.c.created_at, a.c.id)
            .limit(limit)
        )
        return [dict(r) for r in await fetch_all(self._s, stmt)]


def _policy_version(row: Any) -> PolicyVersion:
    return dto(PolicyVersion, row, body=PolicyBody.model_validate(row["body"]))


class SqlPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_version(self, policy_version_id: UUID) -> PolicyVersion | None:
        row = await fetch_one(
            self._s, sa.select(t.policy_versions).where(t.policy_versions.c.id == policy_version_id)
        )
        return _policy_version(row) if row else None

    async def active_version_for_campaign(self, campaign_id: UUID) -> PolicyVersion | None:
        pinned = await fetch_one(
            self._s,
            sa.select(t.campaigns.c.policy_version_id).where(t.campaigns.c.id == campaign_id),
        )
        if pinned is not None and pinned["policy_version_id"] is not None:
            return await self.get_version(pinned["policy_version_id"])
        return await self.active_global_version()

    async def active_global_version(self) -> PolicyVersion | None:
        pv, p = t.policy_versions, t.policies
        stmt = (
            sa.select(pv)
            .join(p, p.c.id == pv.c.policy_id)
            .where(p.c.scope == "global", pv.c.status == "ACTIVE")
            .order_by(pv.c.version_no.desc())
            .limit(1)
        )
        row = await fetch_one(self._s, stmt)
        return _policy_version(row) if row else None


class SqlCampaignRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, campaign_id: UUID) -> Campaign | None:
        row = await fetch_one(
            self._s, sa.select(t.campaigns).where(t.campaigns.c.id == campaign_id)
        )
        return dto(Campaign, row) if row else None

    async def create(
        self,
        *,
        name: str,
        policy_version_id: UUID | None = None,
        goals: dict[str, Any] | None = None,
    ) -> Campaign:
        stmt = (
            sa.insert(t.campaigns)
            .values(stamp(name=name, policy_version_id=policy_version_id, goals=goals or {}))
            .returning(t.campaigns)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(Campaign, row)

    async def list(self, *, cursor: str | None = None, limit: int = 50) -> CursorPage[Campaign]:
        return await keyset_page(
            self._s, t.campaigns, [], cursor=cursor, limit=limit, build=lambda r: dto(Campaign, r)
        )

    async def set_status(
        self, campaign_id: UUID, status: str, *, expected_version: int
    ) -> Campaign:
        new = CampaignStatus(status)  # ValueError on an unknown status, before touching the DB
        c = t.campaigns
        values: dict[str, Any] = {
            "status": new.value,
            "version": c.c.version + 1,
            "updated_at": ts_now(),
        }
        if new is CampaignStatus.ARCHIVED:
            values["archived_at"] = ts_now()
        stmt = (
            sa.update(c)
            .where(c.c.id == campaign_id, c.c.version == expected_version)
            .values(**values)
            .returning(c)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise await _lock_or_missing(self._s, c, campaign_id, expected_version)
        return dto(Campaign, row)


async def _lock_or_missing(
    session: AsyncSession, table: sa.Table, row_id: UUID, expected: int
) -> Exception:
    """Distinguish a stale version (retryable conflict) from a row that does not exist."""
    exists = await fetch_one(session, sa.select(table.c.id).where(table.c.id == row_id))
    if exists is None:
        return LookupError(f"{table.name} {row_id} not found")
    return OptimisticLockError(table.name, row_id, expected)


class SqlDomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_create(self, registered_domain: str) -> Domain:
        # Normalised in SQL-compatible form so lookups from different callers converge.
        name = registered_domain.strip().lower()
        if not name:
            raise ValueError("registered_domain must not be empty")
        d = t.domains
        ins = (
            pg.insert(d)
            .values(stamp(registered_domain=name, first_seen_at=ts_now()))
            .on_conflict_do_nothing(index_elements=[d.c.registered_domain])
            .returning(d)
        )
        row = (await self._s.execute(ins)).mappings().first()
        if row is None:  # lost the race / already present: read the committed row
            row = await fetch_one(self._s, sa.select(d).where(d.c.registered_domain == name))
        assert row is not None
        return dto(Domain, row)

    async def get(self, domain_id: UUID) -> Domain | None:
        row = await fetch_one(self._s, sa.select(t.domains).where(t.domains.c.id == domain_id))
        return dto(Domain, row) if row else None


class SqlDomainMetricRepository:
    """Append-only readings. See `latest` for the three-state contract that drives DEFERRED."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        domain_id: UUID,
        provider: str,
        metric: str,
        value: float | None,
        source: str,
        fetched_at: datetime,
    ) -> DomainMetric:
        if value is not None and not math.isfinite(value):
            raise ValueError("metric value must be finite or None (unavailable)")
        stmt = (
            sa.insert(t.domain_metrics)
            .values(
                stamp(
                    domain_id=domain_id,
                    provider=provider,
                    metric=metric,
                    value=None if value is None else Decimal(repr(value)),
                    fetched_at=utc(fetched_at, field="fetched_at"),
                    source=source,
                )
            )
            .returning(t.domain_metrics)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        # SEAM NOTE: frozen `DomainMetric.value` is a non-optional float, so an "unavailable"
        # reading (stored as NULL) is surfaced to the caller as NaN. NaN fails every threshold
        # comparison, so it can never pass qualification, and `latest()` is the authoritative
        # three-state reader. Reported to the Lead as a contract gap.
        return dto(
            DomainMetric, row, value=float("nan") if row["value"] is None else float(row["value"])
        )

    async def latest(self, domain_id: UUID, metrics: Sequence[str]) -> dict[str, float | None]:
        """Newest reading per metric, keeping three states distinct:

        - key absent          -> never fetched
        - key -> None         -> fetched, provider had no value (unavailable)
        - key -> float        -> fetched with a value
        The newest reading wins even if an older one had a value: an "unavailable" refetch must
        not be masked by stale data.
        """
        if not metrics:
            return {}
        m = t.domain_metrics
        stmt = (
            sa.select(m.c.metric, m.c.value)
            .where(m.c.domain_id == domain_id, m.c.metric.in_(list(metrics)))
            .ext(distinct_on(m.c.metric))
            .order_by(m.c.metric, m.c.fetched_at.desc(), m.c.created_at.desc(), m.c.id.desc())
        )
        out: dict[str, float | None] = {}
        for r in await fetch_all(self._s, stmt):
            out[r["metric"]] = None if r["value"] is None else float(r["value"])
        return out


class SqlProspectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, prospect_id: UUID) -> Prospect | None:
        row = await fetch_one(
            self._s, sa.select(t.prospects).where(t.prospects.c.id == prospect_id)
        )
        return dto(Prospect, row) if row else None

    async def get_by_domain(self, campaign_id: UUID, domain_id: UUID) -> Prospect | None:
        p = t.prospects
        row = await fetch_one(
            self._s, sa.select(p).where(p.c.campaign_id == campaign_id, p.c.domain_id == domain_id)
        )
        return dto(Prospect, row) if row else None

    async def create(
        self, *, campaign_id: UUID, domain_id: UUID, discovered_via: str | None = None
    ) -> Prospect:
        p = t.prospects
        ins = (
            pg.insert(p)
            .values(
                stamp(campaign_id=campaign_id, domain_id=domain_id, discovered_via=discovered_via)
            )
            .on_conflict_do_nothing(index_elements=[p.c.campaign_id, p.c.domain_id])
            .returning(p)
        )
        row = (await self._s.execute(ins)).mappings().first()
        if row is None:  # re-discovery: return the existing prospect, never a duplicate
            existing = await self.get_by_domain(campaign_id, domain_id)
            assert existing is not None
            return existing
        return dto(Prospect, row)

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        status: ProspectStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[Prospect]:
        where: list[sa.ColumnElement[bool]] = []
        if campaign_id is not None:
            where.append(t.prospects.c.campaign_id == campaign_id)
        if status is not None:
            where.append(t.prospects.c.status == ProspectStatus(status).value)
        return await keyset_page(
            self._s,
            t.prospects,
            where,
            cursor=cursor,
            limit=limit,
            build=lambda r: dto(Prospect, r),
        )

    async def set_status(
        self, prospect_id: UUID, status: ProspectStatus, *, expected_version: int
    ) -> Prospect:
        p = t.prospects
        stmt = (
            sa.update(p)
            .where(p.c.id == prospect_id, p.c.version == expected_version)
            .values(
                status=ProspectStatus(status).value, version=p.c.version + 1, updated_at=ts_now()
            )
            .returning(p)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            raise await _lock_or_missing(self._s, p, prospect_id, expected_version)
        return dto(Prospect, row)

    async def count_by_status(self, campaign_id: UUID) -> dict[ProspectStatus, int]:
        p = t.prospects
        stmt = (
            sa.select(p.c.status, sa.func.count().label("n"))
            .where(p.c.campaign_id == campaign_id)
            .group_by(p.c.status)
        )
        counts = {ProspectStatus(r["status"]): int(r["n"]) for r in await fetch_all(self._s, stmt)}
        return {s: counts.get(s, 0) for s in ProspectStatus}


class SqlProspectUrlRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self,
        *,
        prospect_id: UUID,
        url: str,
        canonical_url: str,
        url_hash: str,
        page_type: str = "other",
        provenance: dict[str, Any] | None = None,
    ) -> ProspectUrl:
        u = t.prospect_urls
        # DO UPDATE (no-op on content) so RETURNING yields the existing row on a duplicate;
        # first writer's url/provenance win and the DB enforces uniqueness.
        stmt = (
            pg.insert(u)
            .values(
                stamp(
                    prospect_id=prospect_id,
                    url=url,
                    canonical_url=canonical_url,
                    url_hash=url_hash,
                    page_type=page_type,
                    provenance=provenance or {},
                )
            )
            .on_conflict_do_update(
                index_elements=[u.c.prospect_id, u.c.url_hash], set_={"updated_at": ts_now()}
            )
            .returning(u)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(ProspectUrl, row)

    async def list_for_prospect(self, prospect_id: UUID) -> Sequence[ProspectUrl]:
        u = t.prospect_urls
        stmt = select_ordered(u, u.c.prospect_id == prospect_id)
        return [dto(ProspectUrl, r) for r in await fetch_all(self._s, stmt)]


def select_ordered(table: sa.Table, *where: sa.ColumnElement[bool]) -> sa.Select[Any]:
    return sa.select(table).where(*where).order_by(table.c.created_at, table.c.id)
