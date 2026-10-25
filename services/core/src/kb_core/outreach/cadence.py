"""When the next follow-up is due (CCR-004), computed from policy and the previous send.

Pure: no I/O. `sent_at` is the instant the previous message was successfully handed to the
provider (read from the injected `Clock` immediately after `complete_send` commits); it is never
the scheduling time. Step numbering: step 1 is the initial send, step 2 the first follow-up.
`follow_up_delays_days[i]` is the wait after the send of step `i + 1`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from kb_contracts.domain import PolicyBody

from kb_core.errors import DomainConflict
from kb_core.scheduling.window import SendingWindow, add_days


class FollowUpScheduleGap(DomainConflict):
    """A configured follow-up step has no delay. `PolicyBody` forbids this, so reaching it is a
    bug (or a policy that bypassed validation); it is never silently skipped."""

    def __init__(self, step_no: int, delays: int) -> None:
        super().__init__(
            "FOLLOW_UP_SCHEDULE_GAP",
            f"follow-up step {step_no} has no configured delay ({delays} delays)",
        )


@dataclass(frozen=True)
class FollowUpPlan:
    step_no: int  # the step the timer will fire for
    run_at: datetime  # UTC
    completes: bool  # True: the timer ends the sequence (no further follow-up is allowed)


def plan_after_send(
    body: PolicyBody, *, sent_step_no: int, sent_at: datetime
) -> FollowUpPlan | None:
    """The timer to set once step `sent_step_no` was sent, or None when none can be set.

    * Within `max_follow_ups`: due `delays[sent_step_no - 1]` calendar days after `sent_at` in the
      policy timezone, then moved forward to the next in-window moment.
    * Beyond `max_follow_ups`: a *completion* timer after the last configured delay gives the
      recipient that long to reply before COMPLETED_NO_REPLY. With no delays configured at all
      (only possible when `max_follow_ups == 0`) no timer is set and the sequence keeps waiting
      for a reply; nothing is invented.
    """
    delays = body.follow_up_delays_days
    next_step = sent_step_no + 1
    index = sent_step_no - 1
    if next_step <= 1 + body.max_follow_ups:
        if index >= len(delays):
            raise FollowUpScheduleGap(next_step, len(delays))
        due = add_days(sent_at, delays[index], timezone=body.timezone)
        return FollowUpPlan(next_step, SendingWindow.from_policy(body).next_open(due), False)
    if not delays:
        return None
    grace = delays[min(index, len(delays) - 1)]
    return FollowUpPlan(next_step, sent_at + timedelta(days=grace), True)
