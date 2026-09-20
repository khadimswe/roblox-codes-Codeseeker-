"""
Tests for sources and percept extraction.

CLAUDE.md: "Sources get tests against fixtures, never against the live network."
Nothing in this file opens a socket; the web source is exercised against
hand-written HTML strings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.percepts import extract, infer_kind
from agent.state import CodeKind
from sources.base import RawListing, Source, SourceError, poll_safely
from sources.canned import build_canned_sources, load_snapshots, timeline_offsets

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
SNAPSHOT_DIR = "data/snapshots"


class TestCannedReplay:
    def test_fixtures_load(self):
        snapshots = load_snapshots(SNAPSHOT_DIR, "slayers2")
        assert snapshots, "run: python tools/make_demo_snapshots.py"
        assert timeline_offsets(SNAPSHOT_DIR, "slayers2") == [0.0, 3.0, 7.0, 12.0]

    @pytest.mark.parametrize("subset", ["", "recorded"])
    def test_every_fixture_code_is_recorded_or_obviously_synthetic(self, subset):
        """
        CLAUDE.md hard constraint 3, as a test.  A fixture code must either be
        genuinely recorded with a capture date, or be prefixed DEMO_.  Inventing
        plausible Slayers 2 codes would produce strings a grader could not
        distinguish from fabricated results.

        Both fixture sets are checked: the synthetic demo narrative, and the
        real listings captured by `--source web --record`.
        """
        for snapshot in load_snapshots(SNAPSHOT_DIR, "slayers2", subset):
            if snapshot.provenance == "RECORDED":
                # A recorded fixture earns its real codes by saying exactly
                # where and when they came from.
                assert snapshot.capture_note
                assert "RECORDED" in snapshot.capture_note
                continue
            for listing in snapshot.listings:
                assert listing.code.startswith("DEMO_"), (
                    f"{listing.code} is neither recorded nor DEMO_-prefixed"
                )
            assert snapshot.capture_note, "fixtures must state their provenance"

    def test_recorded_fixtures_carry_a_real_capture_date(self):
        """
        The capture date is what makes a real code in a fixture honest rather
        than fabricated: it says 'this was published on this day', not 'this
        works'.  It is also what `load_snapshots` derives replay offsets from
        when several days have been captured.
        """
        recorded = load_snapshots(SNAPSHOT_DIR, "slayers2", "recorded")
        if not recorded:
            pytest.skip("no recorded snapshots; run: main.py --source web --record")
        for snapshot in recorded:
            assert snapshot.provenance == "RECORDED"
            assert snapshot.offset_days is not None

    def test_the_two_fixture_sets_are_kept_apart(self):
        """
        The demo narrative must not be diluted by real codes, so that it is
        never ambiguous which codes in a screenshot are synthetic.
        """
        demo_codes = {
            l.code for s in load_snapshots(SNAPSHOT_DIR, "slayers2") for l in s.listings
        }
        assert demo_codes, "run: python tools/make_demo_snapshots.py"
        assert all(c.startswith("DEMO_") for c in demo_codes)

    def test_poll_returns_the_most_recent_wave_at_or_before_now(self):
        sources = {s.source_id: s for s in
                   build_canned_sources(SNAPSHOT_DIR, "slayers2", EPOCH)}
        wiki = sources["example_wiki"]

        day1 = [l.code for l in wiki.poll("slayers2", EPOCH + timedelta(days=1))]
        day0 = [l.code for l in wiki.poll("slayers2", EPOCH)]
        assert day1 == day0, "a page shows what it last showed until it changes"

        day3 = [l.code for l in wiki.poll("slayers2", EPOCH + timedelta(days=3))]
        assert "DEMO_STALEONE" in day0 and "DEMO_STALEONE" not in day3

    def test_a_code_dropping_off_a_page_is_how_decay_gets_its_evidence(self):
        """
        The replay's whole job: some codes keep being listed and hold their
        confidence, others quietly vanish.  The agent is told neither fact.
        """
        sources = build_canned_sources(SNAPSHOT_DIR, "slayers2", EPOCH)
        late = {
            l.code
            for s in sources
            for l in s.poll("slayers2", EPOCH + timedelta(days=12))
        }
        assert "DEMO_LAUNCH100K" in late
        assert "DEMO_WEEKENDX2" not in late

    def test_before_the_first_wave_the_source_reports_failure_not_emptiness(self):
        """
        Silence is not absence.  An empty list would tell the agent every code
        it knows has been removed, which is a far stronger claim than 'I could
        not read the page'.
        """
        source = build_canned_sources(SNAPSHOT_DIR, "slayers2", EPOCH)[0]
        with pytest.raises(SourceError):
            source.poll("slayers2", EPOCH - timedelta(days=1))

    def test_listings_are_stamped_with_observation_time_not_capture_time(self):
        source = build_canned_sources(SNAPSHOT_DIR, "slayers2", EPOCH)[0]
        now = EPOCH + timedelta(days=5)
        assert all(l.observed_at == now for l in source.poll("slayers2", now))

    def test_unknown_game_yields_no_sources_rather_than_raising(self):
        assert build_canned_sources(SNAPSHOT_DIR, "no_such_game", EPOCH) == []


class TestPollSafely:
    def test_never_raises(self):
        class Bad(Source):
            source_id = "bad"

            def poll(self, game, now):
                raise KeyError("listings")

        outcome = poll_safely(Bad(), "slayers2", EPOCH)
        assert outcome.ok is False
        assert "KeyError" in outcome.error
        assert outcome.listings == []


class TestExtract:
    def test_case_and_quoting_variants_collapse_to_one_code(self):
        """
        Without normalisation, two sources spelling a code differently look like
        two codes and corroboration never fires.
        """
        batch = extract(
            [
                RawListing("wiki", "demo_alpha"),
                RawListing("community", '"DEMO_ALPHA"'),
                RawListing("video", " Demo_Alpha "),
            ],
            "slayers2",
            EPOCH,
        )
        assert {p.code for p in batch.percepts} == {"DEMO_ALPHA"}
        assert len(batch.percepts) == 3
        assert batch.source_ids == {"wiki", "community", "video"}

    def test_page_furniture_is_rejected_not_passed_through(self):
        batch = extract(
            [RawListing("wiki", x) for x in
             ["Click Here", "SUBSCRIBE", "ab", "", "DEMO_REAL"]],
            "slayers2",
            EPOCH,
        )
        assert [p.code for p in batch.percepts] == ["DEMO_REAL"]
        assert len(batch.rejected) == 4

    def test_a_source_repeating_itself_is_still_one_witness(self):
        batch = extract(
            [RawListing("wiki", "DEMO_A"), RawListing("wiki", "DEMO_A")],
            "slayers2",
            EPOCH,
        )
        assert len(batch.percepts) == 1

    def test_two_sources_agreeing_are_two_witnesses(self):
        batch = extract(
            [RawListing("wiki", "DEMO_A"), RawListing("community", "DEMO_A")],
            "slayers2",
            EPOCH,
        )
        assert len(batch.percepts) == 2


class TestKindInference:
    @pytest.mark.parametrize("text,expected", [
        ("1,000 Spins — 100K likes milestone reward", CodeKind.MILESTONE),
        ("2x XP — weekend event, limited time", CodeKind.EVENT),
        ("Thank you for 50M visits", CodeKind.MILESTONE),
        ("Expires soon!", CodeKind.EVENT),
        ("500 Gems", CodeKind.UNKNOWN),
        ("", CodeKind.UNKNOWN),
    ])
    def test_inference(self, text, expected):
        assert infer_kind(text) is expected

    def test_milestone_wins_over_event_wording(self):
        """
        '100K likes — limited time' is a milestone code with marketing on it.
        Reading it as an event code would make the agent give up far too early.
        """
        assert infer_kind("100K likes reward — limited time!") is CodeKind.MILESTONE
