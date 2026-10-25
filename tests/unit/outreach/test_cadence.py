"""Follow-up cadence: delays come from policy, measured from the previous send."""

from datetime import UTC, datetime, timedelta

import pytest
from kb_contracts.domain import PolicyBody
from kb_core.outreach.cadence import FollowUpScheduleGap, plan_after_send

SENT = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


def test_delay_zero_is_measured_from_the_initial_send():
    plan = plan_after_send(PolicyBody(), sent_step_no=1, sent_at=SENT)
    assert plan is not None and plan.step_no == 2 and not plan.completes
    assert plan.run_at == SENT + timedelta(days=4)


def test_delay_one_is_measured_from_the_first_follow_up_send():
    later = SENT + timedelta(days=5, hours=3)
    plan = plan_after_send(PolicyBody(), sent_step_no=2, sent_at=later)
    assert plan is not None and plan.step_no == 3 and not plan.completes
    assert plan.run_at == later + timedelta(days=7)


def test_custom_delays_are_honoured_per_step():
    body = PolicyBody(follow_up_delays_days=(2, 3, 9), max_follow_ups=3)
    assert plan_after_send(body, sent_step_no=3, sent_at=SENT).run_at == SENT + timedelta(days=9)  # type: ignore[union-attr]


def test_after_the_last_allowed_step_a_completion_timer_is_set_not_a_follow_up():
    plan = plan_after_send(PolicyBody(), sent_step_no=3, sent_at=SENT)
    assert plan is not None and plan.completes and plan.step_no == 4
    assert plan.run_at == SENT + timedelta(days=7)


def test_a_stricter_max_follow_ups_wins_over_longer_delays():
    body = PolicyBody(max_follow_ups=1)  # delays (4, 7) stay, only one follow-up allowed
    first = plan_after_send(body, sent_step_no=2, sent_at=SENT)
    assert first is not None and first.completes and first.step_no == 3


def test_no_follow_ups_allowed_completes_after_the_first_delay():
    body = PolicyBody(max_follow_ups=0)
    plan = plan_after_send(body, sent_step_no=1, sent_at=SENT)
    assert plan is not None and plan.completes and plan.run_at == SENT + timedelta(days=4)


def test_no_delays_and_no_follow_ups_sets_no_timer():
    body = PolicyBody(max_follow_ups=0, follow_up_delays_days=())
    assert plan_after_send(body, sent_step_no=1, sent_at=SENT) is None


def test_a_missing_delay_is_a_loud_gap_never_a_silent_skip():
    body = PolicyBody.model_construct(  # bypasses the validator to simulate a corrupt policy
        **{**PolicyBody().model_dump(), "follow_up_delays_days": (4,), "max_follow_ups": 2}
    )
    with pytest.raises(FollowUpScheduleGap):
        plan_after_send(body, sent_step_no=2, sent_at=SENT)


def test_the_due_time_is_moved_into_the_sending_window_in_the_policy_timezone():
    body = PolicyBody(
        timezone="Europe/London",
        sending_window_start_hour=9,
        sending_window_end_hour=17,
        follow_up_delays_days=(1, 7),
    )
    # Fri 27 Mar 16:30 GMT -> +1 day = Sat 28 Mar 16:30 GMT, in window
    in_window = plan_after_send(
        body, sent_step_no=1, sent_at=datetime(2026, 3, 27, 16, 30, tzinfo=UTC)
    )
    assert in_window is not None and in_window.run_at == datetime(2026, 3, 28, 16, 30, tzinfo=UTC)
    # Sat 28 Mar 16:30 GMT -> +1 day = Sun 29 Mar 16:30 BST (15:30 UTC), still in window
    dst = plan_after_send(body, sent_step_no=1, sent_at=datetime(2026, 3, 28, 16, 30, tzinfo=UTC))
    assert dst is not None and dst.run_at == datetime(2026, 3, 29, 15, 30, tzinfo=UTC)
    # a send at 08:00 UTC Sun 29 Mar = 09:00 BST; 4-day delay lands on Thu 09:00 BST in window
    early = plan_after_send(
        PolicyBody(
            timezone="Europe/London", sending_window_start_hour=10, sending_window_end_hour=17
        ),
        sent_step_no=1,
        sent_at=datetime(2026, 3, 28, 9, 0, tzinfo=UTC),  # 09:00 GMT -> Wed 1 Apr 09:00 BST
    )
    assert early is not None
    assert early.run_at == datetime(2026, 4, 1, 9, 0, tzinfo=UTC)  # 10:00 BST, window opening
