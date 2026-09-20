"""
Tests for the control loop.

These check that the machine in `agent/fsm.py` is the machine specified in
`docs/FSM.md` — the same states, the same edges — and in particular that the two
edges carrying the argument actually fire:

  * POLL_SOURCES -> DECAY when every source fails (beliefs update with no input)
  * VERIFY entering from outside the loop (belief meets ground truth)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.fsm import AgentFSM, State
from agent.model import ConfidenceParams
from agent.state import BeliefStore, CodeStatus, VerificationOutcome
from sources.base import RawListing, Source, SourceError

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
PARAMS = ConfidenceParams()


class ScriptedSource(Source):
    """A source that returns whatever the test tells it to."""

    def __init__(self, source_id: str, codes: list[str], fail: bool = False):
        self.source_id = source_id
        self.display_name = source_id
        self.codes = codes
        self.fail = fail
        self.poll_count = 0

    def poll(self, game: str, now: datetime) -> list[RawListing]:
        self.poll_count += 1
        if self.fail:
            raise SourceError("scripted failure")
        return [
            RawListing(source_id=self.source_id, code=code, observed_at=now)
            for code in self.codes
        ]


def build(sources, refinement=False, trust=None):
    store = BeliefStore(source_trust=trust or {}, default_trust=0.6)
    return AgentFSM(
        store=store, sources=sources, params=PARAMS, game="slayers2",
        refinement_enabled=refinement,
    )


def path(result) -> list[tuple[State, State]]:
    return [(t.from_state, t.to_state) for t in result.transitions]


# --------------------------------------------------------------------------- #

class TestMainCycle:
    def test_happy_path_walks_the_specified_states(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A", "DEMO_B"])])
        result = fsm.tick(EPOCH)

        assert path(result) == [
            (State.IDLE, State.POLL_SOURCES),
            (State.POLL_SOURCES, State.EXTRACT),
            (State.EXTRACT, State.RECONCILE),
            (State.RECONCILE, State.DECAY),
            (State.DECAY, State.RANK),
            (State.RANK, State.PRESENT),
            (State.PRESENT, State.IDLE),
        ]
        assert fsm.state is State.IDLE          # the cycle closes through IDLE
        assert sorted(result.new_codes) == ["DEMO_A", "DEMO_B"]

    def test_second_tick_reports_corroboration_not_new_codes(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        fsm.tick(EPOCH)
        result = fsm.tick(EPOCH + timedelta(days=1))
        assert result.new_codes == []
        assert "0 new code(s), 1 corroboration(s) refreshed" in result.transitions[3].reason

    def test_every_transition_carries_a_human_readable_reason(self):
        """CLAUDE.md: the transition log is a feature, shown in the GUI and the video."""
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        result = fsm.tick(EPOCH)
        for transition in result.transitions:
            assert transition.reason.strip()
            assert len(transition.reason) > 10
            assert "->" in transition.format(0.0)


class TestFailureIsNotAppFailure:
    def test_all_sources_failed_still_reaches_decay(self):
        """
        The single clearest illustration that the agent models a world it cannot
        observe: every sensor is dark and the beliefs still move.
        """
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        fsm.tick(EPOCH)                                   # learn DEMO_A
        before = fsm.store.get("slayers2", "DEMO_A").confidence

        fsm.sources = [ScriptedSource("wiki", [], fail=True)]
        result = fsm.tick(EPOCH + timedelta(days=7))

        assert (State.POLL_SOURCES, State.DECAY) in path(result)
        assert (State.POLL_SOURCES, State.EXTRACT) not in path(result)
        assert fsm.store.get("slayers2", "DEMO_A").confidence < before
        assert result.warnings

    def test_one_source_failing_does_not_stop_the_others(self):
        fsm = build([
            ScriptedSource("wiki", ["DEMO_A"]),
            ScriptedSource("broken", [], fail=True),
        ])
        result = fsm.tick(EPOCH)
        assert result.new_codes == ["DEMO_A"]
        assert len(result.warnings) == 1
        assert (State.POLL_SOURCES, State.EXTRACT) in path(result)

    def test_an_exploding_parser_is_caught(self):
        """The realistic failure is a layout change raising deep inside a parser."""
        class Exploding(Source):
            source_id = "exploding"

            def poll(self, game, now):
                raise AttributeError("'NoneType' object has no attribute 'find_all'")

        fsm = build([Exploding(), ScriptedSource("wiki", ["DEMO_A"])])
        result = fsm.tick(EPOCH)
        assert result.new_codes == ["DEMO_A"]
        assert any("AttributeError" in w for w in result.warnings)

    def test_a_crashing_presenter_does_not_crash_the_agent(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        fsm.presenter = lambda result: (_ for _ in ()).throw(RuntimeError("widget gone"))
        result = fsm.tick(EPOCH)
        assert any("presenter failed" in w for w in result.warnings)
        assert fsm.state is State.IDLE


class TestCorrectionStep:
    def test_verify_enters_from_outside_the_loop_and_returns_through_rank(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        fsm.tick(EPOCH)
        result = fsm.report_outcome("DEMO_A", VerificationOutcome.WORKING, EPOCH)

        assert path(result)[0] == (State.IDLE, State.VERIFY)
        assert (State.VERIFY, State.RANK) in path(result)
        assert fsm.store.get("slayers2", "DEMO_A").confidence == pytest.approx(1.0)

    def test_reporting_dead_snaps_belief_and_purges(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A", "DEMO_B"])])
        fsm.tick(EPOCH)
        result = fsm.report_outcome("DEMO_A", VerificationOutcome.DEAD, EPOCH)

        record = fsm.store.get("slayers2", "DEMO_A")
        assert record.confidence == 0.0
        assert record.status is CodeStatus.DEAD
        assert record.retired is True
        assert "DEMO_A" in result.purged
        # Kept in the record, dropped from the list — not deleted.
        assert record in fsm.store.all_records("slayers2")
        assert record not in fsm.store.active_records("slayers2")

    def test_a_verified_dead_code_is_not_resurrected_by_further_listings(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        fsm.tick(EPOCH)
        fsm.report_outcome("DEMO_A", VerificationOutcome.DEAD, EPOCH)
        fsm.tick(EPOCH + timedelta(days=1))          # wiki still lists it

        record = fsm.store.get("slayers2", "DEMO_A")
        assert record.retired is True
        assert record.confidence == 0.0

    def test_verifying_an_unknown_code_is_a_no_op_not_a_crash(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        fsm.tick(EPOCH)
        result = fsm.report_outcome("DEMO_NOPE", VerificationOutcome.DEAD, EPOCH)
        assert fsm.state is State.IDLE
        assert result.ranked == []


class TestRefinementToggle:
    def test_refinement_off_leaves_source_trust_alone(self):
        """ABOUT.md's demo beat: show the pure model-based agent first."""
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])], refinement=False,
                    trust={"wiki": 0.7})
        fsm.tick(EPOCH)
        fsm.report_outcome("DEMO_A", VerificationOutcome.DEAD, EPOCH)
        assert fsm.store.trust_for("wiki") == pytest.approx(0.7)

    def test_refinement_on_lowers_trust_and_drags_down_sibling_codes(self):
        """
        PLAN.md M3's acceptance criterion: 'mark one dead, and see other codes
        from that source lose confidence'.
        """
        fsm = build([ScriptedSource("wiki", ["DEMO_BAD", "DEMO_SIBLING"])],
                    refinement=True, trust={"wiki": 0.7})
        fsm.tick(EPOCH)
        sibling_before = fsm.store.get("slayers2", "DEMO_SIBLING").confidence

        fsm.report_outcome("DEMO_BAD", VerificationOutcome.DEAD, EPOCH)

        assert fsm.store.trust_for("wiki") < 0.7
        assert fsm.store.get("slayers2", "DEMO_SIBLING").confidence < sibling_before

    def test_refinement_is_logged_so_the_video_can_show_it(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])], refinement=True,
                    trust={"wiki": 0.7})
        fsm.tick(EPOCH)
        result = fsm.report_outcome("DEMO_A", VerificationOutcome.DEAD, EPOCH)
        reasons = " ".join(t.reason for t in result.transitions)
        assert "wiki 0.70->0.60" in reasons


class TestGameSwitchAndPurge:
    def test_selecting_a_game_re_ranks_without_polling(self):
        """The internal state earns its keep: no sensor use needed to re-answer."""
        source = ScriptedSource("wiki", ["DEMO_A"])
        fsm = build([source])
        fsm.tick(EPOCH)
        polls_after_tick = source.poll_count

        result = fsm.select_game("slayers2", EPOCH)

        assert source.poll_count == polls_after_tick
        assert path(result)[0] == (State.IDLE, State.RANK)
        assert len(result.ranked) == 1

    def test_purge_fires_only_once_a_code_is_far_below_dead(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])])
        fsm.tick(EPOCH)
        fsm.sources = [ScriptedSource("wiki", [], fail=True)]

        # Far enough out that even the baseline half-life has ground it down.
        result = fsm.tick(EPOCH + timedelta(days=40))
        assert (State.PRESENT, State.PURGE) in path(result)
        assert "DEMO_A" in result.purged

    def test_log_is_bounded(self):
        fsm = build([ScriptedSource("wiki", ["DEMO_A"])], )
        fsm._max_log_entries = 20
        for day in range(30):
            fsm.tick(EPOCH + timedelta(days=day))
        assert len(fsm.transitions) <= 20
