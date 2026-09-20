"""
Tests for the structural rules in CLAUDE.md.

These do not test behaviour.  They test that the code is *shaped* the way the
report claims it is, because the shape is the assignment:

  * ground truth and belief live in separate structures that never touch
  * the dependency arrow runs ui -> agent -> sources, never backwards
  * agent/model.py is pure

A behavioural bug costs a wrong number.  Breaking one of these quietly would
make the report's central claim untrue, which is worse and much harder to spot
by reading a screenshot.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.fsm import AgentFSM
from agent.model import ConfidenceParams
from agent.state import BeliefStore
from sources.canned import build_canned_sources, load_snapshots

ROOT = Path(__file__).resolve().parent.parent
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def imported_modules(path: Path) -> set[str]:
    """Top-level package name of every import in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


class TestDependencyDirection:
    def test_agent_never_imports_ui(self):
        """CLAUDE.md: 'agent/ must never import from ui/'."""
        for path in sorted((ROOT / "agent").glob("*.py")):
            assert "ui" not in imported_modules(path), f"{path.name} imports ui"

    def test_sources_never_imports_agent_or_ui(self):
        """
        Sources are the far side of the sensor boundary.  A source that could
        see the belief store could tailor what it reports to what the agent
        already thinks, which would make the whole demonstration circular.
        """
        for path in sorted((ROOT / "sources").glob("*.py")):
            imports = imported_modules(path)
            assert "agent" not in imports, f"{path.name} imports agent"
            assert "ui" not in imports, f"{path.name} imports ui"

    def test_model_is_the_pure_core(self):
        """model.py may know about state.py and nothing else in the project."""
        imports = imported_modules(ROOT / "agent" / "model.py")
        assert "sources" not in imports
        assert "ui" not in imports
        for network in ("requests", "urllib", "http", "socket"):
            assert network not in imports, f"model.py imports {network}"

    def test_model_touches_no_files(self):
        imports = imported_modules(ROOT / "agent" / "model.py")
        assert "json" not in imports
        assert "pathlib" not in imports
        assert "os" not in imports


class TestTruthAndBeliefAreSeparate:
    """
    CLAUDE.md hard constraint 4: "data/snapshots/ is ground truth for the canned
    source. BeliefStore is what the agent believes. They must never share
    objects or be merged for convenience. They communicate only through
    percepts."
    """

    def test_no_object_is_shared_between_snapshots_and_belief(self):
        """
        The strict reading, tested strictly: after a full run, not one object
        reachable from the belief store is the *same object* as anything the
        snapshots hold.  Sharing would mean a later belief update could reach
        back and alter the ground truth it was derived from.
        """
        sources = build_canned_sources("data/snapshots", "slayers2", EPOCH)
        store = BeliefStore(default_trust=0.6)
        fsm = AgentFSM(store, sources, ConfidenceParams(), "slayers2")
        for day in (0, 3, 7, 12):
            fsm.tick(EPOCH + timedelta(days=day))

        truth_ids = set()
        for snapshot in load_snapshots("data/snapshots", "slayers2"):
            truth_ids.add(id(snapshot))
            truth_ids.add(id(snapshot.listings))
            for listing in snapshot.listings:
                truth_ids.add(id(listing))
                truth_ids.add(id(listing.code))

        belief_ids = set()
        for record in store.all_records():
            belief_ids.add(id(record))
            belief_ids.add(id(record.sources))
            for sighting in record.sources.values():
                belief_ids.add(id(sighting))

        assert truth_ids.isdisjoint(belief_ids)

    def test_belief_store_cannot_read_the_snapshots(self):
        """
        Structural version of the same rule: state.py has no way to reach
        ground truth even if someone later wanted it to.
        """
        imports = imported_modules(ROOT / "agent" / "state.py")
        assert "sources" not in imports

    def test_mutating_belief_does_not_alter_ground_truth(self):
        sources = build_canned_sources("data/snapshots", "slayers2", EPOCH)
        store = BeliefStore(default_trust=0.6)
        fsm = AgentFSM(store, sources, ConfidenceParams(), "slayers2")
        fsm.tick(EPOCH)

        before = {
            (s.source_id, s.offset_days, tuple(l.code for l in s.listings))
            for s in load_snapshots("data/snapshots", "slayers2")
        }
        for record in store.all_records():
            record.confidence = 0.0
            record.claimed_reward = "TAMPERED"
            record.sources.clear()

        after = {
            (s.source_id, s.offset_days, tuple(l.code for l in s.listings))
            for s in load_snapshots("data/snapshots", "slayers2")
        }
        assert before == after

    def test_the_agent_only_ever_learns_through_percepts(self):
        """
        The FSM's only write path into belief is `_reconcile`, which consumes a
        PerceptBatch.  If another method started writing to the store directly,
        the channel would no longer be narrow and this test should fail.
        """
        tree = ast.parse((ROOT / "agent" / "fsm.py").read_text(encoding="utf-8"))
        writers: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in {"upsert_sighting", "bulk_add"}
                ):
                    writers.add(node.name)
        assert writers == {"_reconcile"}, (
            f"belief is written outside RECONCILE, in: {sorted(writers)}"
        )


class TestNoRobloxAutomation:
    """
    CLAUDE.md hard constraint 1: the agent never automates Roblox.  This is an
    ethics rule, and it is also load-bearing — manual redemption by the user is
    the agent's only true sensor, so automating it away would delete the very
    observation the model depends on.
    """

    def test_no_automation_libraries_anywhere(self):
        forbidden = {
            "pyautogui", "pynput", "keyboard", "mouse", "pydirectinput",
            "selenium", "playwright", "win32api", "win32con", "ctypes",
            "subprocess", "roblox", "rblx",
        }
        offenders = []
        for folder in ("agent", "sources", "ui"):
            for path in sorted((ROOT / folder).glob("*.py")):
                hits = imported_modules(path) & forbidden
                if hits:
                    offenders.append(f"{folder}/{path.name}: {sorted(hits)}")
        assert offenders == [], f"input-automation imports found: {offenders}"

    def test_verification_can_only_come_from_a_user_report(self):
        """
        The only thing that constructs a Verification is the FSM's
        `report_outcome`, which is called by a button the user presses.  Nothing
        derives a verification from a poll.
        """
        source = (ROOT / "agent" / "fsm.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        callers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "apply_verification"
        }
        assert callers == {"report_outcome"}


class TestDocumentationStandard:
    """
    CLAUDE.md: "Comment for a grader, not for a maintainer."  The assignment
    awards marks for a well-commented source program, so the comment standard is
    part of the deliverable and worth a test.
    """

    @pytest.mark.parametrize("module_path", [
        "main.py", "agent/state.py", "agent/model.py", "agent/percepts.py",
        "agent/fsm.py", "agent/clock.py", "sources/base.py", "sources/canned.py",
        "sources/web.py", "ui/app.py",
    ])
    def test_every_module_opens_with_a_substantial_docstring(self, module_path):
        tree = ast.parse((ROOT / module_path).read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree)
        assert docstring, f"{module_path} has no module docstring"
        assert len(docstring) > 200, f"{module_path}'s docstring is too thin"

    @pytest.mark.parametrize("module_path", [
        "agent/state.py", "agent/model.py", "agent/percepts.py", "agent/fsm.py",
        "sources/base.py", "sources/canned.py",
    ])
    def test_agent_modules_explain_their_role_in_the_architecture(self, module_path):
        """
        CLAUDE.md asks each module to say what it does *and how it maps to the
        model-based agent architecture*.
        """
        docstring = ast.get_docstring(
            ast.parse((ROOT / module_path).read_text(encoding="utf-8"))
        ).lower()
        assert any(
            phrase in docstring
            for phrase in ("model-based", "belief", "percept", "sensor", "actuator")
        ), f"{module_path} does not relate itself to the agent architecture"

    def test_public_functions_in_the_model_are_documented(self):
        tree = ast.parse((ROOT / "agent" / "model.py").read_text(encoding="utf-8"))
        undocumented = [
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith("_")
            and not ast.get_docstring(node)
        ]
        assert undocumented == []
