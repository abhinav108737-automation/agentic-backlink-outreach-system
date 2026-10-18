"""In-memory repositories and unit of work for fast, DB-free service tests.

The fakes mirror the *documented* behaviour of the real repositories (optimistic locking,
append-only metrics, ELIGIBLE-with-missing rejection, settings versioning, single-decision
approvals, bounded job backoff). The unit of work snapshots the store on entry and restores it
when the block raises, so atomicity tests exercise real rollback semantics rather than hoping.

Test support only: nothing in the application imports this module.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

from kb_contracts.domain import (
    ApprovalRequest,
    Campaign,
    Contact,
    ContactChannel,
    Domain,
    DomainMetric,
    EvidenceItem,
    PolicyBody,
    PolicyVersion,
    Prospect,
    ProspectUrl,
    QualificationDecision,
    RankingResult,
    ResearchDossier,
    ScheduledJob,
    SuppressionEntry,
)
from kb_contracts.enums import (
    ApprovalKind,
    ApprovalStatus,
    CampaignStatus,
    JobStatus,
    ProspectStatus,
    QualificationOutcome,
    SuppressionReason,
    SuppressionScope,
)

from kb_core.db.page import CursorPage, decode_cursor, encode_cursor
from kb_core.db.protocols import OptimisticLockError
from kb_core.db.repos_runtime import ApprovalAlreadyDecided, InvalidJobTransition
from kb_core.scheduling.clock import FakeClock


@dataclass
class FakeStore:
    clock: FakeClock = field(default_factory=FakeClock)
    fail_audit: bool = False
    audit: list[dict[str, Any]] = field(default_factory=list)
    versions: dict[UUID, PolicyVersion] = field(default_factory=dict)
    global_version_id: UUID | None = None
    campaigns: dict[UUID, Campaign] = field(default_factory=dict)
    domains: dict[UUID, Domain] = field(default_factory=dict)
    metrics: list[DomainMetric] = field(default_factory=list)
    prospects: dict[UUID, Prospect] = field(default_factory=dict)
    urls: list[ProspectUrl] = field(default_factory=list)
    qualifications: list[QualificationDecision] = field(default_factory=list)
    rankings: list[RankingResult] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    dossiers: list[ResearchDossier] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    channels: list[ContactChannel] = field(default_factory=list)
    suppressions: list[SuppressionEntry] = field(default_factory=list)
    idempotency: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    jobs: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    approvals: dict[UUID, ApprovalRequest] = field(default_factory=dict)
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def now(self) -> datetime:
        return self.clock.advance(timedelta(milliseconds=1))

    # -- scenario helpers (tests only) ----------------------------------------------------
    def add_policy(self, body: PolicyBody | None = None, *, global_active: bool = True) -> UUID:
        now = self.now()
        vid = uuid4()
        self.versions[vid] = PolicyVersion(
            id=vid,
            created_at=now,
            updated_at=now,
            policy_id=uuid4(),
            version_no=len(self.versions) + 1,
            body=body or PolicyBody(),
            content_hash="sha256:test",
            created_by="test",
        )
        if global_active:
            self.global_version_id = vid
        return vid


_STORE_FIELDS = tuple(
    f.name for f in dataclasses.fields(FakeStore) if f.name not in ("clock", "fail_audit")
)


def _page[T](
    rows: Sequence[T], cursor: str | None, limit: int, created: Any = None
) -> CursorPage[T]:
    keyed = sorted(rows, key=lambda r: (r.created_at, r.id))  # type: ignore[attr-defined]
    if cursor is not None:
        at, rid = decode_cursor(cursor)
        keyed = [r for r in keyed if (r.created_at, r.id) > (at, rid)]  # type: ignore[attr-defined]
    page, more = keyed[:limit], len(keyed) > limit
    nxt = None
    if more:
        last = page[-1]
        nxt = encode_cursor(last.created_at, last.id)  # type: ignore[attr-defined]
    return CursorPage(page, nxt)


class _Repo:
    def __init__(self, store: FakeStore) -> None:
        self.s = store


class FakeAudit(_Repo):
    async def append(self, **kw: Any) -> UUID:
        if self.s.fail_audit:
            raise RuntimeError("audit store unavailable")
        row = {"id": uuid4(), "occurred_at": self.s.now(), **kw}
        self.s.audit.append(row)
        result: UUID = row["id"]
        return result

    async def list_for_subject(
        self, subject_type: str, subject_id: UUID, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return [
            r
            for r in self.s.audit
            if r["subject_type"] == subject_type and r["subject_id"] == subject_id
        ][:limit]


class FakePolicies(_Repo):
    async def get_version(self, policy_version_id: UUID) -> PolicyVersion | None:
        return self.s.versions.get(policy_version_id)

    async def active_global_version(self) -> PolicyVersion | None:
        return self.s.versions.get(self.s.global_version_id) if self.s.global_version_id else None

    async def active_version_for_campaign(self, campaign_id: UUID) -> PolicyVersion | None:
        campaign = self.s.campaigns.get(campaign_id)
        if campaign is not None and campaign.policy_version_id is not None:
            return self.s.versions.get(campaign.policy_version_id)
        return await self.active_global_version()


class FakeCampaigns(_Repo):
    async def get(self, campaign_id: UUID) -> Campaign | None:
        return self.s.campaigns.get(campaign_id)

    async def create(
        self,
        *,
        name: str,
        policy_version_id: UUID | None = None,
        goals: dict[str, Any] | None = None,
    ) -> Campaign:
        now = self.s.now()
        c = Campaign(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            name=name,
            status=CampaignStatus.DRAFT,
            policy_version_id=policy_version_id,
            goals=goals or {},
        )
        self.s.campaigns[c.id] = c
        return c

    async def list(self, *, cursor: str | None = None, limit: int = 50) -> CursorPage[Campaign]:
        return _page(list(self.s.campaigns.values()), cursor, limit)

    async def set_status(
        self, campaign_id: UUID, status: str, *, expected_version: int
    ) -> Campaign:
        cur = self.s.campaigns.get(campaign_id)
        if cur is None:
            raise LookupError(f"campaign {campaign_id} not found")
        if cur.version != expected_version:
            raise OptimisticLockError("campaigns", campaign_id, expected_version)
        new = cur.model_copy(
            update={
                "status": CampaignStatus(status),
                "version": cur.version + 1,
                "updated_at": self.s.now(),
            }
        )
        self.s.campaigns[campaign_id] = new
        return new


class FakeDomains(_Repo):
    async def get_or_create(self, registered_domain: str) -> Domain:
        name = registered_domain.strip().lower()
        for d in self.s.domains.values():
            if d.registered_domain == name:
                return d
        now = self.s.now()
        d = Domain(
            id=uuid4(), created_at=now, updated_at=now, registered_domain=name, first_seen_at=now
        )
        self.s.domains[d.id] = d
        return d

    async def get(self, domain_id: UUID) -> Domain | None:
        return self.s.domains.get(domain_id)


class FakeDomainMetrics(_Repo):
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
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        now = self.s.now()
        m = DomainMetric(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            domain_id=domain_id,
            provider=provider,
            metric=metric,
            value=value,
            fetched_at=fetched_at,
            source=source,
        )
        self.s.metrics.append(m)
        return m

    async def latest(self, domain_id: UUID, metrics: Sequence[str]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        newest: dict[str, tuple[datetime, datetime]] = {}
        for m in self.s.metrics:
            if m.domain_id != domain_id or m.metric not in metrics:
                continue
            key = (m.fetched_at, m.created_at)
            if m.metric not in newest or key > newest[m.metric]:
                newest[m.metric] = key
                out[m.metric] = m.value
        return out


class FakeProspects(_Repo):
    async def get(self, prospect_id: UUID) -> Prospect | None:
        return self.s.prospects.get(prospect_id)

    async def get_by_domain(self, campaign_id: UUID, domain_id: UUID) -> Prospect | None:
        for p in self.s.prospects.values():
            if p.campaign_id == campaign_id and p.domain_id == domain_id:
                return p
        return None

    async def create(
        self, *, campaign_id: UUID, domain_id: UUID, discovered_via: str | None = None
    ) -> Prospect:
        existing = await self.get_by_domain(campaign_id, domain_id)
        if existing is not None:
            return existing
        now = self.s.now()
        p = Prospect(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            campaign_id=campaign_id,
            domain_id=domain_id,
            status=ProspectStatus.DISCOVERED,
            discovered_via=discovered_via,
        )
        self.s.prospects[p.id] = p
        return p

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        status: ProspectStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CursorPage[Prospect]:
        rows = [
            p
            for p in self.s.prospects.values()
            if (campaign_id is None or p.campaign_id == campaign_id)
            and (status is None or p.status == status)
        ]
        return _page(rows, cursor, limit)

    async def set_status(
        self, prospect_id: UUID, status: ProspectStatus, *, expected_version: int
    ) -> Prospect:
        cur = self.s.prospects.get(prospect_id)
        if cur is None:
            raise LookupError(f"prospect {prospect_id} not found")
        if cur.version != expected_version:
            raise OptimisticLockError("prospects", prospect_id, expected_version)
        new = cur.model_copy(
            update={
                "status": ProspectStatus(status),
                "version": cur.version + 1,
                "updated_at": self.s.now(),
            }
        )
        self.s.prospects[prospect_id] = new
        return new

    async def count_by_status(self, campaign_id: UUID) -> dict[ProspectStatus, int]:
        counts = dict.fromkeys(ProspectStatus, 0)
        for p in self.s.prospects.values():
            if p.campaign_id == campaign_id:
                counts[p.status] += 1
        return counts


class FakeProspectUrls(_Repo):
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
        for u in self.s.urls:
            if u.prospect_id == prospect_id and u.url_hash == url_hash:
                return u
        now = self.s.now()
        u = ProspectUrl(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            prospect_id=prospect_id,
            url=url,
            canonical_url=canonical_url,
            url_hash=url_hash,
            page_type=page_type,
            provenance=provenance or {},
        )
        self.s.urls.append(u)
        return u

    async def list_for_prospect(self, prospect_id: UUID) -> list[ProspectUrl]:
        return [u for u in self.s.urls if u.prospect_id == prospect_id]


class FakeQualifications(_Repo):
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
            raise ValueError("an ELIGIBLE decision cannot list missing_metrics")
        now = self.s.now()
        d = QualificationDecision(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            prospect_id=prospect_id,
            policy_version_id=policy_version_id,
            outcome=out,
            reason_codes=tuple(reason_codes),
            metric_snapshot=dict(metric_snapshot),
            missing_metrics=tuple(missing_metrics),
        )
        self.s.qualifications.append(d)
        return d

    async def latest(self, prospect_id: UUID) -> QualificationDecision | None:
        rows = [d for d in self.s.qualifications if d.prospect_id == prospect_id]
        return rows[-1] if rows else None


class FakeRankings(_Repo):
    async def record(
        self,
        *,
        prospect_id: UUID,
        policy_version_id: UUID,
        score: float,
        components: dict[str, float],
        explanation: str,
    ) -> RankingResult:
        now = self.s.now()
        r = RankingResult(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            prospect_id=prospect_id,
            policy_version_id=policy_version_id,
            score=score,
            components=dict(components),
            explanation=explanation,
        )
        self.s.rankings.append(r)
        return r

    async def latest(self, prospect_id: UUID) -> RankingResult | None:
        rows = [r for r in self.s.rankings if r.prospect_id == prospect_id]
        return rows[-1] if rows else None


class FakeEvidence(_Repo):
    async def record_many(
        self, prospect_id: UUID, items: Sequence[dict[str, Any]], *, dossier_id: UUID | None = None
    ) -> list[EvidenceItem]:
        out = []
        for it in items:
            now = self.s.now()
            e = EvidenceItem(
                id=uuid4(),
                created_at=now,
                updated_at=now,
                prospect_id=prospect_id,
                dossier_id=dossier_id,
                **it,
            )
            self.s.evidence.append(e)
            out.append(e)
        return out

    async def list_for_prospect(self, prospect_id: UUID) -> list[EvidenceItem]:
        return [e for e in self.s.evidence if e.prospect_id == prospect_id]

    async def get_many(self, evidence_ids: Sequence[UUID]) -> list[EvidenceItem]:
        return [e for e in self.s.evidence if e.id in evidence_ids]


class FakeDossiers(_Repo):
    async def create(
        self,
        *,
        prospect_id: UUID,
        summary: str,
        topics: Sequence[str],
        editorial_signals: dict[str, Any],
        agent_run_id: UUID | None = None,
    ) -> ResearchDossier:
        now = self.s.now()
        n = 1 + sum(1 for d in self.s.dossiers if d.prospect_id == prospect_id)
        d = ResearchDossier(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            prospect_id=prospect_id,
            version_no=n,
            summary=summary,
            topics=tuple(topics),
            editorial_signals=editorial_signals,
        )
        self.s.dossiers.append(d)
        return d

    async def latest(self, prospect_id: UUID) -> ResearchDossier | None:
        rows = [d for d in self.s.dossiers if d.prospect_id == prospect_id]
        return rows[-1] if rows else None


class FakeContacts(_Repo):
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
        now = self.s.now()
        c = Contact(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            domain_id=domain_id,
            name=name,
            role=role,
            contact_type=contact_type,
            confidence=confidence,
            provenance=provenance or {},
        )
        self.s.contacts.append(c)
        return c

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
        for ch in self.s.channels:
            if (ch.contact_id, ch.channel_type, ch.value_normalized) == (
                contact_id,
                channel_type,
                value_normalized,
            ):
                return ch
        now = self.s.now()
        ch = ContactChannel(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            contact_id=contact_id,
            channel_type=channel_type,
            value_normalized=value_normalized,
            confidence=confidence,
            evidence_item_id=evidence_item_id,
        )
        self.s.channels.append(ch)
        return ch

    async def list_for_domain(self, domain_id: UUID) -> list[Contact]:
        return [c for c in self.s.contacts if c.domain_id == domain_id]

    async def list_channels(self, domain_id: UUID) -> list[ContactChannel]:
        ids = {c.id for c in self.s.contacts if c.domain_id == domain_id}
        return [ch for ch in self.s.channels if ch.contact_id in ids]


class FakeSuppressions(_Repo):
    @staticmethod
    def _n(value: str) -> str:
        return value.strip().lower()

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
        v = self._n(value_normalized)
        for e in self.s.suppressions:
            if (
                e.scope == scope
                and e.value_normalized == v
                and e.superseded_at is None
                and e.campaign_id == campaign_id
            ):
                return e
        now = self.s.now()
        e = SuppressionEntry(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            scope=SuppressionScope(scope),
            value_normalized=v,
            reason=SuppressionReason(reason),
            campaign_id=campaign_id,
        )
        self.s.suppressions.append(e)
        return e

    async def matching(
        self, *, values: dict[SuppressionScope, str], campaign_id: UUID | None = None
    ) -> list[SuppressionEntry]:
        wanted = {(SuppressionScope(k), self._n(v)) for k, v in values.items()}
        return [
            e
            for e in self.s.suppressions
            if e.superseded_at is None
            and (e.scope, e.value_normalized) in wanted
            and (e.campaign_id is None or e.campaign_id == campaign_id)
        ]

    async def is_suppressed(
        self, *, scope: SuppressionScope, value_normalized: str, campaign_id: UUID | None = None
    ) -> bool:
        return bool(await self.matching(values={scope: value_normalized}, campaign_id=campaign_id))

    async def supersede(self, entry_id: UUID, *, superseded_by: str) -> SuppressionEntry:
        for i, e in enumerate(self.s.suppressions):
            if e.id == entry_id:
                if e.superseded_at is None:
                    e = e.model_copy(update={"superseded_at": self.s.now()})
                    self.s.suppressions[i] = e
                return e
        raise LookupError(f"suppression entry {entry_id} not found")


class FakeIdempotency(_Repo):
    async def claim(self, *, scope: str, key: str, request_hash: str) -> bool:
        if (scope, key) in self.s.idempotency:
            return False
        self.s.idempotency[(scope, key)] = {
            "scope": scope,
            "key": key,
            "request_hash": request_hash,
            "response_ref": None,
        }
        return True

    async def get(self, *, scope: str, key: str) -> dict[str, Any] | None:
        row = self.s.idempotency.get((scope, key))
        return dict(row) if row else None

    async def record_response(self, *, scope: str, key: str, response_ref: str) -> None:
        if (scope, key) not in self.s.idempotency:
            raise LookupError(f"idempotency record {scope}/{key} not found")
        self.s.idempotency[(scope, key)]["response_ref"] = response_ref


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


class FakeJobs(_Repo):
    """Mirrors SqlJobRepository: promote elapsed RETRY_WAIT, one job per concurrency_key,
    bounded attempts -> DEAD, aware datetimes only."""

    @staticmethod
    def _aware(value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"{field_name} must be timezone-aware (UTC); got a naive datetime")
        return value

    def _dto(self, row: dict[str, Any]) -> ScheduledJob:
        return ScheduledJob(
            id=row["id"],
            created_at=row["created_at"],
            updated_at=row["created_at"],
            job_type=row["job_type"],
            payload=row["payload"],
            run_at=row["run_at"],
            status=row["status"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            idempotency_key=row["idempotency_key"],
            concurrency_key=row["concurrency_key"],
        )

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
        self._aware(run_at, "run_at")
        for row in self.s.jobs.values():
            if row["idempotency_key"] == idempotency_key:
                return self._dto(row)
        row = {
            "id": uuid4(),
            "created_at": self.s.now(),
            "job_type": job_type,
            "payload": payload,
            "run_at": run_at,
            "status": JobStatus.SCHEDULED,
            "attempts": 0,
            "max_attempts": max_attempts,
            "idempotency_key": idempotency_key,
            "concurrency_key": concurrency_key,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error": None,
        }
        self.s.jobs[row["id"]] = row
        return self._dto(row)

    async def claim_due(
        self, *, now: datetime, lease_owner: str, lease_seconds: int, limit: int = 1
    ) -> list[ScheduledJob]:
        self._aware(now, "now")
        for row in self.s.jobs.values():
            if row["status"] is JobStatus.RETRY_WAIT and row["run_at"] <= now:
                row["status"] = JobStatus.SCHEDULED
        busy = {
            r["concurrency_key"]
            for r in self.s.jobs.values()
            if r["status"] is JobStatus.RUNNING and r["concurrency_key"] is not None
        }
        due = sorted(
            (
                r
                for r in self.s.jobs.values()
                if r["status"] is JobStatus.SCHEDULED
                and r["run_at"] <= now
                and r["concurrency_key"] not in busy
            ),
            key=lambda r: (r["run_at"], r["created_at"]),
        )[:limit]
        picked, seen = [], set()
        for r in due:
            key = r["concurrency_key"]
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            r["status"] = JobStatus.RUNNING
            r["lease_owner"] = lease_owner
            r["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
            picked.append(self._dto(r))
        return picked

    async def complete(self, job_id: UUID, *, status: JobStatus, last_error: str | None) -> None:
        outcome = JobStatus(status)
        if outcome not in (
            JobStatus.SUCCEEDED,
            JobStatus.RETRY_WAIT,
            JobStatus.DEAD,
            JobStatus.SCHEDULED,
        ):
            raise InvalidJobTransition(f"{outcome} is not a legal outcome from RUNNING")
        row = self.s.jobs.get(job_id)
        if row is None or row["status"] is not JobStatus.RUNNING:
            raise InvalidJobTransition(f"job {job_id} is not RUNNING")
        row.update(status=outcome, last_error=last_error, lease_owner=None, lease_expires_at=None)
        if outcome in (JobStatus.RETRY_WAIT, JobStatus.SCHEDULED):
            row["attempts"] += 1
        if outcome is JobStatus.RETRY_WAIT:
            if row["attempts"] >= row["max_attempts"]:
                row["status"] = JobStatus.DEAD
            else:
                row["run_at"] = self.s.clock.now() + _backoff(row["attempts"])

    async def cancel(self, *, idempotency_key: str) -> bool:
        for row in self.s.jobs.values():
            if row["idempotency_key"] == idempotency_key and row["status"] in (
                JobStatus.SCHEDULED,
                JobStatus.RETRY_WAIT,
            ):
                row["status"] = JobStatus.CANCELLED
                return True
        return False

    async def cancel_for_subject(self, *, subject_type: str, subject_id: UUID) -> int:
        n = 0
        for row in self.s.jobs.values():
            if (
                row["subject_type"] == subject_type
                and row["subject_id"] == subject_id
                and row["status"] in (JobStatus.SCHEDULED, JobStatus.RETRY_WAIT)
            ):
                row["status"] = JobStatus.CANCELLED
                n += 1
        return n

    async def reclaim_expired(self, *, now: datetime) -> int:
        self._aware(now, "now")
        n = 0
        for row in self.s.jobs.values():
            if row["status"] is JobStatus.RUNNING and row["lease_expires_at"] < now:
                row["attempts"] += 1
                row["status"] = (
                    JobStatus.DEAD
                    if row["attempts"] >= row["max_attempts"]
                    else JobStatus.SCHEDULED
                )
                row.update(lease_owner=None, lease_expires_at=None, last_error="lease expired")
                n += 1
        return n


class FakeApprovals(_Repo):
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
        now = self.s.now()
        a = ApprovalRequest(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            kind=ApprovalKind(kind),
            subject_type=subject_type,
            subject_id=subject_id,
            status=ApprovalStatus.PENDING,
            requested_by=requested_by,
            reason=reason,
        )
        self.s.approvals[a.id] = a
        return a

    async def get(self, approval_id: UUID) -> ApprovalRequest | None:
        return self.s.approvals.get(approval_id)

    async def decide(
        self, approval_id: UUID, *, status: ApprovalStatus, decided_by: str, reason: str | None
    ) -> ApprovalRequest:
        if ApprovalStatus(status) is ApprovalStatus.PENDING:
            raise ValueError("an approval cannot be decided as PENDING")
        cur = self.s.approvals.get(approval_id)
        if cur is None:
            raise LookupError(f"approval {approval_id} not found")
        if cur.status is not ApprovalStatus.PENDING:
            raise ApprovalAlreadyDecided(f"approval {approval_id} is not PENDING")
        new = cur.model_copy(
            update={
                "status": ApprovalStatus(status),
                "decided_by": decided_by,
                "reason": reason,
                "updated_at": self.s.now(),
            }
        )
        self.s.approvals[approval_id] = new
        return new

    async def list_pending(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> CursorPage[ApprovalRequest]:
        rows = [a for a in self.s.approvals.values() if a.status is ApprovalStatus.PENDING]
        return _page(rows, cursor, limit)

    async def latest_for_subject(
        self, *, subject_type: str, subject_id: UUID, kind: str
    ) -> ApprovalRequest | None:
        rows = [
            a
            for a in self.s.approvals.values()
            if a.subject_type == subject_type
            and a.subject_id == subject_id
            and a.kind == ApprovalKind(kind)
        ]
        return max(rows, key=lambda a: (a.created_at, a.id)) if rows else None


class FakeSettings(_Repo):
    async def get(self, key: str) -> dict[str, Any] | None:
        row = self.s.settings.get(key)
        return {"key": key, "value": row["value"], "version": row["version"]} if row else None

    async def set(self, key: str, value: dict[str, Any], *, expected_version: int | None) -> int:
        row = self.s.settings.get(key)
        if expected_version is None:
            version = (row["version"] + 1) if row else 1
        elif expected_version == 0:
            if row is not None:
                raise OptimisticLockError("system_settings", UUID(int=0), 0)
            version = 1
        else:
            if row is None or row["version"] != expected_version:
                raise OptimisticLockError("system_settings", UUID(int=0), expected_version)
            version = expected_version + 1
        self.s.settings[key] = {"value": dict(value), "version": version}
        return version


class FakeAgentRuns(_Repo):
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"FakeAgentRuns.{name} is not implemented")


class FakeUnitOfWork:
    """Snapshot on entry, restore on exception: a faithful stand-in for commit/rollback."""

    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self._snapshot: dict[str, Any] | None = None
        self._entered = False
        self.audit: Any = FakeAudit(store)
        self.policies: Any = FakePolicies(store)
        self.campaigns: Any = FakeCampaigns(store)
        self.domains: Any = FakeDomains(store)
        self.domain_metrics: Any = FakeDomainMetrics(store)
        self.prospects: Any = FakeProspects(store)
        self.prospect_urls: Any = FakeProspectUrls(store)
        self.qualifications: Any = FakeQualifications(store)
        self.rankings: Any = FakeRankings(store)
        self.evidence: Any = FakeEvidence(store)
        self.dossiers: Any = FakeDossiers(store)
        self.contacts: Any = FakeContacts(store)
        self.suppressions: Any = FakeSuppressions(store)
        self.idempotency: Any = FakeIdempotency(store)
        self.jobs: Any = FakeJobs(store)
        self.approvals: Any = FakeApprovals(store)
        self.settings: Any = FakeSettings(store)
        self.agent_runs: Any = FakeAgentRuns(store)

    async def __aenter__(self) -> FakeUnitOfWork:
        if self._entered:
            raise RuntimeError("a unit of work is single-use")
        self._entered = True
        self._snapshot = {f: copy.deepcopy(getattr(self.store, f)) for f in _STORE_FIELDS}
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None and self._snapshot is not None:
            for name, value in self._snapshot.items():
                setattr(self.store, name, value)
            # repositories hold the store, not the dicts, so the restore is visible to them

    async def flush(self) -> None:
        return None


class FakeUowFactory:
    def __init__(self, store: FakeStore | None = None) -> None:
        self.store = store or FakeStore()

    def __call__(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.store)
