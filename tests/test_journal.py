import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from factory.errors import JournalError
from factory.journal import (
    EVENT_TYPES,
    GENESIS_PREV_HASH,
    append_event,
    canonical_json,
    compute_event_hash,
    verify_chain,
)

ROOT = Path(__file__).resolve().parent.parent


def _event(run_dir: Path, action: str = "did work", **overrides):
    payload = {
        "stage": "s1_spec",
        "task_id": None,
        "role": "01-smart-interviewer",
        "event_type": "ARTIFACT_SUBMIT",
        "why": "test",
        "for_what": "test",
        "action": action,
        "verdict_status": "DRAFT",
        "verified_by": "01-smart-interviewer",
        "artifacts": [],
    }
    payload.update(overrides)
    return append_event(run_dir, **payload)


def test_chain_valid_and_state_anchor(nano_run):
    run_dir, _, _ = nano_run
    _event(run_dir, "second")
    assert verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json") == []
    state = json.loads((run_dir / "state.json").read_text())
    assert state["event_count"] == 2
    assert state["last_event_hash"] == json.loads((run_dir / "blackbox.jsonl").read_text().splitlines()[-1])["hash"]


def test_tamper_detected(nano_run):
    run_dir, _, _ = nano_run
    _event(run_dir, "second")
    lines = (run_dir / "blackbox.jsonl").read_text().splitlines()
    lines[0] = lines[0].replace("Initialised", "Tampered")
    (run_dir / "blackbox.jsonl").write_text("\n".join(lines) + "\n")
    errors = verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json")
    assert errors


def test_tail_truncation_detected(nano_run):
    run_dir, _, _ = nano_run
    _event(run_dir, "second")
    lines = (run_dir / "blackbox.jsonl").read_text().splitlines()
    (run_dir / "blackbox.jsonl").write_text(lines[0] + "\n")
    errors = verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json")
    assert any("event_count" in error or "last_event_hash" in error for error in errors)


def test_empty_journal_is_invalid(tmp_path):
    log = tmp_path / "blackbox.jsonl"
    log.write_text("")
    errors = verify_chain(log)
    assert errors and "empty" in errors[0]


def test_producer_cannot_write_pass(nano_run):
    run_dir, _, _ = nano_run
    with pytest.raises(JournalError, match="Producer != Verifier"):
        append_event(
            run_dir,
            stage="s4_build",
            task_id=None,
            role="12-fullstack-developer",
            event_type="ARTIFACT_SUBMIT",
            why="x",
            for_what="y",
            action="submitted",
            verdict_status="PASS",
            verified_by="12-fullstack-developer",
            evidence=["looks good"],
        )


def test_verifier_name_cannot_be_spoofed(nano_run):
    run_dir, _, _ = nano_run
    with pytest.raises(JournalError, match="Producer != Verifier"):
        append_event(
            run_dir,
            stage="s4_build",
            task_id=None,
            role="12-fullstack-developer",
            event_type="ARTIFACT_SUBMIT",
            why="x",
            for_what="y",
            action="submitted",
            verdict_status="PASS",
            verified_by="02-independent-verifier",
            evidence=["spoof"],
        )


def test_verify_chain_rejects_forged_pass(nano_run):
    run_dir, _, _ = nano_run
    events = [json.loads(line) for line in (run_dir / "blackbox.jsonl").read_text().splitlines()]
    forged = {
        "event_id": "EVT-000002",
        "run_id": run_dir.name,
        "seq": 2,
        "timestamp_utc": "2026-01-01T00:00:00Z",
        "stage": "s4_build",
        "task_id": None,
        "role": "12-fullstack-developer",
        "event_type": "ARTIFACT_SUBMIT",
        "model": {"tier": "tier-2-balanced", "model_id": "x", "effort": "medium"},
        "why": "x",
        "for_what": "y",
        "action": "forged",
        "artifacts": [],
        "verdict": {"status": "PASS", "verified_by": "12-fullstack-developer", "evidence": ["x"]},
        "parent_event_id": None,
        "prev_hash": events[0]["hash"],
    }
    forged["hash"] = compute_event_hash(events[0]["hash"], forged)
    (run_dir / "blackbox.jsonl").write_text("\n".join([json.dumps(events[0]), canonical_json(forged)]) + "\n")
    errors = verify_chain(run_dir / "blackbox.jsonl")
    assert any("semantic error" in error for error in errors)


def test_artifacts_are_hashed(nano_run):
    run_dir, _, _ = nano_run
    artifact = run_dir / "artifacts" / "note.md"
    artifact.write_text("hello", encoding="utf-8")
    event = _event(run_dir, "wrote note", artifacts=[str(artifact)])
    assert event["artifacts"][0]["bytes"] == 5
    assert len(event["artifacts"][0]["sha256"]) == 64


def test_audit_trail_escapes_table_cells(nano_run):
    run_dir, _, _ = nano_run
    _event(run_dir, "line1 | col | newline\nBROKEN")
    text = (run_dir / "AUDIT_TRAIL.md").read_text()
    assert "line1 \\| col \\| newline BROKEN" in text


def test_concurrent_appends_keep_chain(nano_run):
    run_dir, _, _ = nano_run
    # Subprocesses, not multiprocessing: the same test exercises `flock` on
    # POSIX and `msvcrt.locking` on Windows, and `fork` does not exist there.
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from factory.journal import append_event\n"
        "append_event(Path(sys.argv[1]), stage='s4_build', task_id=None,\n"
        "             role='12-fullstack-developer', event_type='ARTIFACT_SUBMIT',\n"
        "             why='race', for_what='race', action='writer ' + sys.argv[2],\n"
        "             verdict_status='DRAFT', verified_by='12-fullstack-developer',\n"
        "             artifacts=[])\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(run_dir), str(index)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(12)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
    errors = verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json")
    assert errors == []
    events = [json.loads(line) for line in (run_dir / "blackbox.jsonl").read_text().splitlines()]
    assert len(events) == 13
    assert len({event["event_id"] for event in events}) == 13


def test_genesis_hash_is_zeroes():
    assert GENESIS_PREV_HASH == "0" * 64


def test_event_types_and_schema_enum_move_together():
    """A new event type must be in both places or the journal rejects it."""
    from factory.config import load_json, SCHEMA_DIR

    schema = load_json(SCHEMA_DIR / "event.schema.json")
    assert set(schema["properties"]["event_type"]["enum"]) == EVENT_TYPES


def _rerecord(run_dir: Path, **overrides):
    payload = {
        "stage": "s1_spec",
        "task_id": None,
        "role": "01-smart-interviewer",
        "event_type": "ARTIFACT_RERECORD",
        "why": "re-record",
        "for_what": "test",
        "action": "re-recorded",
        "verdict_status": "DRAFT",
        "verified_by": "01-smart-interviewer",
        "artifacts": [],
        "reason": "reviewed again",
        "evidence": ["artifacts/acceptance.md#A-1"],
    }
    payload.update(overrides)
    return payload


def test_re_record_requires_a_reason(nano_run):
    run_dir, _, _ = nano_run
    payload = _rerecord(run_dir, reason="  ")
    with pytest.raises(JournalError, match="non-empty reason"):
        append_event(run_dir, **payload)


def test_re_record_requires_evidence(nano_run):
    run_dir, _, _ = nano_run
    payload = _rerecord(run_dir, evidence=[])
    with pytest.raises(JournalError, match="at least one evidence"):
        append_event(run_dir, **payload)


def test_re_record_cannot_carry_pass(nano_run):
    run_dir, _, _ = nano_run
    artifact = run_dir / "artifacts" / "spec.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# spec\n", encoding="utf-8")
    payload = _rerecord(
        run_dir,
        verdict_status="PASS",
        verified_by="02-independent-verifier",
        role="02-independent-verifier",
        artifacts=[
            {
                "path": "artifacts/spec.md",
                "sha256": "a" * 64,
                "bytes": 7,
                "old_sha256": "b" * 64,
            }
        ],
    )
    with pytest.raises(JournalError, match="must use DRAFT"):
        append_event(run_dir, **payload)


def test_re_record_requires_both_hashes(nano_run):
    run_dir, _, _ = nano_run
    artifact = run_dir / "artifacts" / "spec.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# spec\n", encoding="utf-8")
    payload = _rerecord(
        run_dir,
        artifacts=[{"path": "artifacts/spec.md", "sha256": "a" * 64, "bytes": 7}],
    )
    with pytest.raises(JournalError, match="old_sha256"):
        append_event(run_dir, **payload)


def test_re_record_with_an_unchanged_hash_is_rejected(nano_run):
    run_dir, _, _ = nano_run
    artifact = run_dir / "artifacts" / "spec.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# spec\n", encoding="utf-8")
    digest = "a" * 64
    payload = _rerecord(
        run_dir,
        artifacts=[
            {"path": "artifacts/spec.md", "sha256": digest, "bytes": 7, "old_sha256": digest}
        ],
    )
    with pytest.raises(JournalError, match="unchanged hash"):
        append_event(run_dir, **payload)


def test_valid_re_record_is_chained_and_carries_the_reason(nano_run):
    run_dir, _, _ = nano_run
    artifact = run_dir / "artifacts" / "spec.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# spec\n", encoding="utf-8")
    event = append_event(
        run_dir,
        **_rerecord(
            run_dir,
            artifacts=[
                {
                    "path": "artifacts/spec.md",
                    "sha256": "a" * 64,
                    "bytes": 7,
                    "old_sha256": "b" * 64,
                }
            ],
        ),
    )
    assert event["reason"] == "reviewed again"
    assert event["artifacts"][0]["old_sha256"] == "b" * 64
    assert verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json") == []


def test_events_without_a_reason_still_validate(nano_run):
    """The optional `reason` key must not invalidate history written before it."""
    run_dir, _, _ = nano_run
    event = _event(run_dir, "no reason here")
    assert "reason" not in event
    assert verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json") == []
