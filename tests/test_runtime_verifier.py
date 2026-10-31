"""The independent verifier is a step, not a label.

Every role gate and every `TASK_DONE` in a new run must be preceded by a real
`VERIFY` event, written by `02-independent-verifier` from a report it produced.
These tests pin the four things that make that true: the work order, the write
scope, the report grammar, and the journal order.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from factory.artifacts import validate_verification_report, verification_verdict
from factory.config import load_pipeline
from factory.engine import (
    gate_approve,
    import_plan,
    init_run,
    next_action,
    submit_work,
    task_accept,
    task_claim,
    task_submit,
)
from factory.errors import JournalError, RunError, VerificationError
from factory.journal import append_event, compute_event_hash, read_events
from factory.runtime.base import ExecutionResult
from factory.runtime.fake import FakeExecutor
from factory.runtime.loop import _collect_writable_paths, run_loop
from factory.state import load_state, save_state
from factory.verify import verification_policy_errors, verify_run
from conftest import write_stage_verification, write_task_verification

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "nano-todo"
VERIFIER = "02-independent-verifier"
REVIEW = (
    "# Сканирование\n## 1. Сканирование\n\nСкиллов нет.\n"
    "## 2. Вердикты\n\nНет.\n## 3. Лок\n\nПусто.\n"
)
PLAN = (
    "# Plan\n## 1. Задачи\n"
    "| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Проверка |\n"
    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
    "| `TASK-001` | `12-fullstack-developer` | `REQ-001` | `-` | `product/src/todo.py` | `true` |\n"
)
DELIVERABLE = (
    "## 1. Что сделано\n\nСделано.\n## 2. REQ\n\nREQ-001.\n"
    "## 3. Проверка\n\npytest.\n## 4. Diff\n\nНовый файл.\n"
)


def _write(run: Path, relative: str, text: str) -> Path:
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _advance_to(run_dir: Path, stage_id: str) -> None:
    """Mark every earlier stage accepted, as if the run had reached it."""
    pipeline = load_pipeline()
    state = load_state(run_dir)
    for stage in pipeline["stages"]:
        if stage["id"] == stage_id:
            break
        state["stage_status"][stage["id"]] = "accepted"
        state["gate_status"][stage["gate"]["id"]]["status"] = "approved"
    state["stage_status"][stage_id] = "in_progress"
    state["current_stage"] = stage_id
    save_state(run_dir, state)


@pytest.fixture()
def quarantine_run(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    run_dir, _, _ = init_run("run-01", "nano", root=runs)
    _advance_to(run_dir, "s15_quarantine")
    (tmp_path / "skills-lock.json").write_text(
        json.dumps({"schema_version": "1.0.0", "skills": []}), encoding="utf-8"
    )
    _write(run_dir, "artifacts/security-review-skills.md", REVIEW)
    return run_dir


@pytest.fixture()
def build_run(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    run_dir, _, _ = init_run("run-01", "nano", root=runs)
    _advance_to(run_dir, "s4_build")
    _write(run_dir, "artifacts/plan.md", PLAN)
    import_plan("run-01", root=runs)
    _write(run_dir, "product/tests/test_cli.py", "def test_ok():\n    assert True\n")
    _write(run_dir, "product/src/todo.py", "TASKS = []\n")
    _write(run_dir, "tasks/TASK-001/deliverable.md", DELIVERABLE)
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=runs)
    task_submit(
        "run-01",
        "TASK-001",
        "12-fullstack-developer",
        ["product/src/todo.py", "tasks/TASK-001/deliverable.md"],
        root=runs,
    )
    return run_dir


# --- the policy is opt-in for new runs only --------------------------------


def test_a_new_run_opts_into_role_verification(quarantine_run):
    state = load_state(quarantine_run)
    assert state["verification_policy"] == "role"


def test_a_historical_run_without_the_policy_still_verifies(tmp_path, monkeypatch):
    """A run recorded before the verifier step keeps verifying untouched.

    The committed example now demonstrates the new flow, so the historical
    shape is rebuilt from it: no policy, no reports, no VERIFY events, and a
    journal re-chained without them.
    """
    target = tmp_path / "nano-todo"
    shutil.copytree(EXAMPLE, target)
    monkeypatch.setenv("FACTORY_ROOT", str(target))
    state = json.loads((target / "state.json").read_text(encoding="utf-8"))
    state.pop("verification_policy", None)
    (target / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    for report in target.rglob("verification/verification.md"):
        report.unlink()
    events = [
        event
        for event in read_events(target / "blackbox.jsonl")
        if event["event_type"] != "VERIFY"
        and not (
            event["event_type"] == "ARTIFACT_SUBMIT"
            and event["role"] == VERIFIER
        )
    ]
    for event in events:
        event["artifacts"] = [
            item
            for item in event.get("artifacts") or []
            if not str(item.get("path", "")).endswith("verification/verification.md")
        ]
    _rechain(target, events)
    assert "verification_policy" not in json.loads(
        (target / "state.json").read_text(encoding="utf-8")
    )
    assert verify_run(target) == []


# --- the gate needs the verifier's report ----------------------------------


def test_a_gate_does_not_reach_review_without_the_report(quarantine_run):
    event = submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role="00-orchestrator",
        artifacts=["artifacts/security-review-skills.md", "skills-lock.json"],
        root=quarantine_run.parent,
    )
    assert event["event_type"] == "ARTIFACT_SUBMIT"
    # Everything the producer owes is on disk, but the stage is not reviewable:
    # the verifier has not written its report yet.
    assert load_state(quarantine_run)["stage_status"]["s15_quarantine"] == "in_progress"
    with pytest.raises(RunError):
        gate_approve(
            "run-01",
            gate_id="gate_s15",
            approver=VERIFIER,
            evidence=["premature"],
            root=quarantine_run.parent,
        )


def test_a_forged_review_status_still_cannot_skip_the_report(quarantine_run):
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role="00-orchestrator",
        artifacts=["artifacts/security-review-skills.md", "skills-lock.json"],
        root=quarantine_run.parent,
    )
    state = load_state(quarantine_run)
    state["stage_status"]["s15_quarantine"] = "review"
    save_state(quarantine_run, state)
    with pytest.raises(VerificationError, match="verification"):
        gate_approve(
            "run-01",
            gate_id="gate_s15",
            approver=VERIFIER,
            evidence=["forced"],
            root=quarantine_run.parent,
        )


def test_the_gate_order_for_the_verifier_carries_only_the_report(quarantine_run):
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role="00-orchestrator",
        artifacts=["artifacts/security-review-skills.md", "skills-lock.json"],
        root=quarantine_run.parent,
    )
    order = next_action("run-01", root=quarantine_run.parent)
    assert order["action"] == "verify"
    assert order["roles"] == [VERIFIER]
    assert order["next_role"] == VERIFIER
    assert [spec["id"] for spec in order["artifacts"]] == ["verification"]
    assert {item["id"] for item in order["pending_artifacts"]} == {"verification"}
    assert "artifacts/verification/s15_quarantine/verification.md" in order["instructions"]


def test_gate_approval_records_verify_before_the_decision(quarantine_run):
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role="00-orchestrator",
        artifacts=["artifacts/security-review-skills.md", "skills-lock.json"],
        root=quarantine_run.parent,
    )
    write_stage_verification(quarantine_run, "s15_quarantine")
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role=VERIFIER,
        artifacts=["artifacts/verification/s15_quarantine/verification.md"],
        root=quarantine_run.parent,
    )
    gate_approve(
        "run-01",
        gate_id="gate_s15",
        approver=VERIFIER,
        evidence=["verified"],
        root=quarantine_run.parent,
    )
    kinds = [event["event_type"] for event in read_events(quarantine_run / "blackbox.jsonl")]
    assert kinds.index("VERIFY") < kinds.index("GATE_DECISION")
    assert kinds[-1] == "GATE_DECISION"
    verify_event = next(
        event
        for event in read_events(quarantine_run / "blackbox.jsonl")
        if event["event_type"] == "VERIFY"
    )
    assert verify_event["role"] == VERIFIER
    assert verify_event["verdict"]["status"] == "PASS"
    assert verify_event["artifacts"][0]["path"] == (
        "artifacts/verification/s15_quarantine/verification.md"
    )
    assert verify_run(quarantine_run, "s15_quarantine", require_gate=True) == []


def test_a_failed_report_does_not_close_the_gate(quarantine_run):
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role="00-orchestrator",
        artifacts=["artifacts/security-review-skills.md", "skills-lock.json"],
        root=quarantine_run.parent,
    )
    write_stage_verification(
        quarantine_run, "s15_quarantine", verdict="FAIL", reason="lock has no hashes"
    )
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role=VERIFIER,
        artifacts=["artifacts/verification/s15_quarantine/verification.md"],
        root=quarantine_run.parent,
    )
    with pytest.raises(VerificationError, match="FAIL"):
        gate_approve(
            "run-01",
            gate_id="gate_s15",
            approver=VERIFIER,
            evidence=["looks fine, honestly"],
            root=quarantine_run.parent,
        )
    state = load_state(quarantine_run)
    assert state["stage_status"]["s15_quarantine"] == "review"
    assert state["gate_status"]["gate_s15"]["status"] == "pending"


# --- tasks ------------------------------------------------------------------


def test_task_accept_without_a_report_fails(build_run):
    with pytest.raises(VerificationError, match="no valid verification report"):
        task_accept("run-01", "TASK-001", root=build_run.parent)


def test_a_task_report_with_two_checks_is_not_a_report(build_run):
    write_task_verification(build_run, "TASK-001", checks=2)
    with pytest.raises(VerificationError, match="at least 3"):
        task_accept("run-01", "TASK-001", root=build_run.parent)


def test_a_task_report_with_the_template_choice_is_not_a_verdict(build_run):
    path = write_task_verification(build_run, "TASK-001")
    path.write_text(
        path.read_text(encoding="utf-8").replace("- Вердикт: PASS", "- Вердикт: PASS | FAIL"),
        encoding="utf-8",
    )
    assert any("unresolved" in error for error in validate_verification_report(path))
    with pytest.raises(VerificationError):
        task_accept("run-01", "TASK-001", root=build_run.parent)


def test_task_accept_records_verify_then_task_done(build_run):
    write_task_verification(build_run, "TASK-001")
    task_accept("run-01", "TASK-001", root=build_run.parent)
    events = read_events(build_run / "blackbox.jsonl")
    kinds = [event["event_type"] for event in events if event.get("task_id") == "TASK-001"]
    assert kinds[-2:] == ["VERIFY", "TASK_DONE"]
    done = events[-1]
    assert done["role"] == VERIFIER and done["verdict"]["status"] == "PASS"
    assert any(
        item["path"] == "tasks/TASK-001/verification/verification.md"
        for item in done["artifacts"]
    )
    # The task is closed; the stage still owes its own report, which is a
    # different subject and a different event.
    errors = verify_run(build_run, "s4_build")
    assert not any("TASK-001/verification" in error for error in errors), errors


def test_a_failed_task_report_returns_the_task_to_rework(build_run):
    write_task_verification(build_run, "TASK-001", verdict="FAIL", reason="no tests")
    with pytest.raises(VerificationError, match="FAIL"):
        task_accept("run-01", "TASK-001", root=build_run.parent)
    assert load_state(build_run)["tasks"]["TASK-001"]["status"] == "review"


# --- write scope ------------------------------------------------------------


def test_the_verifier_can_write_only_its_own_report(build_run):
    order = next_action("run-01", root=build_run.parent)
    assert order["action"] == "verify" and order["task_id"] == "TASK-001"
    state = load_state(build_run)
    entries = _collect_writable_paths(order, build_run, state["tasks"]["TASK-001"])
    paths = {str(path) for path in entries}
    assert str((build_run / "tasks" / "TASK-001" / "verification").resolve()) in paths
    assert not any("product" in path for path in paths), paths
    assert not any(path.endswith("state.json") for path in paths)
    assert str(build_run.resolve()) not in paths


def test_the_verifier_cannot_submit_a_producer_artifact(build_run):
    with pytest.raises(RunError):
        submit_work(
            "run-01",
            stage_id="s4_build",
            role=VERIFIER,
            artifacts=["product/src/todo.py"],
            task_id="TASK-001",
            root=build_run.parent,
        )


def test_a_producer_cannot_write_a_verify_event(quarantine_run):
    with pytest.raises(JournalError, match="02-independent-verifier"):
        append_event(
            quarantine_run,
            stage="s15_quarantine",
            task_id=None,
            role="00-orchestrator",
            event_type="VERIFY",
            why="self approval",
            for_what="close the stage",
            action="VERIFY PASS",
            verdict_status="PASS",
            verified_by="00-orchestrator",
            evidence=["trust me"],
            artifacts=["artifacts/security-review-skills.md"],
        )


# --- the journal is the witness --------------------------------------------


def _rechain(run_dir: Path, events: list[dict]) -> None:
    """Rewrite a journal as a self-consistent chain of the events given."""
    prev = "0" * 64
    lines = []
    for index, event in enumerate(events, 1):
        payload = {key: value for key, value in event.items() if key != "hash"}
        payload["seq"] = index
        payload["event_id"] = f"EVT-{index:06d}"
        payload["prev_hash"] = prev
        digest = compute_event_hash(prev, payload)
        lines.append(json.dumps({**payload, "hash": digest}, ensure_ascii=False))
        prev = digest
    (run_dir / "blackbox.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    state = load_state(run_dir)
    state["last_event_hash"] = prev
    state["event_count"] = len(events)
    save_state(run_dir, state)


def test_verify_catches_a_gate_closed_without_a_verify_event(quarantine_run):
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role="00-orchestrator",
        artifacts=["artifacts/security-review-skills.md", "skills-lock.json"],
        root=quarantine_run.parent,
    )
    write_stage_verification(quarantine_run, "s15_quarantine")
    submit_work(
        "run-01",
        stage_id="s15_quarantine",
        role=VERIFIER,
        artifacts=["artifacts/verification/s15_quarantine/verification.md"],
        root=quarantine_run.parent,
    )
    gate_approve(
        "run-01",
        gate_id="gate_s15",
        approver=VERIFIER,
        evidence=["verified"],
        root=quarantine_run.parent,
    )
    state = load_state(quarantine_run)
    assert verification_policy_errors(state, read_events(quarantine_run / "blackbox.jsonl")) == []

    events = [e for e in read_events(quarantine_run / "blackbox.jsonl") if e["event_type"] != "VERIFY"]
    _rechain(quarantine_run, events)
    errors = verification_policy_errors(state, read_events(quarantine_run / "blackbox.jsonl"))
    assert any("without a preceding VERIFY PASS" in error for error in errors), errors


def test_a_task_done_without_verify_is_caught(build_run):
    write_task_verification(build_run, "TASK-001")
    task_accept("run-01", "TASK-001", root=build_run.parent)
    events = [e for e in read_events(build_run / "blackbox.jsonl") if e["event_type"] != "VERIFY"]
    _rechain(build_run, events)
    errors = verification_policy_errors(load_state(build_run), read_events(build_run / "blackbox.jsonl"))
    assert any("TASK_DONE without a preceding VERIFY PASS" in error for error in errors), errors


def test_a_report_changed_after_verify_is_tampering(build_run):
    write_task_verification(build_run, "TASK-001")
    task_accept("run-01", "TASK-001", root=build_run.parent)
    path = build_run / "tasks" / "TASK-001" / "verification" / "verification.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8")
    errors = verify_run(build_run, "s4_build")
    assert any("tampered after submission/approval" in error for error in errors), errors


def test_a_new_submission_makes_the_old_verdict_stale(build_run):
    """A PASS about old bytes is not a licence for new ones."""
    from factory.engine import verification_is_stale

    subject = {"stage_id": "s4_build", "task_id": "TASK-001"}
    assert verification_is_stale(build_run, **subject) is True
    write_task_verification(build_run, "TASK-001")
    task_accept("run-01", "TASK-001", root=build_run.parent)
    assert verification_is_stale(build_run, **subject) is False

    state = load_state(build_run)
    state["tasks"]["TASK-001"]["status"] = "in_progress"
    save_state(build_run, state)
    task_submit(
        "run-01",
        "TASK-001",
        "12-fullstack-developer",
        ["product/src/todo.py", "tasks/TASK-001/deliverable.md"],
        root=build_run.parent,
    )
    assert verification_is_stale(build_run, **subject) is True


# --- report grammar ---------------------------------------------------------


def test_report_grammar_rejects_a_missing_output(tmp_path):
    path = tmp_path / "verification.md"
    path.write_text(
        "# Проверка\n\n## 1. Проверено\n\nВсё.\n\n## 2. Проверки\n\n"
        + "".join(
            f"### Проверка {index}: проверка\n\n- Команда: `true`\n- Вывод: ``\n\n"
            for index in (1, 2, 3)
        )
        + "## 3. Вердикт\n\n- Вердикт: PASS\n",
        encoding="utf-8",
    )
    errors = validate_verification_report(path)
    assert any("empty 'Вывод:'" in error for error in errors), errors
    assert verification_verdict(path.read_text(encoding="utf-8"))[0] == "PASS"


def test_a_stage_semantic_defect_goes_back_to_the_producer(tmp_path, monkeypatch):
    """A verifier cannot fix a bad acceptance matrix; asking it again only burns an attempt."""
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    run_dir, _, _ = init_run("run-01", "nano", root=runs)
    _advance_to(run_dir, "s5_acceptance")
    _write(
        run_dir,
        "artifacts/spec.md",
        "## 1. Требования\n\nREQ-001.\n## 2. Приёмка\n\nREQ-001 PASS.\n",
    )
    _write(
        run_dir,
        "artifacts/test-report.md",
        "## 1. Прогоны\n\nТри прогона.\n## 2. Покрытие REQ\n\nREQ-001.\n## 3. Flaky\n\nНет.\n",
    )
    _write(
        run_dir,
        "artifacts/acceptance.md",
        "## 1. Матрица приёмки\n"
        "| REQ | Критерий | Доказательство | Статус |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| `REQ-001` | работает | тест | пройдено тестом |\n"
        "## 2. Дефекты\n"
        "| ID | Severity | Описание | Статус |\n"
        "| :--- | :--- | :--- | :--- |\n",
    )
    order = next_action("run-01", root=runs)
    assert order["action"] == "produce"
    assert order["next_role"] == "12-fullstack-developer"
    # The producer is not handed the verifier's report writable.
    assert "verification" not in [spec.get("id") for spec in order["artifacts"]]


def test_a_semantically_invalid_stage_runs_the_step_instead_of_resubmitting(tmp_path, monkeypatch):
    """The "nothing pending" shortcut must not spin on a stage that still fails its rules."""
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    run_dir, _, _ = init_run("run-01", "nano", root=runs)
    _advance_to(run_dir, "s5_acceptance")
    _write(
        run_dir,
        "artifacts/spec.md",
        "## 1. Требования\n\nREQ-001.\n## 2. Приёмка\n\nREQ-001 PASS.\n",
    )
    _write(
        run_dir,
        "artifacts/test-report.md",
        "## 1. Прогоны\n\nТри прогона.\n## 2. Покрытие REQ\n\nREQ-001.\n## 3. Flaky\n\nНет.\n",
    )
    _write(
        run_dir,
        "artifacts/acceptance.md",
        "## 1. Матрица приёмки\n"
        "| REQ | Критерий | Доказательство | Статус |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| `REQ-001` | работает | тест | пройдено тестом |\n"
        "## 2. Матрица дефектов\n"
        "| ID | Severity | Описание | Статус |\n"
        "| :--- | :--- | :--- | :--- |\n",
    )

    class Recorder:
        name = "recorder"

        def __init__(self):
            self.calls: list[str] = []

        def execute(self, order, context):
            self.calls.append(str(order.get("stage")))
            return ExecutionResult(exit_code=0)

    executor = Recorder()
    result = run_loop("run-01", executor, max_steps=4, root=runs)
    assert executor.calls and executor.calls[0] == "s5_acceptance"
    submits = [
        event
        for event in read_events(run_dir / "blackbox.jsonl")
        if event["event_type"] == "ARTIFACT_SUBMIT" and event["stage"] == "s5_acceptance"
    ]
    # The bug this guards against: 60 loop steps, zero executor calls and one
    # ARTIFACT_SUBMIT per iteration.  At most one submit per executed step.
    assert len(submits) <= len(executor.calls), (len(submits), executor.calls)
    assert result.status in {"no_progress", "max_steps"}


# --- end to end -------------------------------------------------------------


def test_verification_fail_rework_converges(tmp_path, monkeypatch):
    """A FAIL is a rework loop, not a crash and not an approval."""
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    init_run("run-01", "nano", root=runs)
    executor = FakeExecutor(scenario="verification-fail")
    result = None
    for _ in range(20):
        result = run_loop("run-01", executor, max_steps=80, root=runs)
        if result.code == 2:
            gate_approve(
                "run-01",
                gate_id=result.order["gate"]["id"],
                approver="human:test",
                evidence=["test approval"],
                root=runs,
            )
            continue
        break
    assert result is not None
    assert result.code == 0, result.message
    run_dir = runs / "run-01"
    events = read_events(run_dir / "blackbox.jsonl")
    verdicts = [
        (event.get("task_id"), event["verdict"]["status"])
        for event in events
        if event["event_type"] == "VERIFY"
    ]
    assert ("TASK-001", "FAIL") in verdicts
    assert any(
        event["event_type"] == "GATE_DECISION"
        and event["verdict"].get("reason_code") == "GATE_REJECTED"
        for event in events
    )
    assert any(event["event_type"] == "REWORK" for event in events)
    assert verify_run(run_dir, "s9_learn", require_gate=True) == []
    for stage in ("s15_quarantine", "s2_architecture", "s3_plan", "s4_build",
                  "s5_acceptance", "s6_security", "s8_docs"):
        assert any(
            event["event_type"] == "VERIFY" and event["stage"] == stage and not event.get("task_id")
            for event in events
        ), stage
