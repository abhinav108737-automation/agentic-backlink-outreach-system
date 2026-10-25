"""Follow-up timer: WAITING_REPLY -> FOLLOW_UP_DUE -> (next draft | COMPLETED_NO_REPLY).

Safe to redeliver and safe against races with replies: a sequence that is no longer waiting
(replied, opted out, bounced, cancelled, completed) is a no-op, so a follow-up can never be
drafted after a reply or opt-out even if its job survived cancellation.

Stop conditions (CCR-004). A follow-up never proceeds after a reply, an opt-out or a bounce:
the sequence is no longer waiting, so the run is a no-op and the terminal transition already
cancelled the job.

The remaining stop conditions are deliberately NOT all the same kind of stop:

* **Suppression cancels the sequence.** The recipient asked not to be contacted, so there is
  nothing to resume and CANCELLED (terminal) is correct. It also cancels the sequence's other
  pending jobs in the same transaction.
* **A paused campaign or an active kill switch HOLDS the sequence.** Both are reversible
  operator controls — `docs/13` defines campaign status as `ACTIVE ⇄ PAUSED`, and a kill switch
  exists to be released. Cancelling here would terminally destroy every in-flight sequence the
  moment an operator paused a campaign, and resuming would bring none of them back. So the
  sequence keeps its waiting state and the timer is re-armed for a later check.

Any other block (a sender or limit problem, say) is transient and the job retries with bounded
backoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from kb_contracts.enums import SequenceStatus
from kb_contracts.events import Actor
from kb_contracts.policy import BlockCode, PolicyCheckRequest, PolicyEngine

from kb_core.db.protocols import UnitOfWorkFactory
from kb_core.errors import DomainConflict, EntityNotFound, PolicyUnavailable
from kb_core.outreach.payloads import (
    OUTREACH_DRAFT,
    OUTREACH_FOLLOW_UP,
    sequence_concurrency_key,
)
from kb_core.outreach.ports import ACTOR_OUTREACH, TransitionPort
from kb_core.outreach.send import load_channel

_WAITING = (SequenceStatus.WAITING_REPLY, SequenceStatus.FOLLOW_UP_DUE)
# Permanent: the recipient is off limits, so the sequence is over.
CANCELLING_CODES = frozenset({BlockCode.SUPPRESSED})
# Reversible operator controls: hold the sequence, never destroy it.
HOLDING_CODES = frozenset({BlockCode.CAMPAIGN_PAUSED, BlockCode.KILL_SWITCH_ACTIVE})
# How far out to re-arm the timer while a hold is in force. Re-arming rather than raising keeps
# a long pause from burning the job's bounded retries and dead-lettering the sequence.
HOLD_RECHECK = timedelta(hours=6)
STOP_CODES = CANCELLING_CODES | HOLDING_CODES


@dataclass(frozen=True)
class FollowUpResult:
    outcome: str  # drafted | completed | skipped | cancelled | held


class FollowUpService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, transitions: TransitionPort, policy: PolicyEngine
    ) -> None:
        self._uow_factory = uow_factory
        self._tr = transitions
        self._policy = policy

    async def run(
        self,
        sequence_id: UUID,
        step_no: int,
        *,
        correlation_id: str,
        run_at_now: datetime,
        actor: Actor = ACTOR_OUTREACH,
    ) -> FollowUpResult:
        async with self._uow_factory() as uow:
            seq = await uow.sequences.get(sequence_id)
            if seq is None:
                raise EntityNotFound("outreach_sequence", sequence_id)
            if seq.status not in _WAITING:
                return FollowUpResult("skipped")
            version = await uow.policies.active_version_for_campaign(seq.campaign_id)
            if version is None:
                raise PolicyUnavailable(f"no effective policy for campaign {seq.campaign_id}")

            if seq.status is SequenceStatus.WAITING_REPLY:
                seq = (
                    await self._tr.transition_in(
                        uow,
                        seq,
                        SequenceStatus.FOLLOW_UP_DUE,
                        actor=actor,
                        reason=f"follow-up timer fired for step {step_no}",
                        correlation_id=correlation_id,
                    )
                ).entity
            if step_no > 1 + version.body.max_follow_ups:
                await self._tr.transition_in(
                    uow,
                    seq,
                    SequenceStatus.COMPLETED_NO_REPLY,
                    actor=actor,
                    reason="follow-up steps exhausted",
                    correlation_id=correlation_id,
                )
                return FollowUpResult("completed")

            channel = await load_channel(uow, seq)
            prospect = await uow.prospects.get(seq.prospect_id)
            assert prospect is not None
            domain = await uow.domains.get(prospect.domain_id)
            assert domain is not None
            decision = await self._policy.check(
                PolicyCheckRequest(
                    action="follow_up",
                    campaign_id=seq.campaign_id,
                    subject_type="sequence",
                    subject_id=seq.id,
                    context={
                        "email": channel.value_normalized,
                        "domain": domain.registered_domain,
                        "contact_id": str(channel.contact_id),
                    },
                    correlation_id=correlation_id,
                )
            )
            if not decision.allowed:
                codes = ",".join(c.value for c in decision.block_codes)
                if CANCELLING_CODES.intersection(decision.block_codes):
                    await self._tr.transition_in(
                        uow,
                        seq,
                        SequenceStatus.CANCELLED,
                        actor=actor,
                        reason=f"follow-up stopped: {codes}",
                        correlation_id=correlation_id,
                    )
                    return FollowUpResult("cancelled")
                if HOLDING_CODES.intersection(decision.block_codes):
                    # Hold, do not cancel: both conditions are reversible. Re-arm the timer and
                    # audit the hold so the pause is visible without losing the sequence.
                    recheck = run_at_now + HOLD_RECHECK
                    await uow.sequences.set_next_due(seq.id, run_at=recheck)
                    await uow.jobs.enqueue(
                        job_type=OUTREACH_FOLLOW_UP,
                        payload={
                            "sequence_id": str(seq.id),
                            "step_no": step_no,
                            "correlation_id": correlation_id,
                        },
                        run_at=recheck,
                        idempotency_key=(
                            f"{OUTREACH_FOLLOW_UP}:{seq.id}:{step_no}:hold:{recheck.isoformat()}"
                        ),
                        concurrency_key=sequence_concurrency_key(seq.id),
                        subject_type="sequence",
                        subject_id=seq.id,
                    )
                    await uow.audit.append(
                        actor_type=actor.actor_type.value,
                        actor_id=actor.actor_id,
                        action="follow_up.held",
                        subject_type="outreach_sequence",
                        subject_id=seq.id,
                        correlation_id=correlation_id,
                        new_state={"codes": codes, "recheck_at": recheck.isoformat()},
                        reason=f"follow-up held (reversible): {codes}",
                    )
                    return FollowUpResult("held")
                raise DomainConflict("FOLLOW_UP_BLOCKED", f"follow-up deferred: {codes}")

            await uow.jobs.enqueue(
                job_type=OUTREACH_DRAFT,
                payload={
                    "sequence_id": str(seq.id),
                    "step_no": step_no,
                    "correlation_id": correlation_id,
                },
                run_at=run_at_now,
                idempotency_key=f"{OUTREACH_DRAFT}:{seq.id}:{step_no}",
                concurrency_key=sequence_concurrency_key(seq.id),
                subject_type="sequence",
                subject_id=seq.id,
            )
            return FollowUpResult("drafted")
