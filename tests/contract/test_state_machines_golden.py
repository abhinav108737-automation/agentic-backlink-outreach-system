"""Independent golden tables transcribed from docs/13. Unlike the matrix test (which checks the
code against itself) these fail if any edge is added, removed or changed without a docs change."""

import pytest
from kb_contracts import enums as e
from kb_contracts import state_machines as sm

pytestmark = pytest.mark.req("NFR-08")

GOLDEN = {
    sm.PROSPECT.name: (
        e.ProspectStatus,
        """
        DISCOVERED: ELIGIBLE REJECTED DEFERRED SUPPRESSED
        DEFERRED: ELIGIBLE REJECTED SUPPRESSED
        ELIGIBLE: RANKED SUPPRESSED
        RANKED: RESEARCHED SUPPRESSED
        RESEARCHED: CONTACT_READY REJECTED SUPPRESSED
        CONTACT_READY: OUTREACH_READY SUPPRESSED
        OUTREACH_READY: IN_OUTREACH SUPPRESSED
        IN_OUTREACH: WON LOST SUPPRESSED
        REJECTED: DISCOVERED SUPPRESSED
        LOST: ELIGIBLE SUPPRESSED
        WON:
        SUPPRESSED:""",
    ),
    sm.OUTREACH_SEQUENCE.name: (
        e.SequenceStatus,
        """
        PLANNED: READY POLICY_BLOCKED REPLIED OPTED_OUT BOUNCED CANCELLED
        READY: WAITING_REPLY REPLIED OPTED_OUT BOUNCED CANCELLED
        POLICY_BLOCKED: READY CANCELLED REPLIED OPTED_OUT BOUNCED
        WAITING_REPLY: FOLLOW_UP_DUE REPLIED OPTED_OUT BOUNCED CANCELLED
        FOLLOW_UP_DUE: WAITING_REPLY COMPLETED_NO_REPLY REPLIED OPTED_OUT BOUNCED CANCELLED
        COMPLETED_NO_REPLY:
        REPLIED:
        OPTED_OUT:
        BOUNCED:
        CANCELLED:""",
    ),
    sm.OUTREACH_MESSAGE.name: (
        e.MessageStatus,
        """
        DRAFT: QA_PASSED QA_FAILED CANCELLED
        QA_FAILED: DRAFT CANCELLED
        QA_PASSED: PENDING_APPROVAL APPROVED CANCELLED
        PENDING_APPROVAL: APPROVED REJECTED CANCELLED
        APPROVED: QUEUED POLICY_BLOCKED CANCELLED
        QUEUED: SENDING CANCELLED POLICY_BLOCKED
        SENDING: SENT RETRY_WAIT FAILED
        RETRY_WAIT: QUEUED CANCELLED
        SENT: DELIVERED BOUNCED
        DELIVERED:
        BOUNCED:
        FAILED:
        REJECTED:
        CANCELLED:
        POLICY_BLOCKED:""",
    ),
    sm.OPPORTUNITY.name: (
        e.OpportunityStatus,
        """
        OPEN: NEGOTIATING LOST
        NEGOTIATING: AWAITING_APPROVAL ESCALATED_TO_HUMAN AGREED LOST
        AWAITING_APPROVAL: NEGOTIATING AGREED LOST
        ESCALATED_TO_HUMAN: NEGOTIATING AGREED LOST
        AGREED: CONTENT_IN_PROGRESS LOST
        CONTENT_IN_PROGRESS: CONTENT_SUBMITTED LOST
        CONTENT_SUBMITTED: REVISION_REQUESTED PUBLISHED LOST
        REVISION_REQUESTED: CONTENT_SUBMITTED LOST
        PUBLISHED:
        LOST:""",
    ),
    sm.BACKLINK.name: (
        e.BacklinkStatus,
        """
        EXPECTED: FOUND MISSING
        FOUND: VERIFIED CHANGED MISSING
        VERIFIED: CHANGED MISSING
        CHANGED: VERIFIED MISSING
        MISSING: VERIFIED FOUND LOST
        LOST: FOUND""",
    ),
    sm.OPTIMIZATION.name: (
        e.OptimizationStatus,
        """
        PROPOSED: OFFLINE_TEST REJECTED
        OFFLINE_TEST: SHADOW REJECTED PROMOTED
        SHADOW: CANARY EXPERIMENT REJECTED
        CANARY: EXPERIMENT PROMOTED REJECTED
        EXPERIMENT: PROMOTED REJECTED
        PROMOTED: MONITORED
        MONITORED: ROLLED_BACK
        REJECTED:
        ROLLED_BACK:""",
    ),
    sm.WORKFLOW.name: (
        e.WorkflowStatus,
        """
        PENDING: RUNNING CANCELLED
        RUNNING: WAITING SUCCEEDED FAILED CANCELLED
        WAITING: RUNNING FAILED CANCELLED
        SUCCEEDED:
        FAILED:
        CANCELLED:""",
    ),
    sm.JOB.name: (
        e.JobStatus,
        """
        SCHEDULED: RUNNING CANCELLED
        RUNNING: SUCCEEDED RETRY_WAIT DEAD SCHEDULED
        RETRY_WAIT: SCHEDULED CANCELLED
        SUCCEEDED:
        DEAD:
        CANCELLED:""",
    ),
    sm.CAMPAIGN.name: (
        e.CampaignStatus,
        """
        DRAFT: ACTIVE
        ACTIVE: PAUSED ARCHIVED
        PAUSED: ACTIVE ARCHIVED
        ARCHIVED:""",
    ),
}


def parse(enum_cls, text):
    out = {}
    for line in text.strip().splitlines():
        src, _, rhs = line.strip().partition(":")
        out[enum_cls[src.strip()]] = frozenset(enum_cls[d] for d in rhs.split())
    return out


@pytest.mark.parametrize("name", list(GOLDEN))
def test_machine_equals_golden_table_exactly(name):
    machine = {m.name: m for m in sm.ALL_MACHINES}[name]
    enum_cls, text = GOLDEN[name]
    expected = parse(enum_cls, text)
    assert set(expected) == set(enum_cls), "golden table must list every state"
    assert dict(machine.transitions) == expected


def test_every_machine_has_a_golden_table():
    assert {m.name for m in sm.ALL_MACHINES} == set(GOLDEN)


def test_no_edge_leaves_a_terminal_sequence_or_message():
    """Regression guard for the property that makes opt-out safe: nothing revives a closed
    sequence, and a sent message can never be re-queued."""
    for s in (e.SequenceStatus.OPTED_OUT, e.SequenceStatus.BOUNCED, e.SequenceStatus.REPLIED):
        assert sm.OUTREACH_SEQUENCE.allowed(s) == frozenset()
    assert not sm.OUTREACH_MESSAGE.is_allowed(e.MessageStatus.SENT, e.MessageStatus.QUEUED)
