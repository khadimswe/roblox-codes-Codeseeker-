"""
sources/base.py — the boundary between the agent and the world.

Model-based agent role
----------------------
Sources are the agent's **sensors**, and `poll()` is the **actuator** that
operates them.  Everything on the far side of this interface is the environment:
web pages the agent does not control, written by people who do not know or care
what the agent believes.

A `RawListing` is deliberately dumb.  It is what a source *claims*, verbatim and
unjudged — an unnormalised string, whatever reward text sat next to it, and the
time the agent read it.  No confidence, no status, no trust.  Turning claims
into percepts is `agent/percepts.py`'s job; forming beliefs about them is
`agent/model.py`'s.  Keeping those three apart is what stops a page-layout quirk
from leaking into the belief store.

Graceful degradation
--------------------
CLAUDE.md: "Source failure is not app failure."  A 404, a timeout, or a site
that quietly rewrote its HTML must degrade the run, not end it.  `poll()` is
allowed to raise `SourceError`; `poll_safely()` is the wrapper the FSM actually
calls, and it never raises.  This also maps to a real edge in the state machine:
POLL_SOURCES --(all sources failed)--> DECAY, the transition that shows the
agent updating its beliefs with no new input at all.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class SourceError(Exception):
    """A source could not be read this time.  Expected, not exceptional."""


@dataclass(frozen=True)
class RawListing:
    """
    One claim by one source: 'this string is a code for this game'.

    Raw on purpose.  `code` here is whatever text the page had — possibly
    lowercase, possibly wrapped in quotes, possibly not a code at all.
    """

    source_id: str
    code: str
    claimed_reward: str = ""
    kind_hint: str = ""          # free text near the code, e.g. "limited time"
    observed_at: datetime | None = None


@dataclass
class PollOutcome:
    """
    What happened when the agent operated one sensor.

    Carries the failure rather than throwing it, so that the FSM can log a
    warning, mark the source degraded, and carry on with whatever other sources
    did respond.
    """

    source_id: str
    ok: bool
    listings: list[RawListing] = field(default_factory=list)
    error: str = ""
    elapsed_seconds: float = 0.0

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"{self.source_id}: FAILED ({self.error})"
        return f"{self.source_id}: {len(self.listings)} listings"


class Source(ABC):
    """
    The interface every source implements.

    Two exist: `sources/canned.py` replays recorded snapshots against a virtual
    clock (used for development and for the demo video), and `sources/web.py`
    fetches live pages under a rate limit.  The agent cannot tell them apart,
    which is the point — the belief model is identical either way.
    """

    #: Stable identifier, used as the key for trust and for source badges.
    source_id: str = "unnamed"
    #: Human-readable name for the GUI.
    display_name: str = "Unnamed source"

    @abstractmethod
    def poll(self, game: str, now: datetime) -> list[RawListing]:
        """
        Return what this source currently claims about `game`.

        `now` is passed in rather than read, so a canned source can be asked
        "what did you show on virtual day 7?" and the whole agent can be
        fast-forwarded.  Live sources ignore it for fetching but stamp it onto
        the listings they return.

        May raise `SourceError`.  Callers should use `poll_safely()`.
        """

    def describe(self) -> str:
        return f"{self.display_name} ({self.source_id})"


def poll_safely(source: Source, game: str, now: datetime) -> PollOutcome:
    """
    Operate one sensor without letting it take the agent down.

    Catches `SourceError` *and* unexpected exceptions on purpose: the most likely
    real-world failure is a site changing its markup, which surfaces as an
    AttributeError or IndexError deep inside a parser, not as a tidy SourceError.
    A GUI that dies because a fan wiki moved a `<table>` would be a bad agent.
    """
    started = time.monotonic()
    try:
        listings = source.poll(game, now)
    except SourceError as exc:
        return PollOutcome(
            source_id=source.source_id,
            ok=False,
            error=str(exc),
            elapsed_seconds=time.monotonic() - started,
        )
    except Exception as exc:  # noqa: BLE001 - deliberate: see docstring
        return PollOutcome(
            source_id=source.source_id,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_seconds=time.monotonic() - started,
        )
    return PollOutcome(
        source_id=source.source_id,
        ok=True,
        listings=list(listings),
        elapsed_seconds=time.monotonic() - started,
    )
