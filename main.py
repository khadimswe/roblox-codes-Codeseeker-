#!/usr/bin/env python3
"""
main.py — entry point.  Parses arguments and wires source -> agent -> UI.

This file is deliberately thin.  It decides *which* clock, *which* sources and
*which* display the agent gets, hands them over, and gets out of the way.  All
three of those are interfaces the agent cannot see through, which is what lets
the same belief model run against replayed snapshots with a virtual clock and
against live web pages with a real one.

Usage
-----
    python main.py                      GUI, canned source, real clock
    python main.py --demo               GUI, canned source, virtual clock  <- for the video
    python main.py --source web         GUI, live fetching (rate limited)
    python main.py --demo --headless    terminal run: transitions + ranked list
    python main.py --reset              discard the stored belief and start over
    python main.py --source web --record  save live listings as a dated snapshot
    python main.py --snapshots recorded   replay those real recorded listings
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.clock import Clock, RealClock, VirtualClock
from agent.fsm import AgentFSM, TickResult
from agent.model import ConfidenceParams
from agent.state import BeliefStore
from sources.base import Source

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "games.json"
EXAMPLE_CONFIG_PATH = ROOT / "config" / "games.example.json"
BELIEF_PATH = ROOT / "data" / "belief.json"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"

#: Where the virtual clock starts in demo mode.  A fixed date, not today's, so
#: that a recorded demo video and a fresh run show the same day numbers.
DEMO_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load_config() -> dict:
    """
    Read config/games.json, falling back to the versioned example.

    config/games.json is gitignored, so a freshly cloned repo does not have one.
    Falling back rather than exiting means a grader can clone and run
    immediately, which is worth more than insisting on the copy step.
    """
    for path in (CONFIG_PATH, EXAMPLE_CONFIG_PATH):
        if path.exists():
            if path is EXAMPLE_CONFIG_PATH:
                print(
                    f"note: {CONFIG_PATH.relative_to(ROOT)} not found, "
                    f"using {path.relative_to(ROOT)}",
                    file=sys.stderr,
                )
            return json.loads(path.read_text(encoding="utf-8"))
    raise SystemExit("error: no config found; expected config/games.json")


def build_sources(
    config: dict, game: str, mode: str, epoch: datetime, subset: str = ""
) -> list[Source]:
    """
    Construct the agent's sensors.

    The agent is handed a list of `Source` objects and cannot tell which kind it
    got.  That indifference is the point: the belief model is identical whether
    the evidence came from a replayed fixture or a live page.
    """
    if mode == "canned":
        from sources.canned import build_canned_sources

        sources = build_canned_sources(SNAPSHOT_DIR, game, epoch, subset)
        if not sources:
            where = SNAPSHOT_DIR / game / subset if subset else SNAPSHOT_DIR / game
            hint = (
                "Run: python main.py --source web --record"
                if subset == "recorded"
                else "Run: python tools/make_demo_snapshots.py"
            )
            print(f"warning: no snapshots found in {where}. {hint}", file=sys.stderr)
        return sources

    from sources.web import build_web_sources

    sources = build_web_sources(config, game, cache_dir=ROOT / "data" / "cache")
    if not sources:
        print(
            f"warning: no source in config has a url for {game!r}. "
            f"Add one to config/games.json under games.{game}.sources[].url",
            file=sys.stderr,
        )
    return sources


def build_store(config: dict, game: str, reset: bool) -> BeliefStore:
    """Load the persisted belief, or start blank, then seed source trust from config."""
    default_trust = float((config.get("source_trust") or {}).get("default", 0.6))

    if reset and BELIEF_PATH.exists():
        BELIEF_PATH.unlink()
        print(f"reset: removed {BELIEF_PATH.relative_to(ROOT)}", file=sys.stderr)

    store = BeliefStore.load(BELIEF_PATH, default_trust=default_trust)

    # `seed_trust` uses setdefault, so a value the agent has *learned* through
    # refinement survives a restart and is not clobbered by the config default.
    for source in (config.get("games", {}).get(game, {}) or {}).get("sources", []):
        store.seed_trust(source["id"], float(source.get("initial_trust", default_trust)))
    return store


def print_tick(result: TickResult, fsm: AgentFSM, epoch: datetime | None) -> None:
    """Terminal rendering of one tick: the transition log, then the ranked list."""
    for transition in result.transitions:
        elapsed = (transition.at - epoch).total_seconds() / 86400.0 if epoch else None
        print("  " + transition.format(elapsed))

    for warning in result.warnings:
        print(f"  ! {warning}")

    if not result.ranked:
        print("  (no codes known yet)")
        return

    print()
    print(f"  {'CODE':<18} {'CONF':>5}  {'STATUS':<9} {'SRC':>3}  {'AGE':>6}  REWARD")
    print("  " + "-" * 76)
    for record, breakdown in result.ranked:
        bar = "#" * int(round(breakdown.confidence * 10))
        print(
            f"  {record.code:<18} {breakdown.confidence:>5.2f}  "
            f"{breakdown.status.value:<9} {record.corroboration_count:>3}  "
            f"{breakdown.age_days:>5.1f}d  {record.claimed_reward[:32]}"
            f"  {bar}"
        )


def run_headless(fsm: AgentFSM, clock: Clock, steps: int, step_days: float) -> None:
    """
    Run the loop in the terminal with no GUI.

    This is PLAN.md's M2 acceptance check — "a headless tick loop runs the full
    cycle and prints state transitions and the ranked list" — and it is also the
    cleanest thing to screenshot for the report's output section, because the
    state machine's behaviour is visible as text rather than inferred from a
    picture of a window.
    """
    epoch = clock.start if isinstance(clock, VirtualClock) else None

    for step in range(steps):
        now = clock.now()
        label = (
            f"virtual day {clock.elapsed_days:.1f}"
            if isinstance(clock, VirtualClock)
            else now.strftime("%Y-%m-%d %H:%M:%S UTC")
        )
        print()
        print("=" * 80)
        print(f"TICK {step + 1}/{steps} — {label}")
        print("=" * 80)

        result = fsm.tick(now)
        print_tick(result, fsm, epoch)

        if isinstance(clock, VirtualClock) and step < steps - 1:
            clock.advance(days=step_days)

    print()
    print("=" * 80)
    print("BELIEF AFTER RUN")
    print("=" * 80)
    print(f"  source trust: {fsm.store.trust_map()}")
    retired = [r.code for r in fsm.store.all_records(fsm.game) if r.retired]
    print(f"  retired (kept in record, dropped from list): {retired or 'none'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="codeseeker",
        description=(
            "A model-based AI agent that tracks Roblox game codes, maintains a "
            "belief about which are probably still valid, and presents a ranked "
            "copy-ready list. It never redeems codes — you do that by hand, and "
            "your report of the result is the agent's only true sensor."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage\n-----")[-1],
    )
    parser.add_argument(
        "--source",
        choices=("canned", "web"),
        default="canned",
        help="canned: replay data/snapshots/ (default, use this for the demo). "
             "web: fetch live listings, rate limited.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="use a virtual clock so days can be fast-forwarded in seconds",
    )
    parser.add_argument(
        "--reset", action="store_true", help="discard data/belief.json and start over"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run in the terminal, printing transitions and the ranked list",
    )
    parser.add_argument("--game", default=None, help="game id from config (default: default_game)")
    parser.add_argument(
        "--steps", type=int, default=6, help="headless: how many ticks to run (default 6)"
    )
    parser.add_argument(
        "--step-days",
        type=float,
        default=3.0,
        help="headless + --demo: virtual days to advance per tick (default 3)",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        help="start with source-trust refinement ON (GUI has a toggle)",
    )
    parser.add_argument(
        "--snapshots",
        choices=("demo", "recorded"),
        default="demo",
        help="which canned fixture set to replay: 'demo' is the synthetic DEMO_ "
             "narrative (default, use this for the video), 'recorded' is real "
             "listings captured by --record",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="--source web only: save fetched listings to data/snapshots/ as a "
             "dated RECORDED fixture, then exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    game = args.game or config.get("default_game", "slayers2")

    if game not in config.get("games", {}):
        print(f"error: unknown game {game!r}; known: "
              f"{', '.join(config.get('games', {}))}", file=sys.stderr)
        return 2

    clock: Clock = VirtualClock(DEMO_EPOCH) if args.demo else RealClock()
    epoch = DEMO_EPOCH if args.demo else None
    subset = "recorded" if args.snapshots == "recorded" else ""
    sources = build_sources(config, game, args.source, epoch or DEMO_EPOCH, subset)

    if args.record:
        if args.source != "web":
            print("error: --record requires --source web", file=sys.stderr)
            return 2
        from sources.web import record_snapshot

        return record_snapshot(sources, game, SNAPSHOT_DIR, clock.now())

    store = build_store(config, game, args.reset)
    params = ConfidenceParams.from_config(config)
    fsm = AgentFSM(
        store=store,
        sources=sources,
        params=params,
        game=game,
        refinement_enabled=args.refine,
    )

    if args.headless:
        run_headless(fsm, clock, args.steps, args.step_days)
        store.save(BELIEF_PATH)
        return 0

    from ui.app import launch

    launch(
        fsm=fsm,
        clock=clock,
        config=config,
        belief_path=BELIEF_PATH,
        snapshot_dir=SNAPSHOT_DIR,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
