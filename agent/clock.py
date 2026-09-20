"""
agent/clock.py — the one place in the agent allowed to know what time it is.

Why this exists
---------------
`agent/model.py` is required to be pure and to take `now` as a parameter, which
is what makes `--demo` able to fast-forward a week in a couple of seconds.  That
rule only works if there is exactly one thing that produces `now`, and it can be
swapped.  This module is that thing.

`RealClock` is wall time.  `VirtualClock` is a clock the user drives with a
button, and it is the machinery behind the central beat of the demo video:
advance the clock, arrest nothing else, and watch confidence bars fall on their
own because the agent is modelling a world that keeps moving while nobody looks
at it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class Clock:
    """Something that can be asked for the current time."""

    def now(self) -> datetime:
        raise NotImplementedError

    @property
    def is_virtual(self) -> bool:
        return False


class RealClock(Clock):
    """Wall-clock time, always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class VirtualClock(Clock):
    """
    A clock that only moves when told to.

    `elapsed_days` is kept separately from the timestamp because the GUI shows
    "Day 3" rather than a date — the demo is about intervals, not calendars, and
    "Day 3" is instantly readable on a five-minute video where a date is not.
    """

    def __init__(self, start: datetime | None = None):
        self.start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._now = self.start

    def now(self) -> datetime:
        return self._now

    @property
    def is_virtual(self) -> bool:
        return True

    @property
    def elapsed_days(self) -> float:
        return (self._now - self.start).total_seconds() / 86400.0

    def advance(self, days: float = 0.0, hours: float = 0.0) -> datetime:
        """Move time forward.  Never backward: beliefs are built on ordered evidence."""
        delta = timedelta(days=max(0.0, days), hours=max(0.0, hours))
        self._now += delta
        return self._now

    def reset(self) -> datetime:
        self._now = self.start
        return self._now
