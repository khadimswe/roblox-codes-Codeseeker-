"""
Shared test fixtures.

The important one is `FakeClock`.  `agent/model.py` is required to be pure and
to take `now` as a parameter, which means the entire belief model can be tested
by handing it whatever time we like — no sleeping, no patching, no real clock.
If a test in here ever needs `datetime.now()`, something has leaked a clock read
into the agent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.state import CodeKind, CodeRecord, SourceSighting


class FakeClock:
    """A clock the test drives by hand."""

    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def advance(self, days: float = 0, hours: float = 0) -> datetime:
        self.now += timedelta(days=days, hours=hours)
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_record(
    code: str = "DEMO_TEST",
    *,
    game: str = "slayers2",
    sources: list[str] | None = None,
    seen_at: datetime | None = None,
    kind: CodeKind = CodeKind.UNKNOWN,
    reward: str = "test reward",
) -> CodeRecord:
    """
    Build a CodeRecord directly, bypassing percepts and the belief store.

    Tests of the model should not have to stand up a source or a store to check
    an equation — that coupling is exactly what the purity rule prevents.
    """
    seen_at = seen_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    sources = sources or ["example_wiki"]
    return CodeRecord(
        code=code,
        game=game,
        sources={
            sid: SourceSighting(source_id=sid, first_seen=seen_at, last_seen=seen_at)
            for sid in sources
        },
        first_seen=seen_at,
        last_corroborated=seen_at,
        claimed_reward=reward,
        kind=kind,
    )


@pytest.fixture
def record_factory():
    return make_record
