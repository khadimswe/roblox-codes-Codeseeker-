"""
agent/state.py — the agent's internal state: what it *believes* about the world.

Model-based agent role
----------------------
Russell & Norvig's model-based reflex agent keeps "some sort of internal state
that depends on the percept history and thereby reflects at least some of the
unobserved aspects of the current state".  This module *is* that internal state.

`BeliefStore` is the agent's belief.  It is deliberately NOT the world.  The
true set of currently-valid Roblox codes lives out in the environment where the
agent cannot see it; in canned mode the stand-in for that truth is
`data/snapshots/`, which this module never reads and never imports.  The only
channel between the two is a percept (see `agent/percepts.py`).  Keeping those
two structures separate is the single most important architectural property of
this project — it is what makes the claim "this agent maintains a model"
demonstrable rather than merely asserted.

Representation
--------------
`CodeRecord` is a **factored representation** in the textbook's sense: a state
is not an opaque atom but a vector of attributes (code, sources, timestamps,
kind, status, confidence).  Two records can agree on some attributes and differ
on others, which is exactly what lets the agent reason about them — e.g. "these
two codes are the same age, but one is corroborated by three sources".

This module holds no decision logic.  All the belief *math* lives in
`agent/model.py`, which is pure.  `state.py` stores, serialises, and applies the
values that `model.py` computes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator

# The belief file's schema version.  Bumped if CodeRecord's shape changes, so an
# old data/belief.json is discarded rather than misread.
BELIEF_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #

class CodeStatus(str, Enum):
    """
    The agent's coarse verdict on a code.

    `UNVERIFIED` is the value a record carries between being learned about
    (RECONCILE) and first being scored (RANK) — the agent knows the code exists
    but has not yet formed a graded belief about it.  The other three are
    assigned by `model.classify()` from the confidence number, and are the three
    colours the GUI paints: green / amber / grey.
    """

    UNVERIFIED = "UNVERIFIED"
    ACTIVE = "ACTIVE"
    SUSPECT = "SUSPECT"
    DEAD = "DEAD"


class CodeKind(str, Enum):
    """
    Why the code was issued.  This matters because the world does not evolve
    uniformly: ABOUT.md notes that a limited-time *event* code dies sooner than
    a *milestone* ("100K likes") code.  `model.decay_factor()` stretches or
    shrinks the half-life by kind, so the "how the world evolves" component is
    genuinely a model of the domain and not a single global constant.

    `UNKNOWN` is the honest default — the agent guesses kind from the reward
    text and should not pretend to more knowledge than it has.
    """

    EVENT = "event"
    MILESTONE = "milestone"
    UNKNOWN = "unknown"


class VerificationOutcome(str, Enum):
    """The result of the user pasting a code into the game by hand."""

    WORKING = "working"
    DEAD = "dead"


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #

@dataclass
class SourceSighting:
    """
    One source's testimony about one code: 'example_wiki said this code exists,
    first on day 0, most recently on day 7'.

    Kept per-source rather than collapsed into a count because corroboration is
    about *how many distinct sources* agree, and because when a code turns out
    to be dead the agent needs to know exactly which sources vouched for it in
    order to adjust their trust (the refinement step in ABOUT.md).
    """

    source_id: str
    first_seen: datetime
    last_seen: datetime

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SourceSighting":
        return cls(
            source_id=raw["source_id"],
            first_seen=_parse_time(raw["first_seen"]),
            last_seen=_parse_time(raw["last_seen"]),
        )


@dataclass
class Verification:
    """
    A ground-truth reading.  The user pasted the code into Roblox themselves and
    told the agent what happened.

    This is the agent's *only* true sensor — every other input is a claim by a
    third party.  The agent never produces one of these on its own, because it
    never touches the Roblox client (see the scope boundary in ABOUT.md).
    """

    outcome: VerificationOutcome
    at: datetime

    def to_dict(self) -> dict:
        return {"outcome": self.outcome.value, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict) -> "Verification":
        return cls(
            outcome=VerificationOutcome(raw["outcome"]),
            at=_parse_time(raw["at"]),
        )


# --------------------------------------------------------------------------- #
# The record — the factored representation of one code
# --------------------------------------------------------------------------- #

@dataclass
class CodeRecord:
    """
    Everything the agent believes about one code.

    Note what is *not* here: whether the code actually works.  The agent cannot
    know that.  `confidence` is a prediction, `status` is a label derived from
    that prediction, and `last_verified` is the only field ever set from
    observed fact.
    """

    code: str                                   # the string the user pastes
    game: str                                   # which game it belongs to
    sources: dict[str, SourceSighting] = field(default_factory=dict)
    first_seen: datetime | None = None          # when the agent first learned of it
    last_corroborated: datetime | None = None   # most recent time ANY source listed it
    last_verified: Verification | None = None   # most recent user-reported outcome
    claimed_reward: str = ""                    # what the sources say it gives
    kind: CodeKind = CodeKind.UNKNOWN           # drives the decay rate
    status: CodeStatus = CodeStatus.UNVERIFIED  # assigned by RANK
    confidence: float = 0.0                     # the agent's belief, 0.0 - 1.0
    retired: bool = False                       # PURGE drops it from the active list,
                                                # but the record itself is kept

    # -- convenience views over the factored attributes --------------------- #

    @property
    def source_ids(self) -> list[str]:
        """Distinct sources that have ever listed this code, in first-seen order."""
        return [s.source_id for s in sorted(self.sources.values(), key=lambda s: s.first_seen)]

    @property
    def corroboration_count(self) -> int:
        """How many distinct sources vouch for this code.  Drives the bonus in model.py."""
        return len(self.sources)

    def age_days(self, now: datetime) -> float:
        """
        Days since any source last vouched for this code.  This — not the age
        since discovery — is what decays, because a code that is still being
        re-listed is still being implicitly endorsed.
        """
        if self.last_corroborated is None:
            return 0.0
        return max(0.0, (now - self.last_corroborated).total_seconds() / 86400.0)

    def is_verified_dead(self) -> bool:
        return (
            self.last_verified is not None
            and self.last_verified.outcome is VerificationOutcome.DEAD
        )

    def is_verified_working(self) -> bool:
        return (
            self.last_verified is not None
            and self.last_verified.outcome is VerificationOutcome.WORKING
        )

    # -- persistence -------------------------------------------------------- #

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "game": self.game,
            "sources": [s.to_dict() for s in self.sources.values()],
            "first_seen": _fmt_time(self.first_seen),
            "last_corroborated": _fmt_time(self.last_corroborated),
            "last_verified": self.last_verified.to_dict() if self.last_verified else None,
            "claimed_reward": self.claimed_reward,
            "kind": self.kind.value,
            "status": self.status.value,
            "confidence": round(self.confidence, 6),
            "retired": self.retired,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "CodeRecord":
        sightings = [SourceSighting.from_dict(s) for s in raw.get("sources", [])]
        return cls(
            code=raw["code"],
            game=raw["game"],
            sources={s.source_id: s for s in sightings},
            first_seen=_parse_time(raw.get("first_seen")),
            last_corroborated=_parse_time(raw.get("last_corroborated")),
            last_verified=(
                Verification.from_dict(raw["last_verified"])
                if raw.get("last_verified")
                else None
            ),
            claimed_reward=raw.get("claimed_reward", ""),
            kind=CodeKind(raw.get("kind", CodeKind.UNKNOWN.value)),
            status=CodeStatus(raw.get("status", CodeStatus.UNVERIFIED.value)),
            confidence=float(raw.get("confidence", 0.0)),
            retired=bool(raw.get("retired", False)),
        )


# --------------------------------------------------------------------------- #
# The belief store
# --------------------------------------------------------------------------- #

class BeliefStore:
    """
    The agent's model of the world: every code it knows about, plus how much it
    trusts each source.

    Deliberate omissions:

    * It has no notion of "the real answer".  Nothing in here is ground truth
      except the `Verification` objects the user supplies.
    * It does no scoring.  `model.py` computes numbers; this class stores them.
      That split is what lets the model be unit-tested against a fake clock with
      no files, network, or GUI in the way.
    """

    def __init__(self, source_trust: dict[str, float] | None = None, default_trust: float = 0.6):
        self._records: dict[tuple[str, str], CodeRecord] = {}   # (game, code) -> record
        self.source_trust: dict[str, float] = dict(source_trust or {})
        self.default_trust = default_trust

    # -- queries ------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[CodeRecord]:
        return iter(self._records.values())

    def get(self, game: str, code: str) -> CodeRecord | None:
        return self._records.get((game, code))

    def all_records(self, game: str | None = None) -> list[CodeRecord]:
        """Every record, retired ones included.  Use for the audit view."""
        records = list(self._records.values())
        if game is not None:
            records = [r for r in records if r.game == game]
        return records

    def active_records(self, game: str | None = None) -> list[CodeRecord]:
        """Records the agent is still willing to present.  PURGE sets `retired`."""
        return [r for r in self.all_records(game) if not r.retired]

    def trust_for(self, source_id: str) -> float:
        """How much weight this source's testimony carries.  Unknown sources get the default."""
        return self.source_trust.get(source_id, self.default_trust)

    def trust_map(self) -> dict[str, float]:
        """Snapshot of current trust, for handing to the pure functions in model.py."""
        return dict(self.source_trust)

    # -- mutations ---------------------------------------------------------- #

    def upsert_sighting(
        self,
        game: str,
        code: str,
        source_id: str,
        seen_at: datetime,
        claimed_reward: str = "",
        kind: CodeKind = CodeKind.UNKNOWN,
    ) -> tuple[CodeRecord, bool]:
        """
        Fold one percept into the belief.  Returns `(record, is_new)`.

        This is the RECONCILE step's only write path, and it is the *entire*
        interface between the outside world and the belief store.  Note that it
        takes plain values, not a source object or a page — by the time data
        reaches here it has already been normalised into a percept, so the
        belief store can never accidentally depend on how a website is laid out.
        """
        key = (game, code)
        record = self._records.get(key)
        is_new = record is None

        if record is None:
            record = CodeRecord(
                code=code,
                game=game,
                first_seen=seen_at,
                claimed_reward=claimed_reward,
                kind=kind,
            )
            self._records[key] = record

        sighting = record.sources.get(source_id)
        if sighting is None:
            record.sources[source_id] = SourceSighting(
                source_id=source_id, first_seen=seen_at, last_seen=seen_at
            )
        else:
            sighting.last_seen = max(sighting.last_seen, seen_at)

        # Corroboration is "the most recent time ANY source listed it", so this
        # moves forward whenever any source re-lists the code.
        if record.last_corroborated is None or seen_at > record.last_corroborated:
            record.last_corroborated = seen_at

        # Later sightings may carry better metadata than the first one did.
        if claimed_reward and not record.claimed_reward:
            record.claimed_reward = claimed_reward
        if kind is not CodeKind.UNKNOWN and record.kind is CodeKind.UNKNOWN:
            record.kind = kind

        # A code re-listed after being purged is worth looking at again.  The
        # exception is a code the user has personally seen fail: no amount of
        # third-party listing outweighs one first-hand observation.
        if record.retired and not record.is_verified_dead():
            record.retired = False

        return record, is_new

    def record_verification(
        self, game: str, code: str, outcome: VerificationOutcome, now: datetime
    ) -> CodeRecord | None:
        """
        Attach a ground-truth reading to a record.  The belief *value* is then
        recomputed by `model.apply_verification()`; this method only files the
        observation itself.
        """
        record = self.get(game, code)
        if record is None:
            return None
        record.last_verified = Verification(outcome=outcome, at=now)
        return record

    def put(self, record: CodeRecord) -> None:
        """
        Write a record back into the belief, replacing any existing one.

        This is how a value computed by the pure functions in `model.py` lands
        in the store.  `model.apply_verification()` returns a *new* record
        rather than mutating one, so the correction step in `agent/fsm.py` reads
        as two plain lines — compute the corrected belief, then store it — which
        is exactly the pair of lines the report points at.
        """
        self._records[(record.game, record.code)] = record

    def set_trust(self, source_id: str, trust: float) -> None:
        self.source_trust[source_id] = max(0.0, min(1.0, trust))

    def seed_trust(self, source_id: str, initial_trust: float) -> None:
        """Set a source's starting trust from config, without clobbering a learned value."""
        self.source_trust.setdefault(source_id, initial_trust)

    def retire(self, record: CodeRecord) -> None:
        """
        PURGE: drop a code from the presented list but keep the record.

        Deleting it would be a bug, not an optimisation — the agent would forget
        the code, re-learn it from the next poll, and present it as fresh.  A
        model-based agent is supposed to remember what it has concluded.
        """
        record.retired = True

    # -- persistence -------------------------------------------------------- #

    def to_dict(self) -> dict:
        return {
            "schema_version": BELIEF_SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "source_trust": self.source_trust,
            "default_trust": self.default_trust,
            "records": [r.to_dict() for r in self._records.values()],
        }

    def save(self, path: str | Path) -> None:
        """Write belief to disk.  Atomic-ish: write a temp file, then replace."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(
        cls,
        path: str | Path,
        default_trust: float = 0.6,
    ) -> "BeliefStore":
        """
        Read belief from disk, returning an empty store if the file is missing,
        corrupt, or written by an older schema.

        Losing belief is survivable — the agent simply starts uncertain again,
        which is the correct behaviour when it cannot trust its own memory.
        Crashing on a bad file is not survivable, so this never raises.
        """
        path = Path(path)
        if not path.exists():
            return cls(default_trust=default_trust)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Valid JSON is not necessarily a belief file: `null`, a list, or a
            # bare number all parse cleanly and then fail on attribute access.
            if not isinstance(raw, dict):
                return cls(default_trust=default_trust)
            if raw.get("schema_version") != BELIEF_SCHEMA_VERSION:
                return cls(default_trust=default_trust)
            store = cls(
                source_trust=raw.get("source_trust", {}),
                default_trust=float(raw.get("default_trust", default_trust)),
            )
            for record_raw in raw.get("records", []):
                record = CodeRecord.from_dict(record_raw)
                store._records[(record.game, record.code)] = record
            return store
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
            return cls(default_trust=default_trust)

    def bulk_add(self, records: Iterable[CodeRecord]) -> None:
        """Test/demo helper: drop pre-built records straight in."""
        for record in records:
            self._records[(record.game, record.code)] = record


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #

def _fmt_time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_time(value: str | None) -> datetime | None:
    """
    Parse an ISO timestamp, forcing it timezone-aware in UTC.

    Naive and aware datetimes cannot be compared in Python, and this agent does
    arithmetic on timestamps constantly (ages, decay, virtual clock jumps).  One
    naive datetime leaking in would raise TypeError somewhere far from its
    origin, so everything is normalised at the boundary.
    """
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# Roblox codes are short, uppercase, alphanumeric, occasionally hyphenated or
# with digits.  Used by percepts.py to reject page furniture ("CLICK", "SUBSCRIBE")
# that a loose parser would otherwise mistake for codes.
CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{2,31}$")

# Words that appear in the same markup as codes and happen to match the pattern
# above.  A parser that misses a real code costs the user one code; a parser that
# invents one costs the agent its credibility and would violate the fixture rule
# in CLAUDE.md, so the bias here is firmly toward rejecting.
NON_CODE_WORDS = frozenset({
    # listing-page vocabulary
    "CODE", "CODES", "COPY", "COPIED", "ACTIVE", "EXPIRED", "NEW", "UPDATE",
    "UPDATED", "WORKING", "REWARD", "REWARDS", "FREE", "REDEEM", "ENTER",
    "LIST", "ALL", "NONE", "RELATED", "TAGS", "GUIDE", "GUIDES", "NOTE",
    # navigation and social furniture
    "SUBSCRIBE", "FOLLOW", "LIKE", "SHARE", "TWITTER", "DISCORD", "YOUTUBE",
    "TIKTOK", "INSTAGRAM", "FACEBOOK", "BLUESKY", "NEWSLETTER", "HERE",
    "CLICK", "READ", "MORE", "HOME", "MENU", "SEARCH", "LOGIN", "SIGNUP",
    # the game and platform themselves
    "ROBLOX", "GAME", "GAMES", "SLAYER", "SLAYERS", "PROJECT", "ANIME",
    # instruction verbs that get bolded on how-to-redeem sections
    "LAUNCH", "PRESS", "LOAD", "JOIN", "TYPE", "OPEN", "PLAY", "START",
    # number words, which get bolded in sentences like "Four codes are working"
    "ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
    "NINE", "TEN", "ELEVEN", "TWELVE",
    # month names, from "Last checked September 2026" headings
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JUNE", "JULY", "AUGUST",
    "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
})


def normalize_code(raw: str) -> str | None:
    """
    Canonical form for a code string, or None if it does not look like a code.

    Codes are matched case-insensitively across sources but pasted uppercase,
    so the store keys on the uppercase form.  Returning None rather than raising
    is deliberate: a source listing junk should degrade the percept, not the run.
    """
    if not raw:
        return None
    candidate = raw.strip().strip('"“”‘’\'').upper()
    # Internal whitespace disqualifies outright.  Collapsing it instead would
    # quietly turn page furniture like "Click Here" into the plausible-looking
    # code "CLICKHERE" — exactly the kind of invented string CLAUDE.md forbids.
    if re.search(r"\s", candidate):
        return None
    if candidate in NON_CODE_WORDS:
        return None
    if not CODE_PATTERN.match(candidate):
        return None
    return candidate
