"""
sources/canned.py — replays recorded snapshots against a virtual clock.

Model-based agent role
----------------------
This module is the **environment**, standing in for the real one.  It holds the
ground truth of what each source showed at each moment, and it reveals exactly
as much of that as a poll at a given time would reveal — never more.

That restraint is the whole reason the canned source is worth having.  It would
be trivial to hand the agent the full timeline and let it "know" when each code
appeared and vanished.  That would also destroy the demonstration: the agent
would no longer be inferring anything.  So this class exposes one method, the
same `poll(game, now)` the live source exposes, and the agent cannot tell which
one it is talking to.

`data/snapshots/` is ground truth.  `BeliefStore` is belief.  This file reads
the former and never imports the latter — the two meet only as percepts, which
is the separation CLAUDE.md requires and the report points at.

Replay semantics
----------------
A poll at virtual time T returns the **most recent wave at or before T** for
each source: what that page was last showing.  So a code that keeps appearing
stays corroborated and holds its confidence, while a code that drops off a page
stops being corroborated and starts to decay.  That asymmetry is what produces
the demo: some bars hold while others fall, and the difference is information
the agent inferred rather than information it was told.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sources.base import RawListing, Source, SourceError


@dataclass(frozen=True)
class Snapshot:
    """One recorded view of one source at one moment."""

    source_id: str
    source_display_name: str
    game: str
    offset_days: float
    provenance: str            # "SYNTHETIC" or "RECORDED"
    capture_note: str
    listings: tuple[RawListing, ...]


class CannedSource(Source):
    """
    Replays one source's snapshot history.

    One instance per source, so that the agent sees several independent sources
    exactly as it would live — which matters, because corroboration across
    sources is half the belief model and would be untestable with a single
    merged feed.
    """

    def __init__(
        self,
        source_id: str,
        snapshots: list[Snapshot],
        epoch: datetime,
        display_name: str | None = None,
    ):
        self.source_id = source_id
        self.display_name = display_name or (
            snapshots[0].source_display_name if snapshots else source_id
        )
        self.epoch = epoch
        # Sorted so `poll` can scan for the latest wave at or before `now`.
        self._snapshots = sorted(snapshots, key=lambda s: s.offset_days)

    @property
    def wave_offsets(self) -> list[float]:
        """Virtual days at which this source's content changes.  Shown in the GUI."""
        return [s.offset_days for s in self._snapshots]

    def poll(self, game: str, now: datetime) -> list[RawListing]:
        """
        What this source was showing at virtual time `now`.

        Before the first recorded wave the source legitimately has nothing to
        say, which is modelled as a failure rather than an empty list — an agent
        told "zero codes" would take that as evidence every code it knows has
        been removed, which is a much stronger claim than "I couldn't read the
        page".  Distinguishing silence from absence is a real modelling concern,
        not a technicality.
        """
        current = self._wave_at(now)
        if current is None:
            raise SourceError(
                f"no snapshot recorded at or before virtual day "
                f"{self._elapsed_days(now):.1f}"
            )
        if current.game != game:
            raise SourceError(f"snapshot is for game {current.game!r}, not {game!r}")

        # Re-stamped with the observation time: the agent is learning this now,
        # whatever wave it happens to come from.
        return [
            RawListing(
                source_id=listing.source_id,
                code=listing.code,
                claimed_reward=listing.claimed_reward,
                kind_hint=listing.kind_hint,
                observed_at=now,
            )
            for listing in current.listings
        ]

    def _elapsed_days(self, now: datetime) -> float:
        return (now - self.epoch).total_seconds() / 86400.0

    def _wave_at(self, now: datetime) -> Snapshot | None:
        elapsed = self._elapsed_days(now)
        latest: Snapshot | None = None
        for snapshot in self._snapshots:
            if snapshot.offset_days <= elapsed + 1e-9:
                latest = snapshot
            else:
                break
        return latest


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_snapshots(snapshot_dir: str | Path, game: str, subset: str = "") -> list[Snapshot]:
    """
    Read every snapshot file for a game.

    `subset` selects a sub-directory of fixtures.  Two sets ship:

      * the default (empty) set, `data/snapshots/<game>/`, holds the synthetic
        DEMO_ narrative the video follows — arranged so that some codes hold
        their confidence while others decay;
      * `"recorded"`, `data/snapshots/<game>/recorded/`, holds genuinely
        recorded listings written by `main.py --source web --record`.

    They are kept apart so the demo narrative is not diluted by real codes
    arriving on a single date, and so it is never ambiguous which codes in a
    screenshot are synthetic.  The replay logic is identical for both.

    Two provenances are supported and treated identically at replay time:

      * `SYNTHETIC` fixtures carry an explicit `offset_days`.
      * `RECORDED` fixtures carry a real `captured_at` date; their offsets are
        derived from the earliest capture, so a set of real snapshots recorded
        over a week replays as days 0..7.

    A malformed file is skipped rather than fatal.  Losing one snapshot degrades
    the demo; crashing on it ends the run.
    """
    directory = Path(snapshot_dir) / game
    if subset:
        directory = directory / subset
    if not directory.is_dir():
        return []

    raw_files: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if payload.get("game") != game:
            continue
        payload["_path"] = path.name
        raw_files.append(payload)

    # Derive offsets for recorded snapshots, relative to the earliest capture.
    captured = [
        _parse_iso(p["captured_at"]) for p in raw_files if p.get("captured_at")
    ]
    earliest = min(captured) if captured else None

    snapshots: list[Snapshot] = []
    for payload in raw_files:
        offset = payload.get("offset_days")
        if offset is None and payload.get("captured_at") and earliest is not None:
            offset = (_parse_iso(payload["captured_at"]) - earliest).total_seconds() / 86400.0
        if offset is None:
            continue

        listings = tuple(
            RawListing(
                source_id=payload["source_id"],
                code=item.get("code", ""),
                claimed_reward=item.get("claimed_reward", ""),
                kind_hint=item.get("kind_hint", ""),
            )
            for item in payload.get("listings", [])
        )
        snapshots.append(
            Snapshot(
                source_id=payload["source_id"],
                source_display_name=payload.get("source_display_name", payload["source_id"]),
                game=game,
                offset_days=float(offset),
                provenance=payload.get("provenance", "SYNTHETIC"),
                capture_note=payload.get("capture_note", ""),
                listings=listings,
            )
        )
    return snapshots


def build_canned_sources(
    snapshot_dir: str | Path,
    game: str,
    epoch: datetime,
    subset: str = "",
) -> list[CannedSource]:
    """Group every snapshot by source and return one replaying source for each."""
    by_source: dict[str, list[Snapshot]] = {}
    for snapshot in load_snapshots(snapshot_dir, game, subset):
        by_source.setdefault(snapshot.source_id, []).append(snapshot)

    return [
        CannedSource(source_id=source_id, snapshots=snaps, epoch=epoch)
        for source_id, snaps in sorted(by_source.items())
    ]


def timeline_offsets(snapshot_dir: str | Path, game: str, subset: str = "") -> list[float]:
    """
    Every virtual day on which anything changes, for the GUI's clock control and
    for the headless runner's default schedule.
    """
    return sorted({s.offset_days for s in load_snapshots(snapshot_dir, game, subset)})


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
