"""
agent/model.py — the transition model: how the world evolves, and what the
agent's own actions do to its beliefs.

Model-based agent role
----------------------
Russell & Norvig say a model-based agent needs two kinds of knowledge:

  1. "How the world evolves independently of the agent."
     Here: codes die silently.  `decay_factor()` encodes that as an exponential
     fall-off in confidence with time since the last corroboration, with a
     half-life that depends on the *kind* of code — an event code dies sooner
     than a milestone code.  Nothing observes this happening.  It is predicted.

  2. "What my own actions do."
     Here: polling a source and finding a code listed again refreshes its
     corroboration timestamp; finding it listed by several independent sources
     raises the belief (`corroboration_bonus()`); and a user-reported redemption
     outcome snaps the belief to truth (`apply_verification()`) and adjusts how
     much the agent trusts the sources that vouched for it
     (`refine_source_trust()`).

PURITY RULE (enforced by CLAUDE.md, relied on by the virtual clock)
-------------------------------------------------------------------
Every function in this module is pure:

  * no I/O, no network, no file access
  * **no clock reads** — `now` is always a parameter

That is what lets `--demo` fast-forward a week in two seconds, and what lets the
unit tests in `tests/test_model.py` drive the whole belief model off a fake
clock with nothing else running.  If you ever feel the urge to call
`datetime.now()` in this file, the virtual clock is what you are breaking.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace
from datetime import datetime

from agent.state import (
    CodeKind,
    CodeRecord,
    CodeStatus,
    Verification,
    VerificationOutcome,
)


# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ConfidenceParams:
    """
    Every number the belief model uses, in one place, loaded from
    `config/games.json`.

    Frozen on purpose.  These are the constants of the agent's world-model; if
    they could drift mid-run, a confidence bar the user watched fall would no
    longer mean what it meant a minute ago.  ABOUT.md documents what each one
    represents, and CLAUDE.md requires the two stay in agreement.
    """

    half_life_days: float = 7.0
    corroboration_bonus_per_source: float = 0.15
    max_corroboration_bonus: float = 0.45
    suspect_threshold: float = 0.45
    dead_threshold: float = 0.15
    purge_threshold: float = 0.05
    verified_working_confidence: float = 1.0
    verified_dead_confidence: float = 0.0
    kind_half_life_multipliers: tuple[tuple[str, float], ...] = (
        ("event", 0.5),
        ("unknown", 1.0),
        ("milestone", 2.0),
    )
    trust_reward_on_working: float = 0.05
    trust_penalty_on_dead: float = 0.10
    trust_floor: float = 0.05
    trust_ceiling: float = 0.95

    @classmethod
    def from_config(cls, config: dict) -> "ConfidenceParams":
        """
        Build params from a parsed games.json, ignoring `_comment` keys and any
        unrecognised field.  Unknown keys are skipped rather than raising so
        that a config written for a newer version still boots.
        """
        confidence = {k: v for k, v in (config.get("confidence") or {}).items()
                      if not k.startswith("_")}
        trust = {k: v for k, v in (config.get("source_trust") or {}).items()
                 if not k.startswith("_")}

        multipliers = confidence.pop("kind_half_life_multipliers", None)
        merged: dict = {}
        merged.update(confidence)
        # Only the refinement knobs come from the source_trust block; `default`
        # belongs to the BeliefStore, not to the confidence math.
        for key in ("trust_reward_on_working", "trust_penalty_on_dead",
                    "trust_floor", "trust_ceiling"):
            if key in trust:
                merged[key] = trust[key]

        known = {f.name for f in dataclasses.fields(cls)}
        merged = {k: v for k, v in merged.items() if k in known}
        if multipliers:
            merged["kind_half_life_multipliers"] = tuple(
                (k, float(v)) for k, v in multipliers.items() if not k.startswith("_")
            )
        return cls(**merged)

    def half_life_for(self, kind: CodeKind) -> float:
        """
        The half-life this particular code decays at.

        This is the "how the world evolves" component being genuinely a *model*
        rather than a constant: the agent holds a belief about the domain — that
        weekend event codes are swept out faster than milestone reward codes —
        and applies it to codes it has never observed expiring.
        """
        for key, multiplier in self.kind_half_life_multipliers:
            if key == kind.value:
                return max(0.01, self.half_life_days * multiplier)
        return self.half_life_days


DEFAULT_PARAMS = ConfidenceParams()


# --------------------------------------------------------------------------- #
# Component 1 — how the world evolves on its own
# --------------------------------------------------------------------------- #

def decay_factor(age_days: float, half_life_days: float) -> float:
    """
    The surviving fraction of a belief after `age_days` with no new evidence.

    Exponential half-life decay: the value halves every `half_life_days`.  It is
    monotonically decreasing, never negative, and never reaches zero — the agent
    grows steadily less sure but never becomes certain a code is dead without
    actually observing it.  That asymmetry is the point: only the user's
    redemption report can produce certainty.

        decay_factor(0,  7) == 1.0
        decay_factor(7,  7) == 0.5
        decay_factor(14, 7) == 0.25
    """
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / max(0.01, half_life_days))


def decay(record: CodeRecord, now: datetime, params: ConfidenceParams = DEFAULT_PARAMS) -> float:
    """
    The decay multiplier for one record at time `now`.

    Note what drives it: time since **last corroboration**, not time since
    discovery.  A code a wiki is still re-listing today is being implicitly
    re-endorsed today, even though nobody has confirmed it works.  A code that
    has quietly dropped off every page it used to appear on is the one that
    should be decaying, and it is.
    """
    return decay_factor(record.age_days(now), params.half_life_for(record.kind))


# --------------------------------------------------------------------------- #
# Component 2 — what the agent's own actions do
# --------------------------------------------------------------------------- #

def source_prior(
    record: CodeRecord,
    trust: dict[str, float],
    default_trust: float = 0.6,
) -> float:
    """
    The belief a code starts from, given who vouched for it: the mean trust of
    its listing sources.

    Mean rather than max, so that a code appearing on one reliable site and two
    unreliable ones is not scored as if the unreliable ones were absent.  The
    reward for breadth is handled separately by `corroboration_bonus()` — this
    term is about *quality* of testimony, that one is about *quantity*.
    """
    if not record.sources:
        return 0.0
    values = [trust.get(sid, default_trust) for sid in record.sources]
    return sum(values) / len(values)


def corroborate(
    record: CodeRecord,
    params: ConfidenceParams = DEFAULT_PARAMS,
) -> float:
    """
    The bonus for independent agreement across sources.

    Each source beyond the first adds a fixed amount, capped.  The first source
    adds nothing here because it is already represented in `source_prior()` —
    corroboration means *additional* witnesses.

    Capping matters.  Without it, a code listed by ten scraper sites that all
    copy each other would outrank a code the user personally redeemed yesterday,
    which would be exactly backwards: those ten sites are not ten independent
    observations.
    """
    extra_sources = max(0, record.corroboration_count - 1)
    return min(
        params.max_corroboration_bonus,
        extra_sources * params.corroboration_bonus_per_source,
    )


def evidence_value(
    record: CodeRecord,
    trust: dict[str, float],
    params: ConfidenceParams = DEFAULT_PARAMS,
    default_trust: float = 0.6,
) -> float:
    """
    What third-party listings are worth at the moment they were last made,
    before any time has passed: quality of sources plus breadth of agreement.
    """
    return _clamp(source_prior(record, trust, default_trust) + corroborate(record, params))


# --------------------------------------------------------------------------- #
# Putting it together — the belief itself
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScoreBreakdown:
    """
    A score together with the reasoning that produced it.

    This exists because CLAUDE.md asks for confidence to be *legible*: the user
    should be able to look at a bar and understand roughly why it sits where it
    does.  The GUI shows `lines` as hover text, so the internal model is not
    just present but visible — which is the whole thing a five-minute demo video
    has to get across.
    """

    confidence: float
    status: CodeStatus
    listing_evidence: float      # what the sources were worth when last seen
    listing_decayed: float       # ...after time did its work
    firsthand_decayed: float     # ...and what the user's own report is worth now
    age_days: float
    half_life_days: float
    lines: tuple[str, ...]

    def explanation(self) -> str:
        return "\n".join(self.lines)


def score(
    record: CodeRecord,
    now: datetime,
    params: ConfidenceParams = DEFAULT_PARAMS,
    trust: dict[str, float] | None = None,
    default_trust: float = 0.6,
) -> float:
    """
    The agent's confidence that this code still works, 0.0 to 1.0.

    The agent weighs two strands of evidence and believes whichever has survived
    better:

      A. **Third-party listings** — worth `evidence_value()` at the moment they
         were made, decayed by however long ago that was.
      B. **The user's own redemption report** — worth 1.0 at the moment it was
         made, decayed the same way.

    Both decay at the same rate, because decay models *the world changing*, not
    the evidence getting worse.  A code the user redeemed a week ago and a code
    a wiki listed a week ago have had the same week to be revoked; they differ
    in how sure the agent was to begin with, not in how fast that erodes.

    A reported failure is different, and absorbing: a code the user watched be
    rejected does not come back, so no amount of subsequent listing raises it.
    """
    return explain(record, now, params, trust, default_trust).confidence


def explain(
    record: CodeRecord,
    now: datetime,
    params: ConfidenceParams = DEFAULT_PARAMS,
    trust: dict[str, float] | None = None,
    default_trust: float = 0.6,
) -> ScoreBreakdown:
    """`score()` with its working shown.  See `ScoreBreakdown`."""
    trust = trust or {}
    half_life = params.half_life_for(record.kind)
    age = record.age_days(now)

    # --- the absorbing case: first-hand evidence of failure ---------------- #
    if record.is_verified_dead():
        verified_age = _days_between(record.last_verified.at, now)
        return ScoreBreakdown(
            confidence=params.verified_dead_confidence,
            status=CodeStatus.DEAD,
            listing_evidence=0.0,
            listing_decayed=0.0,
            firsthand_decayed=0.0,
            age_days=age,
            half_life_days=half_life,
            lines=(
                f"You reported this code as DEAD {_ago(verified_age)}.",
                "First-hand observation overrides every listing — the agent will not "
                "re-raise this code no matter how many sources keep publishing it.",
            ),
        )

    # --- strand A: what the sources claim ---------------------------------- #
    listing_evidence = evidence_value(record, trust, params, default_trust)
    listing_decayed = listing_evidence * decay_factor(age, half_life)

    # --- strand B: what the user saw for themselves ------------------------ #
    firsthand_decayed = 0.0
    verified_age = 0.0
    if record.is_verified_working():
        verified_age = _days_between(record.last_verified.at, now)
        firsthand_decayed = params.verified_working_confidence * decay_factor(
            verified_age, half_life
        )

    confidence = _clamp(max(listing_decayed, firsthand_decayed))
    status = classify(confidence, params)

    # --- the human-readable account ---------------------------------------- #
    source_names = ", ".join(record.source_ids) or "no source"
    lines: list[str] = []
    if record.corroboration_count > 1:
        lines.append(
            f"{record.corroboration_count} sources list this code ({source_names}); "
            f"mean trust {source_prior(record, trust, default_trust):.2f} "
            f"+ {corroborate(record, params):.2f} corroboration = {listing_evidence:.2f}."
        )
    else:
        lines.append(
            f"Listed by {source_names} only; trust "
            f"{source_prior(record, trust, default_trust):.2f}, no corroboration bonus."
        )
    lines.append(
        f"Last corroborated {_ago(age)}. A {record.kind.value} code halves every "
        f"{half_life:g} days, so that evidence is now worth {listing_decayed:.2f}."
    )
    if firsthand_decayed > 0:
        lines.append(
            f"You reported it WORKING {_ago(verified_age)}; that first-hand reading "
            f"started at {params.verified_working_confidence:.2f} and has decayed to "
            f"{firsthand_decayed:.2f}."
        )
        lines.append("The agent believes the stronger of the two: "
                     f"{confidence:.2f}.")
    lines.append(f"Confidence {confidence:.2f} -> {status.value}.")

    return ScoreBreakdown(
        confidence=confidence,
        status=status,
        listing_evidence=listing_evidence,
        listing_decayed=listing_decayed,
        firsthand_decayed=firsthand_decayed,
        age_days=age,
        half_life_days=half_life,
        lines=tuple(lines),
    )


def classify(confidence: float, params: ConfidenceParams = DEFAULT_PARAMS) -> CodeStatus:
    """
    Turn a number into the label the GUI paints.

    Three bands, because three is what a user can act on: try it (green), try it
    last (amber), don't bother (grey).  Thresholds live in config so the demo can
    be tuned without touching the model.
    """
    if confidence >= params.suspect_threshold:
        return CodeStatus.ACTIVE
    if confidence >= params.dead_threshold:
        return CodeStatus.SUSPECT
    return CodeStatus.DEAD


# --------------------------------------------------------------------------- #
# The correction step
# --------------------------------------------------------------------------- #

def apply_verification(
    record: CodeRecord,
    outcome: VerificationOutcome,
    now: datetime,
    params: ConfidenceParams = DEFAULT_PARAMS,
) -> CodeRecord:
    """
    Snap a belief to observed truth.  **This is the correction step.**

    The user pasted the code into Roblox by hand and reported what happened.
    That is the agent's only true sensor reading, and it dominates: whatever the
    model had predicted, the belief is replaced outright.

    Returns a NEW record rather than mutating the argument, keeping this module
    pure.  The caller (`agent/fsm.py`) writes it back into the `BeliefStore`, so
    the one line in the FSM that performs the correction is easy to point at in
    the report.
    """
    verification = Verification(outcome=outcome, at=now)
    if outcome is VerificationOutcome.WORKING:
        confidence = params.verified_working_confidence
    else:
        confidence = params.verified_dead_confidence

    return replace(
        record,
        last_verified=verification,
        confidence=confidence,
        status=classify(confidence, params),
    )


def refine_source_trust(
    trust: dict[str, float],
    record: CodeRecord,
    outcome: VerificationOutcome,
    params: ConfidenceParams = DEFAULT_PARAMS,
    default_trust: float = 0.6,
) -> dict[str, float]:
    """
    Model refinement: revise trust in every source that listed the verified code.

    A site that keeps publishing codes the user then finds dead loses influence
    over future rankings; a site that is consistently right gains it.  In
    Russell & Norvig's terms this is the learning element improving the agent's
    "what my actions do" component from experience.

    Because that edges toward learning-agent territory and the assignment asks
    for a model-based agent, the GUI gates this behind a toggle — the agent can
    be demonstrated pure, then again with refinement on.  See ABOUT.md.

    The penalty is larger than the reward on purpose.  A dead code is strong
    evidence a source is stale; a working code is weak evidence it is good,
    since even a bad source lists mostly-working codes right after an update.

    Returns a NEW dict; the input is not mutated.
    """
    updated = dict(trust)
    delta = (
        params.trust_reward_on_working
        if outcome is VerificationOutcome.WORKING
        else -params.trust_penalty_on_dead
    )
    for source_id in record.sources:
        current = updated.get(source_id, default_trust)
        updated[source_id] = _clamp(
            current + delta, params.trust_floor, params.trust_ceiling
        )
    return updated


# --------------------------------------------------------------------------- #
# Belief -> decision
# --------------------------------------------------------------------------- #

def rank_records(
    records: list[CodeRecord],
    now: datetime,
    params: ConfidenceParams = DEFAULT_PARAMS,
    trust: dict[str, float] | None = None,
    default_trust: float = 0.6,
) -> list[tuple[CodeRecord, ScoreBreakdown]]:
    """
    Score every record and order them best-bet-first.

    Ties break on corroboration count, then on recency of corroboration, then
    alphabetically — so the ordering is stable across ticks.  A list that
    reshuffles for no reason would undermine the demo, because the user could
    not tell a real belief change from noise.
    """
    trust = trust or {}
    scored = [
        (record, explain(record, now, params, trust, default_trust))
        for record in records
    ]
    scored.sort(
        key=lambda pair: (
            -pair[1].confidence,
            -pair[0].corroboration_count,
            pair[1].age_days,
            pair[0].code,
        )
    )
    return scored


def should_purge(
    record: CodeRecord,
    confidence: float,
    params: ConfidenceParams = DEFAULT_PARAMS,
) -> bool:
    """
    Whether PURGE should retire a code from the presented list.

    Deliberately stricter than the DEAD label.  If purging fired at
    `dead_threshold`, a code would turn grey and disappear on the same tick and
    the user would never see the grey state — and watching a code go green,
    amber, grey is the demo beat this whole project is built around.  So DEAD is
    a warning the agent still shows, and `purge_threshold` is where it finally
    stops bothering the user.

    A code the user personally watched fail is retired immediately; there is
    nothing left to learn from showing it.
    """
    if record.is_verified_dead():
        return True
    return confidence < params.purge_threshold


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _days_between(earlier: datetime, later: datetime) -> float:
    return max(0.0, (later - earlier).total_seconds() / 86400.0)


def _ago(days: float) -> str:
    """Render an age the way a person would say it, for the hover text."""
    if days < 1 / 24:
        return "just now"
    if days < 1:
        return f"{days * 24:.0f}h ago"
    if days < 2:
        return "1 day ago"
    return f"{days:.1f} days ago"
