"""Tests for symlink attack protection on host-side writes.

These tests verify that write_bytes_nofollow and all callers refuse to follow
symlinks at any path component. The tests run without bwrap and without root.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.engine import init_run
from factory.journal import _save_state_file, append_audit_row, write_audit_trail
from factory.paths import write_bytes_nofollow
from factory.quarantine import write_lock
from factory.runtime.evidence import EvidenceStore
from factory.runtime.usage import UsageLedger
from factory.state import save_state
from factory.checkpoint import _write_atomic


class TestWriteBytesNofollow:
    """Direct tests of the write_bytes_nofollow primitive."""

    def test_leaf_symlink_raises(self, tmp_path: Path) -> None:
        """A symlink as the leaf component must raise JournalError."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        target_file = tmp_path / "outside.txt"
        target_file.write_text("original", encoding="utf-8")

        # Create symlink at the leaf
        leaf = run_root / "usage.jsonl"
        leaf.symlink_to(target_file)

        with pytest.raises(Exception):  # JournalError or RuntimeError
            write_bytes_nofollow(leaf, b"attack", append=False)
        # Target must be unchanged
        assert target_file.read_text() == "original"

    def test_ancestor_symlink_raises(self, tmp_path: Path) -> None:
        """A symlink as an ancestor directory must raise JournalError."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        target_file = outside_dir / "usage.jsonl"
        target_file.write_text("original", encoding="utf-8")

        # Create symlink: runtime -> outside_dir
        runtime_link = run_root / "runtime"
        runtime_link.symlink_to(outside_dir)

        leaf = runtime_link / "usage.jsonl"
        with pytest.raises(Exception):
            write_bytes_nofollow(leaf, b"attack", append=False)
        assert target_file.read_text() == "original"

    def test_append_to_symlink_raises(self, tmp_path: Path) -> None:
        """Appending to a symlink must raise JournalError."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        target_file = tmp_path / "outside.txt"
        target_file.write_text("original", encoding="utf-8")

        leaf = run_root / "usage.jsonl"
        leaf.symlink_to(target_file)

        with pytest.raises(Exception):
            write_bytes_nofollow(leaf, b"append", append=True)
        assert target_file.read_text() == "original"

    def test_normal_write_works(self, tmp_path: Path) -> None:
        """Normal write without symlinks must succeed."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        leaf = run_root / "usage.jsonl"

        write_bytes_nofollow(leaf, b"hello", append=False)
        assert leaf.read_text() == "hello"

        write_bytes_nofollow(leaf, b" world", append=True)
        assert leaf.read_text() == "hello world"


class TestSaveStateSymlink:
    """Tests for save_state with symlink attacks."""

    def test_state_tmp_symlink_raises(self, tmp_path: Path) -> None:
        """Symlink on .state.json.<pid>.tmp must raise, state.json stays intact."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        # Create a valid state.json first
        state_path = run_root / "state.json"
        state_path.write_text(json.dumps({"run_id": "run-01", "event_count": 0}), encoding="utf-8")

        # Create symlink for the temp file pattern
        import os as _os
        tmp_name = f".state.json.{_os.getpid()}.tmp"
        tmp_link = run_root / tmp_name
        outside = tmp_path / "outside_state.json"
        outside.write_text("hacked", encoding="utf-8")
        tmp_link.symlink_to(outside)

        from factory.state import save_state
        with pytest.raises(Exception):
            save_state(run_root, {"run_id": "run-01", "event_count": 1})
        # state.json must not become a symlink and must be unchanged
        assert state_path.is_file()
        assert not state_path.is_symlink()
        data = json.loads(state_path.read_text())
        assert data["event_count"] == 0
        # Target must be unchanged
        assert outside.read_text() == "hacked"

    def test_state_json_symlink_raises(self, tmp_path: Path) -> None:
        """Symlink on state.json must raise."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        outside = tmp_path / "outside_state.json"
        outside.write_text("hacked", encoding="utf-8")

        state_path = run_root / "state.json"
        state_path.symlink_to(outside)

        with pytest.raises(Exception):
            save_state(run_root, {"run_id": "run-01", "event_count": 1})
        assert outside.read_text() == "hacked"


class TestJournalSymlink:
    """Tests for journal write functions with symlink attacks."""

    def test_save_state_file_symlink(self, tmp_path: Path) -> None:
        """_save_state_file must refuse symlink on state.json."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        outside = tmp_path / "outside_state.json"
        outside.write_text("hacked", encoding="utf-8")

        state_path = run_root / "state.json"
        state_path.symlink_to(outside)

        with pytest.raises(Exception):
            _save_state_file(run_root, {"run_id": "run-01", "event_count": 1})
        assert outside.read_text() == "hacked"

    def test_write_audit_trail_symlink(self, tmp_path: Path) -> None:
        """write_audit_trail must refuse symlink on AUDIT_TRAIL.md."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        outside = tmp_path / "outside_audit.md"
        outside.write_text("hacked", encoding="utf-8")

        audit_path = run_root / "AUDIT_TRAIL.md"
        audit_path.symlink_to(outside)

        with pytest.raises(Exception):
            write_audit_trail(run_root)
        assert outside.read_text() == "hacked"

    def test_append_audit_row_symlink(self, tmp_path: Path) -> None:
        """append_audit_row must refuse symlink on AUDIT_TRAIL.md."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        outside = tmp_path / "outside_audit.md"
        outside.write_text("original", encoding="utf-8")

        audit_path = run_root / "AUDIT_TRAIL.md"
        audit_path.symlink_to(outside)

        event = {"event_id": "EVT-000001", "timestamp_utc": "2026-11-20T00:00:00Z",
                 "stage": "init", "task_id": None, "role": "00-orchestrator",
                 "event_type": "INIT", "action": "test", "verdict": {"status": "INIT_OK", "verified_by": "00-orchestrator"},
                 "prev_hash": "0" * 64, "hash": "1" * 64}
        with pytest.raises(Exception):
            append_audit_row(run_root, event)
        assert outside.read_text() == "original"


class TestUsageLedgerSymlink:
    """Tests for UsageLedger.append with symlink attacks."""

    def test_usage_jsonl_symlink(self, tmp_path: Path) -> None:
        """UsageLedger.append must refuse symlink on runtime/usage.jsonl."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        (run_root / "runtime").mkdir(parents=True)

        outside = tmp_path / "outside_usage.jsonl"
        outside.write_text('{"legit": "data"}\n', encoding="utf-8")

        usage_path = run_root / "runtime" / "usage.jsonl"
        usage_path.symlink_to(outside)

        ledger = UsageLedger(usage_path)
        with pytest.raises(Exception):
            ledger.append({"step": 1, "tokens": 100})
        # Target must be unchanged
        assert outside.read_text() == '{"legit": "data"}\n'


class TestCheckpointSymlink:
    """Tests for checkpoint _write_atomic with symlink attacks."""

    def test_checkpoint_write_atomic_symlink(self, tmp_path: Path) -> None:
        """_write_atomic must refuse symlink on checkpoint file."""
        store = tmp_path / "checkpoints"
        store.mkdir(parents=True)
        outside = tmp_path / "outside_checkpoint.json"
        outside.write_text("hacked", encoding="utf-8")

        checkpoint_path = store / "run-01.json"
        checkpoint_path.symlink_to(outside)

        with pytest.raises(Exception):
            _write_atomic(checkpoint_path, {"run_id": "run-01"}, 0o600)
        assert outside.read_text() == "hacked"


class TestQuarantineSymlink:
    """Tests for quarantine write_lock with symlink attacks."""

    def test_write_lock_symlink(self, tmp_path: Path) -> None:
        """write_lock must refuse symlink on skills-lock.json."""
        lock_dir = tmp_path / "quarantine"
        lock_dir.mkdir(parents=True)
        outside = tmp_path / "outside_lock.json"
        outside.write_text('{"skills": []}', encoding="utf-8")

        lock_path = lock_dir / "skills-lock.json"
        lock_path.symlink_to(outside)

        with pytest.raises(Exception):
            write_lock(lock_path, {"schema_version": "1.0.0", "skills": []})
        assert outside.read_text() == '{"skills": []}'


class TestEvidenceSymlink:
    """Tests for EvidenceStore with symlink attacks."""

    def test_evidence_write_symlink(self, tmp_path: Path) -> None:
        """EvidenceStore._write must refuse symlink on evidence file."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        evidence_dir = run_root / "runtime" / "evidence"
        evidence_dir.mkdir(parents=True)

        outside = tmp_path / "outside_evidence.json"
        outside.write_text("hacked", encoding="utf-8")

        evidence_path = evidence_dir / "step-0001.json"
        evidence_path.symlink_to(outside)

        store = EvidenceStore(run_root)
        with pytest.raises(Exception):
            store._write("step-0001.json", {"test": "data"})
        assert outside.read_text() == "hacked"


class TestSafeWriteTextSymlink:
    """Tests for safe_write_text (now using write_bytes_nofollow)."""

    def test_ancestor_runtime_symlink(self, tmp_path: Path) -> None:
        """safe_write_text must refuse symlink on runtime directory ancestor."""
        run_root = tmp_path / "runs" / "run-01"
        run_root.mkdir(parents=True)
        outside_dir = tmp_path / "outside_runtime"
        outside_dir.mkdir()
        outside_file = outside_dir / "evidence.json"
        outside_file.write_text("original", encoding="utf-8")

        # Create symlink: runtime -> outside_dir
        runtime_link = run_root / "runtime"
        runtime_link.symlink_to(outside_dir)

        from factory.paths import safe_write_text
        evidence_path = runtime_link / "evidence" / "step.json"
        # The parent 'evidence' dir doesn't exist in the target, but we test
        # that the symlink on 'runtime' is caught during path traversal
        evidence_path.parent.mkdir(parents=True, exist_ok=True)

        with pytest.raises(Exception):
            safe_write_text(evidence_path, "attack")
        assert outside_file.read_text() == "original"


class TestInitRunCreatesUsageJsonl:
    """Test that init_run creates runtime/usage.jsonl as a regular file."""

    def test_usage_jsonl_is_regular_file(self, tmp_path: Path) -> None:
        """init_run must create runtime/usage.jsonl as a regular file, not a symlink."""
        run_dir, state, event = init_run("run-01", "nano", root=tmp_path)
        usage_path = run_dir / "runtime" / "usage.jsonl"
        assert usage_path.exists()
        assert usage_path.is_file()
        assert not usage_path.is_symlink()
        # Should be empty initially
        assert usage_path.read_text() == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])