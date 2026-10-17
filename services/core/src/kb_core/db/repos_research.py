"""Repositories for qualification, ranking, evidence, dossiers, contacts and suppression."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from kb_contracts.domain import (
    Contact,
    ContactChannel,
    EvidenceItem,
    QualificationDecision,
    RankingResult,
    ResearchDossier,
    SuppressionEntry,
)
from kb_contracts.enums import QualificationOutcome, SuppressionReason, SuppressionScope
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kb_core.db import tables as t
from kb_core.db._common import dto, fetch_all, fetch_one, stamp, ts_now, utc
from kb_core.db.repos_core import select_ordered


class SqlQualificationRepository:
    """Append-only history of decisions; `latest` is the newest row. A decision row is never
    edited, so earlier outcomes stay available for audit and re-qualification."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        prospect_id: UUID,
        policy_version_id: UUID,
        outcome: str,
        reason_codes: Sequence[str],
        metric_snapshot: dict[str, float | None],
        missing_metrics: Sequence[str],
        agent_run_id: UUID | None = None,
    ) -> QualificationDecision:
        out = QualificationOutcome(outcome)
        if out is QualificationOutcome.ELIGIBLE and missing_metrics:
            # ADR-0015: a prospect with a missing required metric is DEFERRED, never passed.
            raise ValueError("an ELIGIBLE decision cannot list missing_metrics")
        stmt = (
            sa.insert(t.qualification_decisions)
            .values(
                stamp(
                    prospect_id=prospect_id,
                    policy_version_id=policy_version_id,
                    outcome=out.value,
                    reason_codes=list(reason_codes),
                    metric_snapshot=metric_snapshot,
                    missing_metrics=list(missing_metrics),
                    decided_at=ts_now(),
                    agent_run_id=agent_run_id,
                )
            )
            .returning(t.qualification_decisions)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(QualificationDecision, row)

    async def latest(self, prospect_id: UUID) -> QualificationDecision | None:
        q = t.qualification_decisions
        stmt = (
            sa.select(q)
            .where(q.c.prospect_id == prospect_id)
            .order_by(q.c.decided_at.desc(), q.c.created_at.desc(), q.c.id.desc())
            .limit(1)
        )
        row = await fetch_one(self._s, stmt)
        return dto(QualificationDecision, row) if row else None


class SqlRankingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        prospect_id: UUID,
        policy_version_id: UUID,
        score: float,
        components: dict[str, float],
        explanation: str,
    ) -> RankingResult:
        if not math.isfinite(score):
            raise ValueError("score must be finite")
        stmt = (
            sa.insert(t.ranking_results)
            .values(
                stamp(
                    prospect_id=prospect_id,
                    policy_version_id=policy_version_id,
                    score=score,
                    components=components,
                    explanation=explanation,
                )
            )
            .returning(t.ranking_results)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(RankingResult, row)

    async def latest(self, prospect_id: UUID) -> RankingResult | None:
        r = t.ranking_results
        stmt = (
            sa.select(r)
            .where(r.c.prospect_id == prospect_id)
            .order_by(r.c.created_at.desc(), r.c.id.desc())
            .limit(1)
        )
        row = await fetch_one(self._s, stmt)
        return dto(RankingResult, row) if row else None


_EVIDENCE_REQUIRED = ("source_url", "excerpt", "content_hash", "kind", "extracted_at")


class SqlEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record_many(
        self,
        prospect_id: UUID,
        items: Sequence[dict[str, Any]],
        *,
        dossier_id: UUID | None = None,
    ) -> Sequence[EvidenceItem]:
        if not items:
            return ()
        rows: list[dict[str, Any]] = []
        for item in items:
            missing = [k for k in _EVIDENCE_REQUIRED if k not in item]
            if missing:
                raise ValueError(f"evidence item missing keys: {missing}")
            rows.append(
                stamp(
                    prospect_id=prospect_id,
                    dossier_id=dossier_id,
                    source_url=item["source_url"],
                    excerpt=item["excerpt"],
                    content_hash=item["content_hash"],
                    kind=item["kind"],
                    extracted_at=utc(item["extracted_at"], field="extracted_at"),
                    extractor=item.get("extractor", "deterministic"),
                )
            )
        res = await self._s.execute(
            sa.insert(t.evidence_items).values(rows).returning(t.evidence_items)
        )
        return [dto(EvidenceItem, r) for r in res.mappings().all()]

    async def list_for_prospect(self, prospect_id: UUID) -> Sequence[EvidenceItem]:
        e = t.evidence_items
        stmt = select_ordered(e, e.c.prospect_id == prospect_id)
        return [dto(EvidenceItem, r) for r in await fetch_all(self._s, stmt)]

    async def get_many(self, evidence_ids: Sequence[UUID]) -> Sequence[EvidenceItem]:
        if not evidence_ids:
            return ()
        e = t.evidence_items
        stmt = select_ordered(e, e.c.id.in_(list(evidence_ids)))
        return [dto(EvidenceItem, r) for r in await fetch_all(self._s, stmt)]


class SqlDossierRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        prospect_id: UUID,
        summary: str,
        topics: Sequence[str],
        editorial_signals: dict[str, Any],
        agent_run_id: UUID | None = None,
    ) -> ResearchDossier:
        # Serialise version allocation per prospect: lock the parent row so two concurrent
        # creators queue instead of racing to UNIQUE(prospect_id, version_no).
        locked = await fetch_one(
            self._s,
            sa.select(t.prospects.c.id).where(t.prospects.c.id == prospect_id).with_for_update(),
        )
        if locked is None:
            raise LookupError(f"prospect {prospect_id} not found")
        d = t.research_dossiers
        next_no = (
            sa.select(sa.func.coalesce(sa.func.max(d.c.version_no), 0) + 1)
            .where(d.c.prospect_id == prospect_id)
            .scalar_subquery()
        )
        stmt = (
            sa.insert(d)
            .values(
                stamp(
                    prospect_id=prospect_id,
                    version_no=next_no,
                    summary=summary,
                    topics=list(topics),
                    editorial_signals=editorial_signals,
                    agent_run_id=agent_run_id,
                )
            )
            .returning(d)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(ResearchDossier, row)

    async def latest(self, prospect_id: UUID) -> ResearchDossier | None:
        d = t.research_dossiers
        stmt = (
            sa.select(d)
            .where(d.c.prospect_id == prospect_id)
            .order_by(d.c.version_no.desc())
            .limit(1)
        )
        row = await fetch_one(self._s, stmt)
        return dto(ResearchDossier, row) if row else None


class SqlContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        domain_id: UUID,
        name: str | None,
        role: str | None,
        contact_type: str,
        confidence: float,
        provenance: dict[str, Any] | None = None,
    ) -> Contact:
        stmt = (
            sa.insert(t.contacts)
            .values(
                stamp(
                    domain_id=domain_id,
                    name=name,
                    role=role,
                    contact_type=contact_type,
                    confidence=confidence,
                    provenance=provenance or {},
                )
            )
            .returning(t.contacts)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(Contact, row)

    async def add_channel(
        self,
        *,
        contact_id: UUID,
        channel_type: str,
        value_normalized: str,
        confidence: float,
        evidence_item_id: UUID | None = None,
    ) -> ContactChannel:
        if value_normalized != value_normalized.strip().lower():
            raise ValueError("value_normalized must already be lower-cased and trimmed")
        c = t.contact_channels
        stmt = (
            pg.insert(c)
            .values(
                stamp(
                    contact_id=contact_id,
                    channel_type=channel_type,
                    value_normalized=value_normalized,
                    confidence=confidence,
                    evidence_item_id=evidence_item_id,
                )
            )
            .on_conflict_do_update(
                index_elements=[c.c.contact_id, c.c.channel_type, c.c.value_normalized],
                set_={"updated_at": ts_now()},
            )
            .returning(c)
        )
        row = (await self._s.execute(stmt)).mappings().one()
        return dto(ContactChannel, row)

    async def list_for_domain(self, domain_id: UUID) -> Sequence[Contact]:
        stmt = select_ordered(t.contacts, t.contacts.c.domain_id == domain_id)
        return [dto(Contact, r) for r in await fetch_all(self._s, stmt)]

    async def list_channels(self, domain_id: UUID) -> Sequence[ContactChannel]:
        c, k = t.contact_channels, t.contacts
        stmt = (
            sa.select(c)
            .join(k, k.c.id == c.c.contact_id)
            .where(k.c.domain_id == domain_id)
            .order_by(c.c.created_at, c.c.id)
        )
        return [dto(ContactChannel, r) for r in await fetch_all(self._s, stmt)]


def _norm(value: str) -> sa.ColumnElement[str]:
    """Normalise in SQL so the value always satisfies the table's CHECK
    (`value_normalized = lower(btrim(value_normalized))`), whatever Python thinks of casing."""
    return sa.func.lower(sa.func.btrim(value))


class SqlSuppressionRepository:
    """No delete exists. Superseding sets `superseded_at`/`superseded_by`, the only columns the
    DB guard trigger lets change."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(
        self,
        *,
        scope: SuppressionScope,
        value_normalized: str,
        reason: str,
        campaign_id: UUID | None = None,
        created_by: str = "system",
        source_event_ref: str | None = None,
    ) -> SuppressionEntry:
        if not value_normalized.strip():
            raise ValueError("suppression value must not be empty")
        sc = SuppressionScope(scope)
        why = SuppressionReason(reason)
        s = t.suppression_entries
        try:
            # SAVEPOINT: a duplicate-active violation must not poison the outer transaction.
            async with self._s.begin_nested():
                row = (
                    (
                        await self._s.execute(
                            sa.insert(s)
                            .values(
                                stamp(
                                    scope=sc.value,
                                    value_normalized=_norm(value_normalized),
                                    reason=why.value,
                                    campaign_id=campaign_id,
                                    created_by=created_by,
                                    source_event_ref=source_event_ref,
                                )
                            )
                            .returning(s)
                        )
                    )
                    .mappings()
                    .one()
                )
            return dto(SuppressionEntry, row)
        except IntegrityError:
            existing = await fetch_one(
                self._s,
                sa.select(s).where(
                    s.c.scope == sc.value,
                    s.c.value_normalized == _norm(value_normalized),
                    s.c.superseded_at.is_(None),
                    s.c.campaign_id.is_(None)
                    if campaign_id is None
                    else s.c.campaign_id == campaign_id,
                ),
            )
            if existing is None:
                raise
            return dto(SuppressionEntry, existing)

    def _active_match(
        self, pairs: Sequence[tuple[SuppressionScope, str]], campaign_id: UUID | None
    ) -> sa.Select[Any]:
        s = t.suppression_entries
        any_pair = sa.or_(
            *[sa.and_(s.c.scope == sc.value, s.c.value_normalized == _norm(v)) for sc, v in pairs]
        )
        applies = (
            s.c.campaign_id.is_(None)
            if campaign_id is None
            else sa.or_(s.c.campaign_id.is_(None), s.c.campaign_id == campaign_id)
        )
        return (
            sa.select(s)
            .where(s.c.superseded_at.is_(None), any_pair, applies)
            .order_by(s.c.created_at, s.c.id)
        )

    async def is_suppressed(
        self, *, scope: SuppressionScope, value_normalized: str, campaign_id: UUID | None = None
    ) -> bool:
        stmt = self._active_match([(SuppressionScope(scope), value_normalized)], campaign_id)
        return await fetch_one(self._s, stmt.limit(1)) is not None

    async def matching(
        self, *, values: dict[SuppressionScope, str], campaign_id: UUID | None = None
    ) -> Sequence[SuppressionEntry]:
        if not values:
            return ()
        pairs = [(SuppressionScope(k), v) for k, v in values.items()]
        return [
            dto(SuppressionEntry, r)
            for r in await fetch_all(self._s, self._active_match(pairs, campaign_id))
        ]

    async def supersede(self, entry_id: UUID, *, superseded_by: str) -> SuppressionEntry:
        s = t.suppression_entries
        stmt = (
            sa.update(s)
            .where(s.c.id == entry_id, s.c.superseded_at.is_(None))
            .values(superseded_at=ts_now(), superseded_by=superseded_by, updated_at=ts_now())
            .returning(s)
        )
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            row = await fetch_one(self._s, sa.select(s).where(s.c.id == entry_id))
            if row is None:
                raise LookupError(f"suppression entry {entry_id} not found")
        return dto(SuppressionEntry, row)
