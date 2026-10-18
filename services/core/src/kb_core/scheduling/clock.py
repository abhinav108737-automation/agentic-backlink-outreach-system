"""`Clock` implementations (C5). The system clock is the only place wall time is read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Deterministic, manually advanced clock for tests. Always timezone-aware UTC."""

    def __init__(self, start: datetime | None = None) -> None:
        start = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        if start.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware start")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta | float) -> datetime:
        step = delta if isinstance(delta, timedelta) else timedelta(seconds=delta)
        if step < timedelta(0):
            raise ValueError("a clock never goes backwards")
        self._now += step
        return self._now
