"""
agent/percepts.py — raw source output becomes percepts.

Model-based agent role
----------------------
This is the agent's **sensor processing** stage, the EXTRACT state in
`docs/FSM.md`.  It sits at the narrowest point of the whole design, and that is
intentional:

    sources/  ->  [ percepts.py ]  ->  BeliefStore

Nothing else crosses.  In canned mode `data/snapshots/` is ground truth and
`BeliefStore` is belief, and CLAUDE.md requires that those two structures never
touch or share objects.  They communicate only through the `CodePercept` values
produced here — plain, immutable, already-normalised facts of the form "source
S claimed code C at time T".

Because this module is the choke point, it is also where the agent refuses to
invent things.  A string that does not look like a code is dropped, loudly, into
the rejection list rather than passed along hopefully.  An agent that fabricates
codes is worse than useless: the user wastes redemption attempts on them and the
belief model is fed noise it cannot distinguish from evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from agent.state import CodeKind, normalize_code
from sources.base import RawListing

# Reward text hints at how long a code is meant to live.  ABOUT.md's "how the
# world evolves" component decays event codes faster than milestone codes, so
# the agent has to guess a kind from the only evidence sources give it: prose.
_EVENT_HINTS = re.compile(
    r"\b(event|weekend|limited|today|tonight|24\s*h|48\s*h|hour|flash|"
    r"launch\s*day|expires?|ends?\s+(soon|today))\b",
    re.IGNORECASE,
)
_MILESTONE_HINTS = re.compile(
    r"\b(\d+\s*[km]\s*(likes?|visits?|members?|subs?|followers?)|"
    r"milestone|anniversary|thank\s*you|permanent|celebration)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CodePercept:
    """
    One normalised observation: 'source S claimed code C for game G at time T'.

    Frozen, and carrying no confidence or status.  A percept is not a belief —
    it is the input a belief is computed from.  Conflating the two is the most
    natural way to accidentally merge truth and belief, which is the thing this
    project exists to keep apart.
    """

    game: str
    code: str                       # normalised, uppercase
    source_id: str
    observed_at: datetime
    claimed_reward: str = ""
    kind: CodeKind = CodeKind.UNKNOWN


@dataclass
class PerceptBatch:
    """
    The output of one EXTRACT: what survived, and what did not.

    Rejections are kept rather than silently dropped because they are the early
    warning that a parser has drifted — a source that suddenly yields thirty
    rejections and no percepts has almost certainly changed its layout, and the
    transition log should say so before the user wonders why nothing updated.
    """

    percepts: list[CodePercept]
    rejected: list[tuple[str, str]]        # (raw string, reason)

    def __len__(self) -> int:
        return len(self.percepts)

    @property
    def source_ids(self) -> set[str]:
        return {p.source_id for p in self.percepts}

    @property
    def summary(self) -> str:
        parts = [f"{len(self.percepts)} percepts from {len(self.source_ids)} sources"]
        if self.rejected:
            parts.append(f"{len(self.rejected)} rejected")
        return ", ".join(parts)


def infer_kind(claimed_reward: str, kind_hint: str = "") -> CodeKind:
    """
    Guess whether a code is an event code or a milestone code from its blurb.

    Milestone is checked first: "100K likes — limited time" is a milestone code
    with marketing attached, and reading it as an event code would make the agent
    give up on it far too early.

    Returns UNKNOWN freely.  A wrong guess here distorts the decay rate for that
    code, so guessing only on clear evidence is the conservative choice — UNKNOWN
    simply uses the baseline half-life.
    """
    blob = f"{claimed_reward} {kind_hint}".strip()
    if not blob:
        return CodeKind.UNKNOWN
    if _MILESTONE_HINTS.search(blob):
        return CodeKind.MILESTONE
    if _EVENT_HINTS.search(blob):
        return CodeKind.EVENT
    return CodeKind.UNKNOWN


def extract(
    listings: list[RawListing],
    game: str,
    now: datetime,
) -> PerceptBatch:
    """
    Normalise raw listings into percepts.  This is the EXTRACT state.

    Three things happen, in order:

    1. **Normalisation.** Codes are uppercased and stripped so that
       "sl2launch", "SL2Launch" and '"SL2LAUNCH"' are recognised as one code
       rather than three.  Without this, corroboration would never fire, because
       two sources spelling the same code differently would look like two codes.

    2. **Rejection.** Anything that does not look like a code is discarded with
       a reason.  See `normalize_code()` in `agent/state.py`.

    3. **Deduplication within a source.** A page that lists a code twice is one
       source agreeing with itself, not two witnesses.  Collapsing here means
       the corroboration bonus in `model.py` can simply count sources and be
       right.

    `now` is the observation time, passed in rather than read, so a replayed
    snapshot is stamped with the virtual time it was replayed at.
    """
    percepts: list[CodePercept] = []
    rejected: list[tuple[str, str]] = []
    seen_per_source: set[tuple[str, str]] = set()

    for listing in listings:
        code = normalize_code(listing.code)
        if code is None:
            rejected.append((listing.code, "does not look like a code"))
            continue

        key = (listing.source_id, code)
        if key in seen_per_source:
            # Not an error: pages repeat codes in a summary table and again in
            # the body.  One source saying it twice is still one witness.
            continue
        seen_per_source.add(key)

        percepts.append(
            CodePercept(
                game=game,
                code=code,
                source_id=listing.source_id,
                observed_at=listing.observed_at or now,
                claimed_reward=listing.claimed_reward.strip(),
                kind=infer_kind(listing.claimed_reward, listing.kind_hint),
            )
        )

    return PerceptBatch(percepts=percepts, rejected=rejected)
