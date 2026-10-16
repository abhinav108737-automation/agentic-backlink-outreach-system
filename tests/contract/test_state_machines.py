"""Exhaustive invalid-transition matrix + structural/property tests (docs/13, ADR-0012)."""

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st
from kb_contracts import enums
from kb_contracts.state_machines import (
    ALL_MACHINES,
    BACKLINK,
    CAMPAIGN,
    JOB,
    OPPORTUNITY,
    OPTIMIZATION,
    OUTREACH_MESSAGE,
    OUTREACH_SEQUENCE,
    PROSPECT,
    WORKFLOW,
    InvalidTransition,
)

pytestmark = pytest.mark.req("NFR-08")


@pytest.mark.parametrize("m", ALL_MACHINES, ids=lambda m: m.name)
def test_every_unlisted_pair_is_rejected(m):
    for src, dst in itertools.product(m.states, m.states):
        if dst in m.transitions.get(src, frozenset()):
            m.check(src, dst)
        else:
            with pytest.raises(InvalidTransition):
                m.check(src, dst)


@pytest.mark.parametrize("m", ALL_MACHINES, ids=lambda m: m.name)
def test_table_only_references_member_states(m):
    for src, dsts in m.transitions.items():
        assert src in m.states
        assert dsts <= set(m.states)


@pytest.mark.parametrize("m", ALL_MACHINES, ids=lambda m: m.name)
def test_every_state_is_reachable_from_some_start(m):
    # Every non-initial state must have at least one inbound edge.
    inbound = {d for ds in m.transitions.values() for d in ds}
    initial = {
        "prospect": {enums.ProspectStatus.DISCOVERED},
        "outreach_sequence": {enums.SequenceStatus.PLANNED},
        "outreach_message": {enums.MessageStatus.DRAFT},
        "opportunity": {enums.OpportunityStatus.OPEN},
        "backlink": {enums.BacklinkStatus.EXPECTED},
        "optimization": {enums.OptimizationStatus.PROPOSED},
        "workflow": {enums.WorkflowStatus.PENDING},
        "job": {enums.JobStatus.SCHEDULED},
        "campaign": {enums.CampaignStatus.DRAFT},
    }[m.name]
    # DISCOVERED has an inbound edge too (re-qualification); initial states are exempt.
    assert set(m.states) - inbound <= initial


def test_terminal_states_match_docs():
    assert PROSPECT.terminal_states() == {enums.ProspectStatus.WON, enums.ProspectStatus.SUPPRESSED}
    assert OUTREACH_SEQUENCE.terminal_states() == {
        enums.SequenceStatus.REPLIED,
        enums.SequenceStatus.OPTED_OUT,
        enums.SequenceStatus.BOUNCED,
        enums.SequenceStatus.CANCELLED,
        enums.SequenceStatus.COMPLETED_NO_REPLY,
    }
    assert OPPORTUNITY.terminal_states() == {
        enums.OpportunityStatus.PUBLISHED,
        enums.OpportunityStatus.LOST,
    }
    assert OPTIMIZATION.terminal_states() == {
        enums.OptimizationStatus.REJECTED,
        enums.OptimizationStatus.ROLLED_BACK,
    }
    assert WORKFLOW.terminal_states() == {
        enums.WorkflowStatus.SUCCEEDED,
        enums.WorkflowStatus.FAILED,
        enums.WorkflowStatus.CANCELLED,
    }
    assert JOB.terminal_states() == {
        enums.JobStatus.SUCCEEDED,
        enums.JobStatus.DEAD,
        enums.JobStatus.CANCELLED,
    }
    assert CAMPAIGN.terminal_states() == {enums.CampaignStatus.ARCHIVED}


def test_any_non_terminal_prospect_can_be_suppressed():
    for s in enums.ProspectStatus:
        if s not in PROSPECT.terminal_states():
            assert PROSPECT.is_allowed(s, enums.ProspectStatus.SUPPRESSED)


def test_active_sequence_states_can_all_exit_on_reply_optout_bounce_cancel():
    for s in (
        enums.SequenceStatus.PLANNED,
        enums.SequenceStatus.READY,
        enums.SequenceStatus.POLICY_BLOCKED,
        enums.SequenceStatus.WAITING_REPLY,
        enums.SequenceStatus.FOLLOW_UP_DUE,
    ):
        for exit_ in (
            enums.SequenceStatus.REPLIED,
            enums.SequenceStatus.OPTED_OUT,
            enums.SequenceStatus.BOUNCED,
            enums.SequenceStatus.CANCELLED,
        ):
            assert OUTREACH_SEQUENCE.is_allowed(s, exit_)


def test_message_cannot_skip_qa_or_policy_recheck():
    M = enums.MessageStatus
    assert not OUTREACH_MESSAGE.is_allowed(M.DRAFT, M.QUEUED)
    assert not OUTREACH_MESSAGE.is_allowed(M.QA_PASSED, M.SENDING)
    assert not OUTREACH_MESSAGE.is_allowed(M.APPROVED, M.SENDING)  # must pass through QUEUED
    assert not OUTREACH_MESSAGE.is_allowed(M.SENT, M.QUEUED)  # never resend


def test_backlink_lost_only_via_missing_and_only_verified_is_active():
    B = enums.BacklinkStatus
    assert not BACKLINK.is_allowed(B.VERIFIED, B.LOST)
    assert not BACKLINK.is_allowed(B.EXPECTED, B.VERIFIED)
    assert BACKLINK.is_allowed(B.MISSING, B.LOST)


def test_optimization_cannot_promote_without_offline_test_and_no_state_skips():
    Z = enums.OptimizationStatus
    assert not OPTIMIZATION.is_allowed(Z.PROPOSED, Z.PROMOTED)
    assert not OPTIMIZATION.is_allowed(Z.SHADOW, Z.PROMOTED)  # L2 needs canary/experiment
    assert not OPTIMIZATION.is_allowed(Z.PROMOTED, Z.ROLLED_BACK)  # must pass MONITORED
    assert OPTIMIZATION.is_allowed(Z.MONITORED, Z.ROLLED_BACK)
    assert OPTIMIZATION.is_allowed(Z.OFFLINE_TEST, Z.PROMOTED)  # L1 only, guard enforces class


@pytest.mark.parametrize("m", ALL_MACHINES, ids=lambda m: m.name)
@given(data=st.data())
def test_random_walks_never_leave_the_table(m, data):
    state = data.draw(st.sampled_from(sorted(m.states, key=lambda s: s.value)))
    for _ in range(25):
        options = sorted(m.allowed(state), key=lambda s: s.value)
        if not options:
            assert state in m.terminal_states()
            break
        nxt = data.draw(st.sampled_from(options))
        m.check(state, nxt)
        state = nxt
