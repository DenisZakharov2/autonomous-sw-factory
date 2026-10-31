"""`ARTIFACT_RERECORD`: the legal way to change an accepted artifact.

The gate freezes the hash of everything it approved.  Before this event the
only honest options were "do not change the file" or "break `verify`"; a silent
rewrite was indistinguishable from tampering.  A re-record keeps the gate
decision intact, names the owner, states a reason, and records both the hash
the gate saw and the hash that now binds the file.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from factory.engine import artifact_rerecord
from factory.errors import JournalError, RunError
from factory.journal import read_events, verify_chain
from factory.verify import rerecord_summary, verify_run

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "nano-todo"
SPEC = "artifacts/spec.md"
OWNER = "01-smart-interviewer"
TASK_FILE = "product/src/todo.py"
TASK_ID = "TASK-001"


@pytest.fixture()
def run(tmp_path, monkeypatch) -> Path:
    """A complete accepted run, copied so the reference example stays intact."""
    target = tmp_path / "nano-todo"
    shutil.copytree(EXAMPLE, target)
    monkeypatch.setenv("FACTORY_ROOT", str(target))
    assert verify_run(target) == []
    return target


def _append(path: Path, text: str = "\n<!-- reviewed again -->\n") -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _gate_hash(run_dir: Path, path: str) -> str:
    for event in read_events(run_dir / "blackbox.jsonl"):
        if event.get("event_type") != "GATE_DECISION":
            continue
        for item in event.get("artifacts") or []:
            if item.get("path") == path:
                return str(item["sha256"])
    raise AssertionError(f"{path} is not recorded by any gate")


def test_owner_may_re_record_and_verify_stays_green(run):
    _append(run / SPEC)
    old = _gate_hash(run, SPEC)
    event = artifact_rerecord(
        "nano-todo",
        path=SPEC,
        role=OWNER,
        reason="Правка формулировки REQ-001 после ревью",
        evidence=["artifacts/acceptance.md#A-1"],
        root=run.parent,
    )
    item = event["artifacts"][0]
    assert event["event_type"] == "ARTIFACT_RERECORD"
    assert event["verdict"]["status"] == "DRAFT"
    assert event["stage"] == "s1_spec"
    assert event["task_id"] is None
    assert event["role"] == OWNER
    assert event["reason"] == "Правка формулировки REQ-001 после ревью"
    assert item["path"] == SPEC
    assert item["old_sha256"] == old
    assert item["sha256"] != old
    assert verify_run(run) == []


def test_editing_after_the_re_record_is_tampering_again(run):
    _append(run / SPEC)
    artifact_rerecord(
        "nano-todo", path=SPEC, role=OWNER, reason="typo", evidence=["e"], root=run.parent
    )
    assert verify_run(run) == []
    _append(run / SPEC, "<!-- and one more edit -->\n")
    errors = verify_run(run)
    assert any("tampered after submission/approval" in error for error in errors)


def test_foreign_role_cannot_re_record(run):
    _append(run / SPEC)
    with pytest.raises(JournalError, match="owned by 01-smart-interviewer"):
        artifact_rerecord(
            "nano-todo",
            path=SPEC,
            role="12-fullstack-developer",
            reason="я так решил",
            evidence=["e"],
            root=run.parent,
        )


def test_reason_is_mandatory(run):
    _append(run / SPEC)
    with pytest.raises(RunError, match="non-empty --reason"):
        artifact_rerecord(
            "nano-todo", path=SPEC, role=OWNER, reason="   ", evidence=["e"], root=run.parent
        )


def test_evidence_is_mandatory(run):
    _append(run / SPEC)
    with pytest.raises(RunError, match="at least one --evidence"):
        artifact_rerecord(
            "nano-todo", path=SPEC, role=OWNER, reason="typo", evidence=[], root=run.parent
        )


def test_unchanged_artifact_is_not_re_recorded(run):
    with pytest.raises(RunError, match="unchanged"):
        artifact_rerecord(
            "nano-todo", path=SPEC, role=OWNER, reason="typo", evidence=["e"], root=run.parent
        )


def test_path_outside_the_run_is_refused(run):
    with pytest.raises(RunError, match="outside"):
        artifact_rerecord(
            "nano-todo",
            path="../../outside.md",
            role=OWNER,
            reason="typo",
            evidence=["e"],
            root=run.parent,
        )


def test_unrecorded_path_is_refused(run):
    (run / "artifacts" / "notes.md").write_text("scratch\n", encoding="utf-8")
    with pytest.raises(RunError, match="never recorded|not declared"):
        artifact_rerecord(
            "nano-todo",
            path="artifacts/notes.md",
            role=OWNER,
            reason="typo",
            evidence=["e"],
            root=run.parent,
        )


def test_gate_status_and_stage_status_do_not_move(run):
    before = json.loads((run / "state.json").read_text(encoding="utf-8"))
    _append(run / SPEC)
    artifact_rerecord(
        "nano-todo", path=SPEC, role=OWNER, reason="typo", evidence=["e"], root=run.parent
    )
    after = json.loads((run / "state.json").read_text(encoding="utf-8"))
    assert after["stage_status"] == before["stage_status"]
    assert after["gate_status"] == before["gate_status"]
    assert after["current_stage"] == before["current_stage"]
    assert after["tasks"] == before["tasks"]
    # Only the journal anchor moves.
    assert after["event_count"] == before["event_count"] + 1


def test_old_gate_hash_stays_in_the_journal_and_the_audit_trail(run):
    old = _gate_hash(run, SPEC)
    _append(run / SPEC)
    event = artifact_rerecord(
        "nano-todo", path=SPEC, role=OWNER, reason="typo", evidence=["e"], root=run.parent
    )
    events = read_events(run / "blackbox.jsonl")
    gate_hashes = [
        item["sha256"]
        for recorded in events
        if recorded.get("event_type") == "GATE_DECISION"
        for item in recorded.get("artifacts") or []
        if item.get("path") == SPEC
    ]
    assert old in gate_hashes
    trail = (run / "AUDIT_TRAIL.md").read_text(encoding="utf-8")
    assert f"`{event['event_id']}`" in trail
    assert "ARTIFACT_RERECORD" in trail
    assert old[:8] in trail


def test_chain_still_verifies_after_a_re_record(run):
    _append(run / SPEC)
    artifact_rerecord(
        "nano-todo", path=SPEC, role=OWNER, reason="typo", evidence=["e"], root=run.parent
    )
    state_path = run / "state.json"
    assert verify_chain(run / "blackbox.jsonl", state_path) == []


def test_summary_counts_re_records_by_stage_and_task(run):
    _append(run / SPEC)
    artifact_rerecord(
        "nano-todo", path=SPEC, role=OWNER, reason="typo", evidence=["e"], root=run.parent
    )
    _append(run / "tasks/TASK-001/deliverable.md", "\n<!-- reviewed -->\n")
    artifact_rerecord(
        "nano-todo",
        path="tasks/TASK-001/deliverable.md",
        role="12-fullstack-developer",
        reason="comment only",
        evidence=["e"],
        root=run.parent,
    )
    summary = rerecord_summary(run)
    assert {(row["stage"], row["task_id"]): row["count"] for row in summary} == {
        ("s1_spec", None): 1,
        ("s4_build", TASK_ID): 1,
    }


def test_a_task_artifact_re_record_binds_its_own_record(run):
    """`tasks/TASK-001/deliverable.md` is recorded by `TASK_DONE`, not by a gate."""
    path = "tasks/TASK-001/deliverable.md"
    _append(run / path, "\n<!-- owner note -->\n")
    event = artifact_rerecord(
        "nano-todo",
        path=path,
        role="12-fullstack-developer",
        reason="добавлена заметка владельца",
        evidence=["tasks/TASK-001/verification.md"],
        root=run.parent,
    )
    assert event["task_id"] == TASK_ID
    assert event["stage"] == "s4_build"
    assert verify_run(run) == []


def test_a_file_inside_a_directory_artifact_needs_the_directory_too(run):
    """The gate binds `product/src/` as a directory hash, so one file is not enough.

    `s4_build` records both the task's files (`TASK_DONE`) and the produced
    tree (`GATE_DECISION`, path ending in `/`).  Re-recording the file settles
    the task record; `verify` keeps naming the directory until that record is
    re-recorded as well.  Both are explicit events, which is the point.
    """
    _append(run / TASK_FILE, "\n# reviewed\n")
    artifact_rerecord(
        "nano-todo",
        path=TASK_FILE,
        role="12-fullstack-developer",
        reason="comment only",
        evidence=["e"],
        root=run.parent,
    )
    errors = verify_run(run)
    assert any("product/src/" in error for error in errors), errors
    artifact_rerecord(
        "nano-todo",
        path="product/src/",
        role="12-fullstack-developer",
        reason="comment only",
        evidence=["e"],
        root=run.parent,
    )
    assert verify_run(run) == []


def test_second_re_record_supersedes_the_first(run):
    _append(run / SPEC)
    artifact_rerecord(
        "nano-todo", path=SPEC, role=OWNER, reason="first", evidence=["e"], root=run.parent
    )
    _append(run / SPEC, "<!-- second pass -->\n")
    second = artifact_rerecord(
        "nano-todo", path=SPEC, role=OWNER, reason="second", evidence=["e"], root=run.parent
    )
    assert second["artifacts"][0]["old_sha256"] == read_events(run / "blackbox.jsonl")[-2][
        "artifacts"
    ][0]["sha256"]
    assert verify_run(run) == []
    assert len(rerecord_summary(run)) == 1
    assert rerecord_summary(run)[0]["count"] == 2
