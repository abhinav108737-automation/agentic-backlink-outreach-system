"""Sending-window arithmetic (CCR-004): pure, deterministic, DST-safe, no I/O.

All instants in and out are timezone-aware and returned in UTC. The window is wall-clock hours in
the policy's IANA timezone, `[start_hour, end_hour)`; `0..24` means no restriction.

Rules (each chosen so the result is never earlier than the input and never ambiguous):

* `add_days` adds *calendar* days in the policy timezone (wall-clock arithmetic), so a 09:00 send
  followed by a 4-day delay is due at 09:00 local four days later even across a DST change.
* `next_open` returns the input unchanged when it is inside the window. Otherwise it walks forward
  day by day and returns the first window-start instant that is >= the input and itself inside
  the window.
* A window start that does not exist (spring-forward gap) resolves to the first real instant
  after the gap (zoneinfo `fold=0` semantics); if that instant is outside the window the day is
  skipped. A start that occurs twice (fall-back) resolves to its first occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from kb_contracts.domain import PolicyBody

_MAX_LOOKAHEAD_DAYS = 8


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def add_days(base: datetime, days: int, *, timezone: str) -> datetime:
    """`base` + `days` calendar days in `timezone`, returned in UTC."""
    local = _aware_utc(base, field="base").astimezone(ZoneInfo(timezone))
    return (local + timedelta(days=days)).astimezone(UTC)


@dataclass(frozen=True)
class SendingWindow:
    timezone: str = "UTC"
    start_hour: int = 0
    end_hour: int = 24

    @classmethod
    def from_policy(cls, body: PolicyBody) -> SendingWindow:
        return cls(body.timezone, body.sending_window_start_hour, body.sending_window_end_hour)

    @property
    def unrestricted(self) -> bool:
        return self.start_hour == 0 and self.end_hour == 24

    def contains(self, instant: datetime) -> bool:
        if self.unrestricted:
            return True
        local = _aware_utc(instant, field="instant").astimezone(ZoneInfo(self.timezone))
        return self.start_hour <= local.hour < self.end_hour

    def next_open(self, instant: datetime) -> datetime:
        """The earliest instant >= `instant` that is inside the window (UTC)."""
        at = _aware_utc(instant, field="instant")
        if self.contains(at):
            return at
        zone = ZoneInfo(self.timezone)
        first: date = at.astimezone(zone).date()
        for offset in range(_MAX_LOOKAHEAD_DAYS):
            day = first + timedelta(days=offset)
            candidate = datetime(
                day.year, day.month, day.day, self.start_hour, tzinfo=zone
            ).astimezone(UTC)
            if candidate >= at and self.contains(candidate):
                return candidate
        raise ValueError(  # pragma: no cover - unreachable for any real zone and a valid window
            f"no sending window opens within {_MAX_LOOKAHEAD_DAYS} days of {at.isoformat()}"
        )
