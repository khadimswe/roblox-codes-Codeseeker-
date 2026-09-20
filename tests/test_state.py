"""
Tests for the belief store: the factored representation, and persistence.

Persistence matters more than it looks.  A model-based agent is supposed to
remember what it has concluded; an agent that forgot its beliefs every launch
would re-learn each code as brand new and present a dead one as fresh.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.state import (
    BeliefStore,
    CodeKind,
    CodeStatus,
    VerificationOutcome,
    normalize_code,
)

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestFactoredRepresentation:
    def test_records_share_attributes_and_differ_on_others(self):
        """
        The property that makes a factored representation easier to reason over
        than an atomic one: two codes agree on age and differ on corroboration,
        so the agent can compare them on one axis at a time.
        """
        store = BeliefStore()
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        store.upsert_sighting("slayers2", "DEMO_B", "wiki", EPOCH)
        store.upsert_sighting("slayers2", "DEMO_B", "community", EPOCH)

        a = store.get("slayers2", "DEMO_A")
        b = store.get("slayers2", "DEMO_B")
        assert a.age_days(EPOCH) == b.age_days(EPOCH)
        assert a.corroboration_count == 1 and b.corroboration_count == 2

    def test_the_same_source_listing_twice_is_one_witness(self):
        store = BeliefStore()
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        record, is_new = store.upsert_sighting(
            "slayers2", "DEMO_A", "wiki", EPOCH + timedelta(days=1)
        )
        assert is_new is False
        assert record.corroboration_count == 1
        assert record.sources["wiki"].first_seen == EPOCH
        assert record.sources["wiki"].last_seen == EPOCH + timedelta(days=1)

    def test_corroboration_tracks_the_most_recent_listing_by_any_source(self):
        store = BeliefStore()
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        store.upsert_sighting("slayers2", "DEMO_A", "community", EPOCH + timedelta(days=4))
        record = store.get("slayers2", "DEMO_A")
        assert record.last_corroborated == EPOCH + timedelta(days=4)
        assert record.first_seen == EPOCH

    def test_later_sightings_fill_in_missing_metadata(self):
        store = BeliefStore()
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH, claimed_reward="")
        store.upsert_sighting(
            "slayers2", "DEMO_A", "community", EPOCH,
            claimed_reward="500 Gems", kind=CodeKind.MILESTONE,
        )
        record = store.get("slayers2", "DEMO_A")
        assert record.claimed_reward == "500 Gems"
        assert record.kind is CodeKind.MILESTONE

    def test_games_are_kept_apart(self):
        store = BeliefStore()
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        store.upsert_sighting("other_game", "DEMO_A", "wiki", EPOCH)
        assert len(store.all_records("slayers2")) == 1
        assert len(store.all_records()) == 2


class TestRetirement:
    def test_retire_keeps_the_record(self):
        """PURGE drops a code from the list; deleting it would be a bug."""
        store = BeliefStore()
        record, _ = store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        store.retire(record)
        assert record not in store.active_records("slayers2")
        assert record in store.all_records("slayers2")

    def test_a_re_listed_code_comes_back(self):
        store = BeliefStore()
        record, _ = store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        store.retire(record)
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH + timedelta(days=1))
        assert record.retired is False

    def test_but_not_one_the_user_watched_fail(self):
        """No amount of third-party listing outweighs one first-hand observation."""
        store = BeliefStore()
        record, _ = store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        store.record_verification("slayers2", "DEMO_A", VerificationOutcome.DEAD, EPOCH)
        store.retire(record)
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH + timedelta(days=1))
        assert record.retired is True


class TestTrust:
    def test_seed_trust_does_not_clobber_a_learned_value(self):
        """
        Refinement is the agent learning something.  Re-reading config at startup
        must not throw that away.
        """
        store = BeliefStore(default_trust=0.6)
        store.set_trust("wiki", 0.31)
        store.seed_trust("wiki", 0.7)
        assert store.trust_for("wiki") == pytest.approx(0.31)

    def test_unknown_sources_get_the_default(self):
        assert BeliefStore(default_trust=0.6).trust_for("never_seen") == 0.6

    def test_trust_is_clamped(self):
        store = BeliefStore()
        store.set_trust("wiki", 5.0)
        store.set_trust("community", -3.0)
        assert store.trust_for("wiki") == 1.0
        assert store.trust_for("community") == 0.0


class TestPersistence:
    def test_round_trip_preserves_the_whole_belief(self, tmp_path):
        store = BeliefStore(default_trust=0.6)
        store.seed_trust("wiki", 0.7)
        store.upsert_sighting(
            "slayers2", "DEMO_A", "wiki", EPOCH,
            claimed_reward="500 Gems", kind=CodeKind.MILESTONE,
        )
        store.upsert_sighting("slayers2", "DEMO_A", "community", EPOCH + timedelta(days=2))
        store.record_verification("slayers2", "DEMO_A", VerificationOutcome.WORKING, EPOCH)
        record = store.get("slayers2", "DEMO_A")
        record.confidence = 0.87
        record.status = CodeStatus.ACTIVE

        path = tmp_path / "belief.json"
        store.save(path)
        reloaded = BeliefStore.load(path)

        restored = reloaded.get("slayers2", "DEMO_A")
        assert restored.confidence == pytest.approx(0.87)
        assert restored.status is CodeStatus.ACTIVE
        assert restored.kind is CodeKind.MILESTONE
        assert restored.corroboration_count == 2
        assert restored.last_corroborated == EPOCH + timedelta(days=2)
        assert restored.last_verified.outcome is VerificationOutcome.WORKING
        assert reloaded.trust_for("wiki") == pytest.approx(0.7)

    def test_timestamps_survive_as_timezone_aware(self, tmp_path):
        """
        One naive datetime leaking in would raise TypeError somewhere far from
        its origin, because the agent does arithmetic on timestamps constantly.
        """
        store = BeliefStore()
        store.upsert_sighting("slayers2", "DEMO_A", "wiki", EPOCH)
        path = tmp_path / "belief.json"
        store.save(path)
        restored = BeliefStore.load(path).get("slayers2", "DEMO_A")
        assert restored.first_seen.tzinfo is not None
        assert restored.age_days(EPOCH + timedelta(days=1)) == pytest.approx(1.0)

    @pytest.mark.parametrize("content", ["", "{", "null", '{"records": "nope"}'])
    def test_a_corrupt_belief_file_starts_blank_instead_of_crashing(self, tmp_path, content):
        """
        Losing belief is survivable — the agent simply starts uncertain again,
        which is the right behaviour when it cannot trust its own memory.
        Crashing on a bad file is not survivable.
        """
        path = tmp_path / "belief.json"
        path.write_text(content, encoding="utf-8")
        store = BeliefStore.load(path)
        assert len(store) == 0

    def test_a_missing_file_starts_blank(self, tmp_path):
        assert len(BeliefStore.load(tmp_path / "nope.json")) == 0

    def test_an_old_schema_is_discarded(self, tmp_path):
        path = tmp_path / "belief.json"
        path.write_text('{"schema_version": 0, "records": []}', encoding="utf-8")
        assert len(BeliefStore.load(path)) == 0


class TestNormalizeCode:
    @pytest.mark.parametrize("raw,expected", [
        ("demo_hello", "DEMO_HELLO"),
        ("  DEMO_A  ", "DEMO_A"),
        ('"DEMO_B"', "DEMO_B"),
        ("SL2-100K", "SL2-100K"),
    ])
    def test_accepts_code_shaped_strings(self, raw, expected):
        assert normalize_code(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", "ab", "Click Here", "CLICK", "SUBSCRIBE", "two words", "!!!",
    ])
    def test_rejects_everything_else(self, raw):
        assert normalize_code(raw) is None

    def test_whitespace_is_not_collapsed_into_a_fake_code(self):
        """
        Collapsing would turn "Click Here" into the plausible-looking code
        "CLICKHERE" — exactly the kind of invented string CLAUDE.md forbids.
        """
        assert normalize_code("Click Here") is None
        assert normalize_code("Free Robux Now") is None
