"""Sending-window arithmetic: in/out of window, unrestricted, DST-safe in non-UTC zones."""

from datetime import UTC, datetime, timedelta

import pytest
from kb_contracts.domain import PolicyBody
from kb_core.scheduling.window import SendingWindow, add_days

LONDON = "Europe/London"
NY = "America/New_York"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_unrestricted_window_never_moves_anything():
    w = SendingWindow()
    t = utc(2026, 1, 3, 3, 17, 5)
    assert w.unrestricted and w.contains(t) and w.next_open(t) == t


def test_inside_the_window_is_unchanged():
    w = SendingWindow("UTC", 9, 17)
    t = utc(2026, 1, 5, 12, 30)
    assert w.next_open(t) == t


def test_window_end_is_exclusive_and_start_inclusive():
    w = SendingWindow("UTC", 9, 17)
    assert w.contains(utc(2026, 1, 5, 9, 0)) and not w.contains(utc(2026, 1, 5, 17, 0))


def test_before_start_moves_to_the_same_days_start():
    assert SendingWindow("UTC", 9, 17).next_open(utc(2026, 1, 5, 6, 0)) == utc(2026, 1, 5, 9)


def test_after_end_moves_to_the_next_days_start():
    assert SendingWindow("UTC", 9, 17).next_open(utc(2026, 1, 5, 17, 0)) == utc(2026, 1, 6, 9)


def test_non_utc_zone_is_interpreted_locally():
    # 09:00 New York in January is 14:00 UTC
    assert SendingWindow(NY, 9, 17).next_open(utc(2026, 1, 5, 10, 0)) == utc(2026, 1, 5, 14)


def test_london_spring_forward_window_opens_an_hour_earlier_in_utc():
    # Sat 28 Mar 2026 20:00 GMT is after 17:00; the next 09:00 is Sun 29 Mar, already BST (UTC+1)
    got = SendingWindow(LONDON, 9, 17).next_open(utc(2026, 3, 28, 20, 0))
    assert got == utc(2026, 3, 29, 8, 0)  # 09:00 BST, not 09:00 UTC


def test_london_fall_back_window_opens_an_hour_later_in_utc():
    # Sat 24 Oct 2026 18:30 BST; Sun 25 Oct is GMT, so 09:00 local is 09:00 UTC
    got = SendingWindow(LONDON, 9, 17).next_open(utc(2026, 10, 24, 17, 30))
    assert got == utc(2026, 10, 25, 9, 0)


def test_window_start_inside_a_spring_forward_gap_resolves_after_the_gap():
    # New York 8 Mar 2026: 02:00-03:00 does not exist. Window 02-05 opens at 03:00 EDT = 07:00 UTC
    got = SendingWindow(NY, 2, 5).next_open(utc(2026, 3, 8, 5, 0))  # 00:00 EST
    assert got == utc(2026, 3, 8, 7, 0)
    assert SendingWindow(NY, 2, 5).contains(got)


def test_a_window_that_never_exists_that_day_skips_to_the_next():
    # window 02-03 on the gap day: the first real instant (03:00 EDT) is outside it
    got = SendingWindow(NY, 2, 3).next_open(utc(2026, 3, 8, 5, 0))
    assert got == utc(2026, 3, 9, 6, 0)  # 02:00 EDT next day


def test_result_is_never_earlier_than_the_input():
    w = SendingWindow(LONDON, 9, 17)
    t = utc(2026, 3, 29, 0, 0)
    for _ in range(96):
        assert w.next_open(t) >= t
        t += timedelta(minutes=37)


def test_add_days_is_wall_clock_across_dst():
    # Sat 7 Mar 2026 19:30 UTC = 14:30 EST; +1 calendar day = Sun 14:30 EDT = 18:30 UTC
    assert add_days(utc(2026, 3, 7, 19, 30), 1, timezone=NY) == utc(2026, 3, 8, 18, 30)
    assert add_days(utc(2026, 3, 7, 19, 30), 1, timezone="UTC") == utc(2026, 3, 8, 19, 30)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        SendingWindow("UTC", 9, 17).next_open(datetime(2026, 1, 1, 3))
    with pytest.raises(ValueError):
        add_days(datetime(2026, 1, 1), 1, timezone="UTC")


def test_from_policy_reads_the_policy_fields():
    w = SendingWindow.from_policy(
        PolicyBody(timezone=NY, sending_window_start_hour=8, sending_window_end_hour=18)
    )
    assert (w.timezone, w.start_hour, w.end_hour) == (NY, 8, 18)
