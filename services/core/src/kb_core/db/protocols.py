"""Repository and unit-of-work seam between the data layer (Lane C) and the services (Lane B).

This is **not** a frozen contract (contracts/** is), but it is frozen for Wave 1: Lane C
implements these protocols against PostgreSQL and Lane B consumes them, so changing a
signature mid-wave breaks the other lane. Changes go through the Lead.

Design rules this seam exists to enforce:
- Every consequential write happens inside one `UnitOfWork`, so a state transition and its
  `audit_events` row commit or roll back together (docs/13 enforcement rule 4).
- Services never see a SQLAlchemy session, a connection or raw SQL. `raw_sql` is a forbidden
  agent capability and services keep the same discipline, which keeps the data layer swappable.
- Repositories return frozen `kb_contracts.domain` DTOs, never ORM rows, so no lane leaks
  persistence details into the contract surface.
- Status columns are never written through a repository `update`. Transitions go through
  `TransitionService`, which is the only writer of `status` (docs/13: agents and services never
  set status columns ad hoc).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from kb_contracts.domain import (
    ApprovalRequest,
    Backlink,
    BacklinkVerification,
    Campaign,
    CampaignTarget,
    Contact,
    ContactChannel,
    Domain,
    DomainMetric,
    EvidenceItem,
    GuestPostOpportunity,
    OutreachMessage,
    OutreachSequence,
    PolicyVersion,
    Prospect,
    ProspectUrl,
    QualificationDecision,
    RankingResult,
    Reply,
    ReplyClassification,
    ResearchDossier,
    ScheduledJob,
    SuppressionEntry,
)
from kb_contracts.enums import (
    ApprovalStatus,
    BacklinkStatus,
    JobStatus,
    MessageStatus,
    OpportunityStatus,
    ProspectStatus,
    SequenceStatus,
    SuppressionScope,
)

# --------------------------------------------------------------------------------------------
# Supporting value types
# --------------------------------------------------------------------------------------------


class Page[T](Protocol):
    """Cursor page. C8 specifies cursor pagination, so repositories never expose offsets."""

    items: Sequence[T]
    next_cursor: str | None


class OptimisticLockError(Exception):
    """A versioned row changed under us. Raised by `update_*` when `expected_version` is stale."""

    def __init__(self, table: str, row_id: UUID, expected: int) -> None:
        super().__init__(f"{table} {row_id}: version {expected} is stale")
        self.table, self.row_id, self.expected = table, row_id, expected


# --------------------------------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------------------------------


class AuditRepository(Protocol):
    """`audit_events` is append-only at the DB level: no update, no delete, no truncate."""

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
    ) -> UUID: ...

    async def list_for_subject(
        self, subject_type: str, subject_id: UUID, *, limit: int = 100
    ) -> Sequence[dict[str, Any]]: ...


class PolicyRepository(Protocol):
    async def get_version(self, policy_version_id: UUID) -> PolicyVersion | None: ...

    async def active_version_for_campaign(self, campaign_id: UUID) -> PolicyVersion | None:
        """The campaign's pinned version, else the ACTIVE global version."""
        ...

    async def active_global_version(self) -> PolicyVersion | None: ...


class CampaignRepository(Protocol):
    async def get(self, campaign_id: UUID) -> Campaign | None: ...
    async def create(
        self,
        *,
        name: str,
        policy_version_id: UUID | None = None,
        goals: dict[str, Any] | None = None,
    ) -> Campaign: ...
    async def list(self, *, cursor: str | None = None, limit: int = 50) -> Page[Campaign]: ...
    async def set_status(
        self, campaign_id: UUID, status: str, *, expected_version: int
    ) -> Campaign:
        """Transition writer only. Raises `OptimisticLockError` on a stale version."""
        ...

    async def list_targets(self, campaign_id: UUID) -> Sequence[CampaignTarget]:
        """The campaign's targets, ordered by descending priority. Added at I1: discovery
        needs the campaign's topics and seed keywords, and reading them is the only way an
        agent can learn what to look for without being handed the database."""
        ...


class DomainRepository(Protocol):
    async def get_or_create(self, registered_domain: str) -> Domain: ...
    async def get(self, domain_id: UUID) -> Domain | None: ...


class DomainMetricRepository(Protocol):
    """`domain_metrics` is append-only: a new reading is a new row, never an update."""

    async def record(
        self,
        *,
        domain_id: UUID,
        provider: str,
        metric: str,
        value: float | None,
        source: str,
        fetched_at: datetime,
    ) -> DomainMetric: ...

    async def latest(self, domain_id: UUID, metrics: Sequence[str]) -> dict[str, float | None]:
        """Latest reading per metric. A metric absent from the mapping was never fetched; a
        metric mapped to None was fetched and unavailable. Both mean DEFER, never pass
        (ADR-0015) — the caller must be able to tell them apart, so do not collapse them."""
        ...


class ProspectRepository(Protocol):
    async def get(self, prospect_id: UUID) -> Prospect | None: ...
    async def get_by_domain(self, campaign_id: UUID, domain_id: UUID) -> Prospect | None: ...
    async def create(
        self, *, campaign_id: UUID, domain_id: UUID, discovered_via: str | None = None
    ) -> Prospect:
        """UNIQUE(campaign_id, domain_id): re-discovery must not create a second prospect."""
        ...

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        status: ProspectStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[Prospect]: ...

    async def set_status(
        self, prospect_id: UUID, status: ProspectStatus, *, expected_version: int
    ) -> Prospect:
        """Transition writer only. Raises `OptimisticLockError` on a stale version."""
        ...

    async def count_by_status(self, campaign_id: UUID) -> dict[ProspectStatus, int]: ...


class ProspectUrlRepository(Protocol):
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
        """Idempotent on UNIQUE(prospect_id, url_hash) — dedupe is enforced by the DB."""
        ...

    async def list_for_prospect(self, prospect_id: UUID) -> Sequence[ProspectUrl]: ...


class QualificationRepository(Protocol):
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
    ) -> QualificationDecision: ...

    async def latest(self, prospect_id: UUID) -> QualificationDecision | None: ...


class RankingRepository(Protocol):
    async def record(
        self,
        *,
        prospect_id: UUID,
        policy_version_id: UUID,
        score: float,
        components: dict[str, float],
        explanation: str,
    ) -> RankingResult: ...

    async def latest(self, prospect_id: UUID) -> RankingResult | None: ...


class EvidenceRepository(Protocol):
    async def record_many(
        self,
        prospect_id: UUID,
        items: Sequence[dict[str, Any]],
        *,
        dossier_id: UUID | None = None,
    ) -> Sequence[EvidenceItem]:
        """Each item carries source_url, excerpt, content_hash, kind, extracted_at, extractor."""
        ...

    async def list_for_prospect(self, prospect_id: UUID) -> Sequence[EvidenceItem]: ...
    async def get_many(self, evidence_ids: Sequence[UUID]) -> Sequence[EvidenceItem]:
        """Used to verify that every drafted claim maps to real evidence."""
        ...


class DossierRepository(Protocol):
    async def create(
        self,
        *,
        prospect_id: UUID,
        summary: str,
        topics: Sequence[str],
        editorial_signals: dict[str, Any],
        agent_run_id: UUID | None = None,
    ) -> ResearchDossier:
        """Allocates the next version_no for the prospect (UNIQUE(prospect_id, version_no))."""
        ...

    async def latest(self, prospect_id: UUID) -> ResearchDossier | None: ...


class ContactRepository(Protocol):
    async def create(
        self,
        *,
        domain_id: UUID,
        name: str | None,
        role: str | None,
        contact_type: str,
        confidence: float,
        provenance: dict[str, Any] | None = None,
    ) -> Contact: ...

    async def add_channel(
        self,
        *,
        contact_id: UUID,
        channel_type: str,
        value_normalized: str,
        confidence: float,
        evidence_item_id: UUID | None = None,
    ) -> ContactChannel:
        """Idempotent on UNIQUE(contact_id, channel_type, value_normalized).
        `value_normalized` must already be lower(btrim(...))."""
        ...

    async def list_for_domain(self, domain_id: UUID) -> Sequence[Contact]: ...
    async def list_channels(self, domain_id: UUID) -> Sequence[ContactChannel]: ...


class SuppressionRepository(Protocol):
    """`suppression_entries` is guarded: only `superseded_at`/`superseded_by` may be updated,
    and rows can never be deleted or truncated. There is no `remove`; superseding is the
    only way out, and it stays auditable."""

    async def add(
        self,
        *,
        scope: SuppressionScope,
        value_normalized: str,
        reason: str,
        campaign_id: UUID | None = None,
        created_by: str = "system",
        source_event_ref: str | None = None,
    ) -> SuppressionEntry: ...

    async def is_suppressed(
        self, *, scope: SuppressionScope, value_normalized: str, campaign_id: UUID | None = None
    ) -> bool:
        """True if an active (not superseded) entry matches, global or campaign-scoped."""
        ...

    async def matching(
        self, *, values: dict[SuppressionScope, str], campaign_id: UUID | None = None
    ) -> Sequence[SuppressionEntry]: ...

    async def supersede(self, entry_id: UUID, *, superseded_by: str) -> SuppressionEntry: ...


class IdempotencyRepository(Protocol):
    async def claim(self, *, scope: str, key: str, request_hash: str) -> bool:
        """True if this caller claimed the key; False if it was already claimed. Relies on
        UNIQUE(scope, key), so two concurrent callers cannot both win."""
        ...

    async def get(self, *, scope: str, key: str) -> dict[str, Any] | None: ...
    async def record_response(self, *, scope: str, key: str, response_ref: str) -> None: ...


class JobRepository(Protocol):
    """PostgreSQL job queue (ADR-0008/0017). No Redis, no Celery."""

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
        """Idempotent on UNIQUE(idempotency_key): returns the existing job if already queued."""
        ...

    async def claim_due(
        self, *, now: datetime, lease_owner: str, lease_seconds: int, limit: int = 1
    ) -> Sequence[ScheduledJob]:
        """Claims SCHEDULED jobs with run_at <= now using FOR UPDATE SKIP LOCKED, sets the
        lease and moves them to RUNNING. Concurrent workers must never claim the same job."""
        ...

    async def complete(self, job_id: UUID, *, status: JobStatus, last_error: str | None) -> None:
        """Terminal or retry outcome. Only SUCCEEDED, RETRY_WAIT, DEAD, SCHEDULED are legal
        from RUNNING (docs/13 §6)."""
        ...

    async def cancel(self, *, idempotency_key: str) -> bool: ...
    async def cancel_for_subject(self, *, subject_type: str, subject_id: UUID) -> int:
        """Cancels pending jobs for a subject. Entering a terminal sequence state must cancel
        its pending jobs in the SAME transaction, so this is called inside the UoW."""
        ...

    async def reclaim_expired(self, *, now: datetime) -> int:
        """RUNNING jobs whose lease expired go back to SCHEDULED with attempts += 1."""
        ...


class ApprovalRepository(Protocol):
    async def create(
        self,
        *,
        kind: str,
        subject_type: str,
        subject_id: UUID,
        requested_by: str,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> ApprovalRequest: ...

    async def get(self, approval_id: UUID) -> ApprovalRequest | None: ...
    async def decide(
        self, approval_id: UUID, *, status: ApprovalStatus, decided_by: str, reason: str | None
    ) -> ApprovalRequest: ...
    async def list_pending(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> Page[ApprovalRequest]: ...
    async def latest_for_subject(
        self, *, subject_type: str, subject_id: UUID, kind: str
    ) -> ApprovalRequest | None: ...


class SettingsRepository(Protocol):
    """`system_settings` holds the kill switch and runtime mode. Versioned for locking."""

    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def set(self, key: str, value: dict[str, Any], *, expected_version: int | None) -> int:
        """Returns the new version. Raises `OptimisticLockError` on a stale version."""
        ...


class AgentRunRepository(Protocol):
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
    ) -> UUID: ...

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
    ) -> None: ...

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
    ) -> UUID: ...

    async def get(self, agent_run_id: UUID) -> dict[str, Any] | None: ...
    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        agent: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[dict[str, Any]]: ...


# --------------------------------------------------------------------------------------------
# Outreach (Wave 2A). Added by the Lead when both lanes blocked on the absence of these: the
# tables, the DTOs and the state machines all existed, only the repositories were missing.
#
# Nothing here writes a `status` column except the `set_status` methods, which exist so that
# `TransitionService` can perform a guarded, audited, optimistically-locked transition. No
# other caller may use them to move a state machine directly.
# --------------------------------------------------------------------------------------------


class SequenceRepository(Protocol):
    async def get(self, sequence_id: UUID) -> OutreachSequence | None: ...
    async def create(
        self,
        *,
        campaign_id: UUID,
        prospect_id: UUID,
        contact_channel_id: UUID,
        policy_version_id: UUID | None = None,
    ) -> OutreachSequence: ...

    async def list(
        self,
        *,
        campaign_id: UUID | None = None,
        status: SequenceStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[OutreachSequence]: ...

    async def set_status(
        self, sequence_id: UUID, status: SequenceStatus, *, expected_version: int
    ) -> OutreachSequence:
        """Transition writer only. Raises `OptimisticLockError` on a stale version."""
        ...

    async def set_next_due(self, sequence_id: UUID, *, run_at: datetime | None) -> None: ...
    async def advance_step(self, sequence_id: UUID) -> int:
        """Increment `current_step` and return the new value."""
        ...


class MessageRepository(Protocol):
    async def get(self, message_id: UUID) -> OutreachMessage | None: ...
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
        """Honours `UNIQUE(sequence_id, step_no, message_version)` and the
        `one_live_message_per_step` partial index: a second live message for a step is a
        conflict, not a silent second row."""
        ...

    async def get_by_idempotency_key(self, key: str) -> OutreachMessage | None:
        """The crash-recovery lookup. A message found in SENDING is reconciled by this key
        before any retry, so a send is never duplicated."""
        ...

    async def live_for_step(self, sequence_id: UUID, step_no: int) -> OutreachMessage | None:
        """The not-yet-terminal message for a step, if any. Backs DUPLICATE_STEP."""
        ...

    async def live_for_channel(
        self, campaign_id: UUID, contact_channel_id: UUID
    ) -> Sequence[OutreachMessage]:
        """Live messages already aimed at a contact channel in this campaign. Backs
        DUPLICATE_CONTACT."""
        ...

    async def list_for_sequence(self, sequence_id: UUID) -> Sequence[OutreachMessage]: ...

    async def count_sent_since(
        self,
        *,
        since: datetime,
        campaign_id: UUID | None = None,
        domain_id: UUID | None = None,
        contact_channel_id: UUID | None = None,
    ) -> int:
        """Messages that left the system within a window, for the three send limits. Counts
        SENT, DELIVERED and BOUNCED: all of them were sent, so all of them consume quota."""
        ...

    async def set_status(
        self, message_id: UUID, status: MessageStatus, *, expected_version: int
    ) -> OutreachMessage:
        """Transition writer only. Raises `OptimisticLockError` on a stale version."""
        ...

    async def record_qa(self, message_id: UUID, qa_result: dict[str, Any]) -> None: ...

    async def mark_sent(
        self, message_id: UUID, *, provider_message_id: str, expected_version: int
    ) -> OutreachMessage:
        """Attaches the provider id as part of the SENDING -> SENT transition."""
        ...

    async def bump_attempts(self, message_id: UUID) -> int: ...


class ThreadRepository(Protocol):
    async def get(self, thread_id: UUID) -> dict[str, Any] | None: ...
    async def get_or_create_for_sequence(
        self, sequence_id: UUID, *, subject: str, provider_thread_id: str | None = None
    ) -> dict[str, Any]: ...
    async def list_replies(self, thread_id: UUID) -> Sequence[Reply]: ...


class ReplyRepository(Protocol):
    """A reply is a received fact: ingest is idempotent on `provider_message_id`, so
    re-polling a mailbox cannot duplicate it."""

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
        """Returns (reply, created). `created=False` means this provider message was already
        ingested and the existing row is returned unchanged."""
        ...

    async def get(self, reply_id: UUID) -> Reply | None: ...
    async def list_for_sequence(self, sequence_id: UUID) -> Sequence[Reply]: ...

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
    ) -> ReplyClassification: ...

    async def latest_classification(self, reply_id: UUID) -> ReplyClassification | None: ...


class OpportunityRepository(Protocol):
    async def get(self, opportunity_id: UUID) -> GuestPostOpportunity | None: ...
    async def create(
        self,
        *,
        prospect_id: UUID,
        sequence_id: UUID | None = None,
        target_url: str | None = None,
    ) -> GuestPostOpportunity: ...

    async def list(
        self,
        *,
        status: OpportunityStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[GuestPostOpportunity]: ...

    async def set_status(
        self, opportunity_id: UUID, status: OpportunityStatus, *, expected_version: int
    ) -> GuestPostOpportunity:
        """Transition writer only. Raises `OptimisticLockError` on a stale version."""
        ...

    async def set_terms(
        self, opportunity_id: UUID, *, agreed_terms: dict[str, Any], requires_approval: bool
    ) -> GuestPostOpportunity: ...

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
        """Appends the next `round_no` for this opportunity."""
        ...

    async def record_submission(
        self,
        *,
        opportunity_id: UUID,
        title: str,
        content_ref: str,
        published_url: str | None = None,
    ) -> dict[str, Any]:
        """Appends the next `version_no` for this opportunity."""
        ...


class BacklinkRepository(Protocol):
    """`backlink_verifications` is append-only: every check is a new row, never an update."""

    async def get(self, backlink_id: UUID) -> Backlink | None: ...
    async def create(
        self,
        *,
        opportunity_id: UUID | None,
        source_url: str,
        target_url: str,
        expected_anchor: str | None = None,
    ) -> Backlink:
        """Created in EXPECTED when an opportunity reaches PUBLISHED (docs/13 section 3)."""
        ...

    async def list(
        self,
        *,
        status: BacklinkStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[Backlink]: ...

    async def set_status(
        self, backlink_id: UUID, status: BacklinkStatus, *, expected_version: int
    ) -> Backlink:
        """Transition writer only. Raises `OptimisticLockError` on a stale version."""
        ...

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
    ) -> BacklinkVerification: ...

    async def list_verifications(self, backlink_id: UUID) -> Sequence[BacklinkVerification]: ...

    async def set_miss_counters(
        self, backlink_id: UUID, *, consecutive_misses: int, seen: bool, checked_at: datetime
    ) -> None:
        """Maintains `consecutive_misses` plus first/last seen and last checked. The
        MISSING -> LOST threshold is a service decision, not a repository one."""
        ...


class ProviderAccountRepository(Protocol):
    async def get(self, provider_account_id: UUID) -> dict[str, Any] | None: ...
    async def list_enabled(self, provider_type: str) -> Sequence[dict[str, Any]]:
        """Enabled accounts of a type. `is_paid` is returned so a caller can refuse a paid
        account rather than enable one implicitly."""
        ...


# --------------------------------------------------------------------------------------------
# Unit of work
# --------------------------------------------------------------------------------------------


class UnitOfWork(Protocol):
    """One database transaction exposing every repository.

    Used as `async with uow_factory() as uow: ...`. Exiting normally commits; an exception
    rolls back. This is what makes "transition + audit row in the same transaction" true
    rather than aspirational, so services must not commit per repository call.
    """

    audit: AuditRepository
    policies: PolicyRepository
    campaigns: CampaignRepository
    domains: DomainRepository
    domain_metrics: DomainMetricRepository
    prospects: ProspectRepository
    prospect_urls: ProspectUrlRepository
    qualifications: QualificationRepository
    rankings: RankingRepository
    evidence: EvidenceRepository
    dossiers: DossierRepository
    contacts: ContactRepository
    suppressions: SuppressionRepository
    idempotency: IdempotencyRepository
    jobs: JobRepository
    approvals: ApprovalRepository
    settings: SettingsRepository
    agent_runs: AgentRunRepository
    sequences: SequenceRepository
    messages: MessageRepository
    threads: ThreadRepository
    replies: ReplyRepository
    opportunities: OpportunityRepository
    backlinks: BacklinkRepository
    provider_accounts: ProviderAccountRepository

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def flush(self) -> None:
        """Make pending writes visible to later reads in this same transaction."""
        ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
