#!/usr/bin/env python3
"""
tools/demo_transcript.py — produce the terminal transcript for the report.

Run:  python tools/demo_transcript.py > docs/sample-run.txt

PLAN.md M5 asks for "terminal output showing state transitions".  A plain
`main.py --demo --headless` run shows the main cycle but never reaches VERIFY,
because nothing in a headless loop reports a redemption outcome.  VERIFY is the
correction step — the single most important state in the argument — so this
script drives the same agent through a scripted narrative that visits it.

The narrative is the one the demo video follows:

  Act 1  the agent learns six codes from three sources
  Act 2  time passes; some codes keep being listed and hold their confidence,
         others quietly drop off pages and decay — with nobody announcing
         anything
  Act 3  every source fails, and the agent updates its beliefs anyway
  Act 4  the user reports a code dead; belief snaps to truth and, with
         refinement on, that source's other codes fall with it

Nothing here is special-cased: it calls the same `AgentFSM` the GUI calls.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.fsm import AgentFSM                      # noqa: E402
from agent.model import ConfidenceParams            # noqa: E402
from agent.state import BeliefStore, VerificationOutcome  # noqa: E402
from main import load_config, print_tick            # noqa: E402
from sources.base import RawListing, Source, SourceError  # noqa: E402
from sources.canned import build_canned_sources     # noqa: E402

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class DeadSource(Source):
    """Stands in for a site that has gone down or changed its layout."""

    def __init__(self, source_id: str):
        self.source_id = source_id
        self.display_name = source_id

    def poll(self, game: str, now: datetime) -> list[RawListing]:
        raise SourceError("HTTP 503 Service Unavailable")


def banner(title: str, subtitle: str = "") -> None:
    print()
    print("=" * 80)
    print(title)
    if subtitle:
        print(subtitle)
    print("=" * 80)


def main() -> int:
    config = load_config()
    store = BeliefStore(default_trust=0.6)
    for entry in config["games"]["slayers2"]["sources"]:
        store.seed_trust(entry["id"], float(entry["initial_trust"]))

    live_sources = build_canned_sources(ROOT / "data" / "snapshots", "slayers2", EPOCH)
    fsm = AgentFSM(
        store=store,
        sources=live_sources,
        params=ConfidenceParams.from_config(config),
        game="slayers2",
        refinement_enabled=False,
    )

    print("CodeSeeker — model-based agent, scripted demo run")
    print("Canned source replaying data/snapshots/slayers2 against a virtual clock.")
    print("All codes are DEMO_-prefixed synthetic fixtures (see CLAUDE.md).")
    print(f"Source trust at start: {store.trust_map()}")

    # ---- Act 1 & 2: the world evolves unobserved ------------------------- #
    for day in (0, 3, 6, 9, 12):
        banner(
            f"ACT {'1' if day == 0 else '2'} — virtual day {day}",
            "the agent's first look at the world" if day == 0
            else "no announcements, no expiry notices — only what pages still list",
        )
        print_tick(fsm.tick(EPOCH + timedelta(days=day)), fsm, EPOCH)

    # ---- Act 3: every sensor dark ---------------------------------------- #
    banner(
        "ACT 3 — virtual day 15, every source fails",
        "POLL_SOURCES -> DECAY. The agent has no new information at all and its "
        "beliefs still change,\nbecause it is reasoning about a world it cannot "
        "currently observe. A reflex agent has\nnothing to do here.",
    )
    fsm.sources = [DeadSource(s.source_id) for s in live_sources]
    print_tick(fsm.tick(EPOCH + timedelta(days=15)), fsm, EPOCH)

    # ---- Act 4: the correction step -------------------------------------- #
    fsm.sources = live_sources
    fsm.refinement_enabled = True
    banner(
        "ACT 4 — the correction step, with refinement ON",
        "The user pasted DEMO_RUMOUR into Roblox by hand and it was rejected.\n"
        "This is the agent's ONLY true sensor reading. Watch two things:\n"
        "  1. the belief about DEMO_RUMOUR snaps to truth\n"
        "  2. DEMO_HEARSAY — a different code, from the same source — falls too",
    )
    before = {r.code: r.confidence for r in store.active_records("slayers2")}
    result = fsm.report_outcome("DEMO_RUMOUR", VerificationOutcome.DEAD,
                                EPOCH + timedelta(days=15))
    print_tick(result, fsm, EPOCH)

    print()
    print("  effect on codes the user said nothing about:")
    for record, breakdown in result.ranked:
        was = before.get(record.code)
        if was is not None and abs(was - breakdown.confidence) > 1e-9:
            print(f"    {record.code:<18} {was:.2f} -> {breakdown.confidence:.2f}")

    banner("BELIEF AFTER THE RUN")
    print(f"  source trust: {store.trust_map()}")
    print("  (example_community was penalised for vouching for a dead code)")
    print()
    for record in sorted(store.all_records("slayers2"), key=lambda r: -r.confidence):
        flag = "retired" if record.retired else record.status.value
        print(f"    {record.code:<18} {record.confidence:.2f}  {flag:<10} "
              f"{record.corroboration_count} source(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
