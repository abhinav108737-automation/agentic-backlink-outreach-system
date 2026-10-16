"""C1: bounded state-machine transition tables (docs/13, ADR-0012). Data, not logic.

Guards (policy, approvals, risk class) live in the services' transition(); this module
only answers "is the (from, to) pair structurally legal?".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from kb_contracts.enums import (
    BacklinkStatus,
    CampaignStatus,
    JobStatus,
    MessageStatus,
    OpportunityStatus,
    OptimizationStatus,
    ProspectStatus,
    SequenceStatus,
    WorkflowStatus,
)


class InvalidTransition(Exception):  # noqa: N818 - domain term
    def __init__(self, machine: str, src: StrEnum, dst: StrEnum) -> None:
        super().__init__(f"{machine}: illegal transition {src.value} -> {dst.value}")
        self.machine, self.src, self.dst = machine, src, dst


@dataclass(frozen=True)
class StateMachine[S: StrEnum]:
    name: str
    states: type[S]
    transitions: Mapping[S, frozenset[S]]

    def allowed(self, src: S) -> frozenset[S]:
        return self.transitions.get(src, frozenset())

    def is_allowed(self, src: S, dst: S) -> bool:
        return dst in self.allowed(src)

    def check(self, src: S, dst: S) -> None:
        if not self.is_allowed(src, dst):
            raise InvalidTransition(self.name, src, dst)

    def terminal_states(self) -> frozenset[S]:
        return frozenset(s for s in self.states if not self.allowed(s))


def _table[S: StrEnum](states: type[S], spec: str) -> dict[S, frozenset[S]]:
    """Parse 'SRC -> DST DST' lines. States with no line (or an empty RHS) are terminal."""
    table: dict[S, set[S]] = {s: set() for s in states}
    for raw in spec.strip().splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        src, _, rhs = line.partition("->")
        table[states[src.strip()]].update(states[d] for d in rhs.split())
    return {k: frozenset(v) for k, v in table.items()}


def _add_to_all[S: StrEnum](
    table: dict[S, frozenset[S]], sources: Sequence[S], dst: Sequence[S]
) -> dict[S, frozenset[S]]:
    for src in sources:
        table[src] = table[src] | frozenset(dst)
    return table


_prospect = _table(
    ProspectStatus,
    """
    DISCOVERED -> ELIGIBLE REJECTED DEFERRED
    DEFERRED -> ELIGIBLE REJECTED
    ELIGIBLE -> RANKED
    RANKED -> RESEARCHED
    RESEARCHED -> CONTACT_READY REJECTED
    CONTACT_READY -> OUTREACH_READY
    OUTREACH_READY -> IN_OUTREACH
    IN_OUTREACH -> WON LOST
    REJECTED -> DISCOVERED   # only via re-qualification under a new policy version
    LOST -> ELIGIBLE         # only if policy allows re-contact after cooldown
    """,
)
_non_terminal = [
    s for s in ProspectStatus if s not in (ProspectStatus.WON, ProspectStatus.SUPPRESSED)
]
_prospect = _add_to_all(_prospect, _non_terminal, [ProspectStatus.SUPPRESSED])

_sequence = _table(
    SequenceStatus,
    """
    PLANNED -> READY POLICY_BLOCKED
    READY -> WAITING_REPLY
    POLICY_BLOCKED -> READY CANCELLED
    WAITING_REPLY -> FOLLOW_UP_DUE
    FOLLOW_UP_DUE -> WAITING_REPLY COMPLETED_NO_REPLY
    """,
)
_sequence = _add_to_all(
    _sequence,
    [
        SequenceStatus[n]
        for n in ("PLANNED", "READY", "POLICY_BLOCKED", "WAITING_REPLY", "FOLLOW_UP_DUE")
    ],
    [SequenceStatus[n] for n in ("REPLIED", "OPTED_OUT", "BOUNCED", "CANCELLED")],
)

_message = _table(
    MessageStatus,
    """
    DRAFT -> QA_PASSED QA_FAILED
    QA_FAILED -> DRAFT
    QA_PASSED -> PENDING_APPROVAL APPROVED
    PENDING_APPROVAL -> APPROVED REJECTED
    APPROVED -> QUEUED POLICY_BLOCKED CANCELLED
    QUEUED -> SENDING CANCELLED POLICY_BLOCKED
    SENDING -> SENT RETRY_WAIT FAILED
    RETRY_WAIT -> QUEUED
    SENT -> DELIVERED BOUNCED
    # a terminal sequence cancels all not-yet-sent work (docs/13 2a)
    DRAFT -> CANCELLED
    QA_FAILED -> CANCELLED
    QA_PASSED -> CANCELLED
    PENDING_APPROVAL -> CANCELLED
    RETRY_WAIT -> CANCELLED
    """,
)

_opportunity = _table(
    OpportunityStatus,
    """
    OPEN -> NEGOTIATING LOST
    NEGOTIATING -> AWAITING_APPROVAL ESCALATED_TO_HUMAN AGREED LOST
    AWAITING_APPROVAL -> NEGOTIATING AGREED LOST
    ESCALATED_TO_HUMAN -> NEGOTIATING AGREED LOST
    AGREED -> CONTENT_IN_PROGRESS LOST
    CONTENT_IN_PROGRESS -> CONTENT_SUBMITTED LOST
    CONTENT_SUBMITTED -> REVISION_REQUESTED PUBLISHED LOST
    REVISION_REQUESTED -> CONTENT_SUBMITTED LOST
    """,
)

_backlink = _table(
    BacklinkStatus,
    """
    EXPECTED -> FOUND MISSING
    FOUND -> VERIFIED CHANGED MISSING
    VERIFIED -> CHANGED MISSING
    CHANGED -> VERIFIED MISSING
    MISSING -> VERIFIED FOUND LOST
    LOST -> FOUND
    """,
)

# OFFLINE_TEST -> PROMOTED is structurally legal for L1 only; the service guard enforces class.
_optimization = _table(
    OptimizationStatus,
    """
    PROPOSED -> OFFLINE_TEST REJECTED
    OFFLINE_TEST -> SHADOW REJECTED PROMOTED
    SHADOW -> CANARY EXPERIMENT REJECTED
    CANARY -> EXPERIMENT PROMOTED REJECTED
    EXPERIMENT -> PROMOTED REJECTED
    PROMOTED -> MONITORED
    MONITORED -> ROLLED_BACK
    """,
)

_workflow = _table(
    WorkflowStatus,
    """
    PENDING -> RUNNING CANCELLED
    RUNNING -> WAITING SUCCEEDED FAILED CANCELLED
    WAITING -> RUNNING FAILED CANCELLED  # FAILED: approval/timer expiry
    """,
)

# RUNNING -> SCHEDULED is lease expiry (attempts += 1).
_job = _table(
    JobStatus,
    """
    SCHEDULED -> RUNNING CANCELLED
    RUNNING -> SUCCEEDED RETRY_WAIT DEAD SCHEDULED
    RETRY_WAIT -> SCHEDULED CANCELLED
    """,
)

_campaign = _table(
    CampaignStatus,
    """
    DRAFT -> ACTIVE
    ACTIVE -> PAUSED ARCHIVED
    PAUSED -> ACTIVE ARCHIVED
    """,
)

PROSPECT = StateMachine("prospect", ProspectStatus, _prospect)
OUTREACH_SEQUENCE = StateMachine("outreach_sequence", SequenceStatus, _sequence)
OUTREACH_MESSAGE = StateMachine("outreach_message", MessageStatus, _message)
OPPORTUNITY = StateMachine("opportunity", OpportunityStatus, _opportunity)
BACKLINK = StateMachine("backlink", BacklinkStatus, _backlink)
OPTIMIZATION = StateMachine("optimization", OptimizationStatus, _optimization)
WORKFLOW = StateMachine("workflow", WorkflowStatus, _workflow)
JOB = StateMachine("job", JobStatus, _job)
CAMPAIGN = StateMachine("campaign", CampaignStatus, _campaign)

ALL_MACHINES: tuple[StateMachine, ...] = (  # type: ignore[type-arg]
    PROSPECT,
    OUTREACH_SEQUENCE,
    OUTREACH_MESSAGE,
    OPPORTUNITY,
    BACKLINK,
    OPTIMIZATION,
    WORKFLOW,
    JOB,
    CAMPAIGN,
)
