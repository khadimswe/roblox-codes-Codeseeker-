"""
Unit tests for the belief model.

PLAN.md sets the bar for this file: "A code unseen for two weeks should decay
well below a fresh one; a code in three sources should outrank the same-age code
in one."  CLAUDE.md adds: decay behaves monotonically, corroboration raises
score, verification overrides both.

Everything here runs off a fake clock.  No I/O, no network, no real time.
"""

from __future__ import annotations

from datetime import timezone

import pytest

from agent import model
from agent.state import CodeKind, CodeStatus, VerificationOutcome
from tests.conftest import make_record

PARAMS = model.ConfidenceParams()
TRUST = {"example_wiki": 0.7, "example_community": 0.5, "example_video": 0.6}


# --------------------------------------------------------------------------- #
# Component 1 — how the world evolves
# --------------------------------------------------------------------------- #

class TestDecay:
    def test_half_life_is_exact(self):
        assert model.decay_factor(0, 7) == pytest.approx(1.0)
        assert model.decay_factor(7, 7) == pytest.approx(0.5)
        assert model.decay_factor(14, 7) == pytest.approx(0.25)
        assert model.decay_factor(21, 7) == pytest.approx(0.125)

    def test_decay_is_monotonically_decreasing(self):
        """Confidence must never rise with age alone.  This is the core guarantee."""
        previous = 1.1
        for age_days in range(0, 60):
            current = model.decay_factor(age_days, 7)
            assert current < previous
            previous = current

    def test_decay_never_reaches_zero(self):
        """
        The agent grows unsure but never becomes *certain* a code is dead without
        observing it.  Only a user report can produce certainty.
        """
        assert model.decay_factor(3650, 7) > 0.0

    def test_two_week_old_code_decays_well_below_a_fresh_one(self, clock):
        """PLAN.md's acceptance criterion for M1, stated literally."""
        fresh = make_record("DEMO_FRESH", seen_at=clock.now)
        stale = make_record("DEMO_STALE", seen_at=clock.now)
        later = clock.advance(days=14)

        fresh.last_corroborated = later          # fresh: re-listed today
        fresh_score = model.score(fresh, later, PARAMS, TRUST)
        stale_score = model.score(stale, later, PARAMS, TRUST)

        assert stale_score < fresh_score / 3
        assert stale_score == pytest.approx(fresh_score * 0.25, rel=1e-6)

    def test_event_codes_decay_faster_than_milestone_codes(self, clock):
        """
        The 'how the world evolves' component is non-uniform, per ABOUT.md.
        Same age, same sources, different predicted lifetime.
        """
        event = make_record("DEMO_EVENT", seen_at=clock.now, kind=CodeKind.EVENT)
        milestone = make_record("DEMO_MILE", seen_at=clock.now, kind=CodeKind.MILESTONE)
        later = clock.advance(days=7)

        assert model.score(event, later, PARAMS, TRUST) < model.score(
            milestone, later, PARAMS, TRUST
        )

    def test_decay_measures_time_since_corroboration_not_discovery(self, clock):
        """
        A code first seen long ago but still being re-listed today is current.
        Decaying on discovery age would punish long-lived milestone codes.
        """
        record = make_record("DEMO_OLDBUTLISTED", seen_at=clock.now)
        later = clock.advance(days=30)
        record.last_corroborated = later     # still on the page today

        assert model.score(record, later, PARAMS, TRUST) == pytest.approx(
            model.evidence_value(record, TRUST, PARAMS)
        )


# --------------------------------------------------------------------------- #
# Component 2 — what the agent's actions do
# --------------------------------------------------------------------------- #

class TestCorroboration:
    def test_three_sources_outrank_one_at_the_same_age(self, clock):
        """PLAN.md's second acceptance criterion for M1, stated literally."""
        one = make_record("DEMO_ONE", sources=["example_wiki"], seen_at=clock.now)
        three = make_record(
            "DEMO_THREE",
            sources=["example_wiki", "example_community", "example_video"],
            seen_at=clock.now,
        )
        later = clock.advance(days=5)

        assert model.score(three, later, PARAMS, TRUST) > model.score(one, later, PARAMS, TRUST)

    def test_corroboration_bonus_grows_then_caps(self):
        """Ten sites that copy each other are not ten independent observations."""
        bonuses = [
            model.corroborate(
                make_record(sources=[f"s{i}" for i in range(n)]), PARAMS
            )
            for n in range(1, 8)
        ]
        assert bonuses[0] == 0.0                       # one source: no corroboration
        assert bonuses == sorted(bonuses)              # never decreases
        assert max(bonuses) == pytest.approx(PARAMS.max_corroboration_bonus)

    def test_source_prior_uses_trust(self):
        """A code on a trusted site beats the same code on an untrusted one."""
        trusted = make_record(sources=["example_wiki"])       # 0.7
        untrusted = make_record(sources=["example_community"])  # 0.5
        assert model.source_prior(trusted, TRUST) > model.source_prior(untrusted, TRUST)

    def test_corroboration_raises_score_without_changing_age(self, clock):
        """Adding a witness raises belief immediately, holding time constant."""
        record = make_record(sources=["example_wiki"], seen_at=clock.now)
        before = model.score(record, clock.now, PARAMS, TRUST)

        from agent.state import SourceSighting
        record.sources["example_community"] = SourceSighting(
            "example_community", clock.now, clock.now
        )
        after = model.score(record, clock.now, PARAMS, TRUST)

        assert after > before


# --------------------------------------------------------------------------- #
# The correction step
# --------------------------------------------------------------------------- #

class TestVerification:
    def test_verification_dead_overrides_decay_and_corroboration(self, clock):
        """
        CLAUDE.md: 'verification overrides both'.  A heavily corroborated,
        brand-new code still goes to zero if the user watched it fail.
        """
        record = make_record(
            sources=["example_wiki", "example_community", "example_video"],
            seen_at=clock.now,
        )
        assert model.score(record, clock.now, PARAMS, TRUST) > 0.8

        corrected = model.apply_verification(
            record, VerificationOutcome.DEAD, clock.now, PARAMS
        )
        assert model.score(corrected, clock.now, PARAMS, TRUST) == 0.0
        assert corrected.status is CodeStatus.DEAD

    def test_verification_working_overrides_a_decayed_belief(self, clock):
        """A code the model had written off snaps back to 1.0 when it works."""
        record = make_record(sources=["example_community"], seen_at=clock.now)
        later = clock.advance(days=21)
        assert model.score(record, later, PARAMS, TRUST) < PARAMS.dead_threshold

        corrected = model.apply_verification(
            record, VerificationOutcome.WORKING, later, PARAMS
        )
        assert model.score(corrected, later, PARAMS, TRUST) == pytest.approx(1.0)
        assert corrected.status is CodeStatus.ACTIVE

    def test_dead_is_absorbing_even_if_sources_keep_listing_it(self, clock):
        """
        First-hand evidence of failure is not undone by third-party claims.
        Without this, a stubborn wiki could talk the agent out of what the user
        personally saw.
        """
        record = make_record(sources=["example_wiki"], seen_at=clock.now)
        corrected = model.apply_verification(
            record, VerificationOutcome.DEAD, clock.now, PARAMS
        )
        later = clock.advance(days=1)
        corrected.last_corroborated = later
        corrected.sources["example_community"] = __import__(
            "agent.state", fromlist=["SourceSighting"]
        ).SourceSighting("example_community", later, later)

        assert model.score(corrected, later, PARAMS, TRUST) == 0.0

    def test_verified_working_still_decays_afterwards(self, clock):
        """
        Verification is a reading, not a permanent guarantee.  The world keeps
        evolving after the user's report, so the belief must keep falling.
        """
        record = make_record(seen_at=clock.now)
        corrected = model.apply_verification(
            record, VerificationOutcome.WORKING, clock.now, PARAMS
        )
        at_verification = model.score(corrected, clock.now, PARAMS, TRUST)
        a_week_later = model.score(corrected, clock.advance(days=7), PARAMS, TRUST)

        assert at_verification == pytest.approx(1.0)
        assert a_week_later == pytest.approx(0.5, abs=0.01)

    def test_apply_verification_does_not_mutate_its_argument(self, clock):
        """model.py is pure; the FSM writes the returned record back to the store."""
        record = make_record(seen_at=clock.now)
        model.apply_verification(record, VerificationOutcome.DEAD, clock.now, PARAMS)
        assert record.last_verified is None
        assert record.status is CodeStatus.UNVERIFIED


# --------------------------------------------------------------------------- #
# Model refinement (the optional learning beat)
# --------------------------------------------------------------------------- #

class TestSourceTrustRefinement:
    def test_dead_report_lowers_trust_in_every_listing_source(self, clock):
        record = make_record(sources=["example_wiki", "example_community"], seen_at=clock.now)
        updated = model.refine_source_trust(TRUST, record, VerificationOutcome.DEAD, PARAMS)

        assert updated["example_wiki"] < TRUST["example_wiki"]
        assert updated["example_community"] < TRUST["example_community"]
        assert updated["example_video"] == TRUST["example_video"]   # uninvolved: untouched

    def test_working_report_raises_trust(self, clock):
        record = make_record(sources=["example_community"], seen_at=clock.now)
        updated = model.refine_source_trust(TRUST, record, VerificationOutcome.WORKING, PARAMS)
        assert updated["example_community"] > TRUST["example_community"]

    def test_penalty_outweighs_reward(self, clock):
        """A dead code is stronger evidence about a source than a working one."""
        assert PARAMS.trust_penalty_on_dead > PARAMS.trust_reward_on_working

    def test_trust_stays_within_bounds(self, clock):
        record = make_record(sources=["example_wiki"], seen_at=clock.now)
        trust = dict(TRUST)
        for _ in range(50):
            trust = model.refine_source_trust(trust, record, VerificationOutcome.DEAD, PARAMS)
        assert trust["example_wiki"] == pytest.approx(PARAMS.trust_floor)

        for _ in range(100):
            trust = model.refine_source_trust(trust, record, VerificationOutcome.WORKING, PARAMS)
        assert trust["example_wiki"] == pytest.approx(PARAMS.trust_ceiling)

    def test_refinement_does_not_mutate_its_argument(self, clock):
        record = make_record(sources=["example_wiki"], seen_at=clock.now)
        original = dict(TRUST)
        model.refine_source_trust(TRUST, record, VerificationOutcome.DEAD, PARAMS)
        assert TRUST == original

    def test_losing_trust_in_a_source_lowers_its_other_codes(self, clock):
        """
        The demo beat from PLAN.md M3: mark one code dead, watch *other* codes
        from that source lose confidence.  This is that effect, at model level.
        """
        sibling = make_record("DEMO_SIBLING", sources=["example_wiki"], seen_at=clock.now)
        before = model.score(sibling, clock.now, PARAMS, TRUST)

        bad_code = make_record("DEMO_BAD", sources=["example_wiki"], seen_at=clock.now)
        worse_trust = model.refine_source_trust(
            TRUST, bad_code, VerificationOutcome.DEAD, PARAMS
        )
        after = model.score(sibling, clock.now, PARAMS, worse_trust)

        assert after < before


# --------------------------------------------------------------------------- #
# Belief -> decision
# --------------------------------------------------------------------------- #

class TestClassifyAndRank:
    def test_status_bands(self):
        assert model.classify(0.90, PARAMS) is CodeStatus.ACTIVE
        assert model.classify(PARAMS.suspect_threshold, PARAMS) is CodeStatus.ACTIVE
        assert model.classify(0.30, PARAMS) is CodeStatus.SUSPECT
        assert model.classify(PARAMS.dead_threshold, PARAMS) is CodeStatus.SUSPECT
        assert model.classify(0.02, PARAMS) is CodeStatus.DEAD

    def test_a_code_walks_green_to_amber_to_grey_with_no_new_information(self, clock):
        """
        The central claim of the project, as a test: with zero percepts arriving,
        the agent's belief still changes, because it models a world it cannot see.
        """
        record = make_record(sources=["example_wiki"], seen_at=clock.now)
        seen = []
        for _ in range(6):
            seen.append(model.explain(record, clock.now, PARAMS, TRUST).status)
            clock.advance(days=4)

        assert seen[0] is CodeStatus.ACTIVE
        assert CodeStatus.SUSPECT in seen
        assert seen[-1] is CodeStatus.DEAD

    def test_ranking_is_best_first_and_stable(self, clock):
        strong = make_record(
            "DEMO_STRONG",
            sources=["example_wiki", "example_community", "example_video"],
            seen_at=clock.now,
        )
        weak = make_record("DEMO_WEAK", sources=["example_community"], seen_at=clock.now)
        ranked = model.rank_records([weak, strong], clock.now, PARAMS, TRUST)

        assert [r.code for r, _ in ranked] == ["DEMO_STRONG", "DEMO_WEAK"]
        assert ranked == model.rank_records([strong, weak], clock.now, PARAMS, TRUST)

    def test_purge_is_stricter_than_the_dead_label(self, clock):
        """
        A code must be able to sit visibly grey for a while.  If PURGE fired at
        dead_threshold, the grey state would never appear on screen.
        """
        record = make_record(seen_at=clock.now)
        just_dead = (PARAMS.dead_threshold + PARAMS.purge_threshold) / 2
        assert model.classify(just_dead, PARAMS) is CodeStatus.DEAD
        assert not model.should_purge(record, just_dead, PARAMS)
        assert model.should_purge(record, 0.001, PARAMS)

    def test_verified_dead_purges_immediately(self, clock):
        record = make_record(seen_at=clock.now)
        corrected = model.apply_verification(
            record, VerificationOutcome.DEAD, clock.now, PARAMS
        )
        assert model.should_purge(corrected, 0.0, PARAMS)


# --------------------------------------------------------------------------- #
# Legibility
# --------------------------------------------------------------------------- #

class TestExplanation:
    def test_breakdown_explains_the_number_it_reports(self, clock):
        """
        CLAUDE.md asks for confidence to be legible.  The hover text has to
        actually mention the number the bar is showing.
        """
        record = make_record(
            sources=["example_wiki", "example_community"], seen_at=clock.now
        )
        later = clock.advance(days=3)
        breakdown = model.explain(record, later, PARAMS, TRUST)

        assert f"{breakdown.confidence:.2f}" in breakdown.explanation()
        assert "2 sources" in breakdown.explanation()
        assert breakdown.status.value in breakdown.explanation()

    def test_model_module_reads_no_clock(self):
        """
        The virtual clock depends on this, so it is worth asserting rather than
        merely documenting.  Walks the parsed syntax tree rather than grepping
        the text, so that prose *about* clock reads (this module has plenty)
        doesn't trip it and an actual call can't hide behind odd formatting.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(model))
        forbidden = {"now", "utcnow", "today", "time", "monotonic"}
        offenders = [
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden
        ]
        assert offenders == [], f"model.py must stay pure; found clock reads: {offenders}"
