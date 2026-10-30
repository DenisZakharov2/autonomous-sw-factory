"""Invariants: gate approvers and task ownership are enforced, not advisory."""
import json

import pytest

from factory.engine import (
    gate_reject,
    import_plan,
    next_action,
    submit_work,
    task_accept,
    task_claim,
    task_reject,
    task_submit,
)
from factory.errors import ConfigError, RunError
from factory.state import get_task, load_state, reject_task, save_state

PLAN = (
    "# Plan\n## 1. Задачи\n"
    "| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Проверка |\n"
    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
    "| `TASK-001` | `12-fullstack-developer` | `REQ-001` | `-` | "
    "`product/src/todo.py` | `true` |\n"
    "## 2. Покрытие REQ\n`REQ-001`\n"
)


def _plan_run(run):
    (run / "artifacts" / "plan.md").write_text(PLAN, encoding="utf-8")
    import_plan(run.name, root=run.parent)


def _file(run, relative, text="x\n"):
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


ACCEPTED_BEFORE_BUILD = [
    "s00_intake", "s0_vision", "s1_spec", "s15_quarantine", "s2_architecture", "s3_plan",
]


def _advance_to(run, target):
    """Mark every earlier stage accepted so the target becomes current."""
    from factory.config import load_pipeline

    pipeline = load_pipeline()
    order = {stage["id"]: stage["order"] for stage in pipeline["stages"]}
    state = load_state(run)
    for stage in pipeline["stages"]:
        if stage["order"] < order[target]:
            state["stage_status"][stage["id"]] = "accepted"
            state["gate_status"][stage["gate"]["id"]]["status"] = "approved"
    state["stage_status"][target] = "in_progress"
    state["current_stage"] = target
    save_state(run, state)


def test_gate_reject_rejects_foreign_approver(nano_run):
    run, _, _ = nano_run
    with pytest.raises(RunError, match="requires a human approver"):
        gate_reject("run-01", gate_id="gate_s00", approver="03-product-owner",
                    evidence=["rogue"], root=run.parent)
    state = load_state(run)
    assert state["gate_status"]["gate_s00"]["status"] == "pending"
    assert state["stage_status"]["s00_intake"] == "pending"


def test_gate_reject_accepts_declared_human(nano_run):
    run, _, _ = nano_run
    event = gate_reject("run-01", gate_id="gate_s00", approver="human:auditor",
                        evidence=["brief rejected"], root=run.parent)
    assert event["verdict"]["status"] == "FAIL"
    state = load_state(run)
    assert state["gate_status"]["gate_s00"]["decided_by"] == "human:auditor"


def test_role_gate_reject_requires_the_declared_role(nano_run):
    run, _, _ = nano_run
    _advance_to(run, "s2_architecture")
    with pytest.raises(RunError, match="must be decided by 02-independent-verifier"):
        gate_reject("run-01", gate_id="gate_s2", approver="12-fullstack-developer",
                    evidence=["rogue"], root=run.parent)
    gate_reject("run-01", gate_id="gate_s2", approver="02-independent-verifier",
                evidence=["rework architecture"], root=run.parent)


def test_task_submit_rejects_foreign_owner(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    _advance_to(run, "s4_build")
    _file(run, "product/src/todo.py")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    with pytest.raises(RunError, match="is owned by 12-fullstack-developer"):
        task_submit("run-01", "TASK-001", "03-product-owner", ["product/src/todo.py"],
                    root=run.parent)
    assert get_task(load_state(run), "TASK-001")["status"] == "in_progress"


def test_task_submit_rejects_file_outside_allowed_files(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    _advance_to(run, "s4_build")
    _file(run, "artifacts/rogue.md")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    with pytest.raises(RunError, match="outside allowed_files"):
        task_submit("run-01", "TASK-001", "12-fullstack-developer", ["artifacts/rogue.md"],
                    root=run.parent)
    assert get_task(load_state(run), "TASK-001")["status"] == "in_progress"


def test_task_submit_accepts_allowed_file_and_deliverable(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    _advance_to(run, "s4_build")
    _file(run, "product/src/todo.py")
    _file(run, "tasks/TASK-001/deliverable.md")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    task_submit(
        "run-01", "TASK-001", "12-fullstack-developer",
        ["product/src/todo.py", "tasks/TASK-001/deliverable.md"], root=run.parent,
    )
    assert get_task(load_state(run), "TASK-001")["status"] == "review"


def test_submit_work_rejects_artifact_of_another_role(nano_run):
    run, _, _ = nano_run
    _file(run, "artifacts/interview-brief.md")
    with pytest.raises(RunError, match="is owned by 01-smart-interviewer"):
        submit_work("run-01", stage_id="s00_intake", role="02-independent-verifier",
                    artifacts=["artifacts/interview-brief.md"], root=run.parent)
    assert load_state(run)["stage_status"]["s00_intake"] == "pending"


def test_submit_work_accepts_owned_artifact(nano_run):
    run, _, _ = nano_run
    _file(run, "artifacts/interview-brief.md",
          "## 1. Цель (JTBD)\n\nЦель.\n## 2. Out-of-Scope\n\nНет.\n"
          "## 3. Требования\n\nТребование.\n## 4. Профиль\n\nПрофиль.\n")
    _file(run, "artifacts/GLOSSARY.md", "## Термины\n\nТермин.\n")
    submit_work("run-01", stage_id="s00_intake", role="01-smart-interviewer",
                artifacts=["artifacts/interview-brief.md", "artifacts/GLOSSARY.md"], root=run.parent)
    assert load_state(run)["stage_status"]["s00_intake"] == "review"


def test_submit_work_rejects_undeclared_artifact(nano_run):
    run, _, _ = nano_run
    _file(run, "artifacts/random.md")
    with pytest.raises(RunError, match="is not declared for stage s00_intake"):
        submit_work("run-01", stage_id="s00_intake", role="01-smart-interviewer",
                    artifacts=["artifacts/random.md"], root=run.parent)


@pytest.mark.parametrize("task_id", ["task-001", "../TASK-001", "TASK_001", ""])
def test_task_commands_validate_task_id(nano_run, task_id):
    run, _, _ = nano_run
    with pytest.raises(RunError, match="task_id must match"):
        task_accept("run-01", task_id, root=run.parent)
    with pytest.raises(RunError, match="task_id must match"):
        task_reject("run-01", task_id, "no", root=run.parent)


def test_task_reject_records_reason(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    _advance_to(run, "s4_build")
    _file(run, "product/src/todo.py")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    task_submit("run-01", "TASK-001", "12-fullstack-developer", ["product/src/todo.py"],
                root=run.parent)
    task_reject("run-01", "TASK-001", "test missing", root=run.parent)
    task = get_task(load_state(run), "TASK-001")
    assert task["status"] == "pending"
    assert task["reject_reasons"] == ["test missing"]
    json.dumps(task)  # the state schema accepts the recorded reason list


def test_gate_reject_returns_to_rework_stage(nano_run):
    run, _, _ = nano_run
    state = load_state(run)
    for stage_id in [*ACCEPTED_BEFORE_BUILD, "s4_build"]:
        state["stage_status"][stage_id] = "accepted"
    state["stage_status"]["s5_acceptance"] = "review"
    state["gate_status"]["gate_s4"]["status"] = "approved"
    save_state(run, state)

    event = gate_reject("run-01", gate_id="gate_s5", approver="02-independent-verifier",
                        evidence=["acceptance has a FAIL row"], root=run.parent)

    state = load_state(run)
    assert state["stage_status"]["s4_build"] == "in_progress"
    assert state["stage_status"]["s5_acceptance"] == "pending"
    assert state["gate_status"]["gate_s4"]["status"] == "pending"
    assert state["gate_status"]["gate_s5"]["status"] == "rejected"
    assert state["current_stage"] == "s4_build"
    assert "rework returned to s4_build" in event["action"]


def test_gate_reject_from_security_returns_to_build(nano_run):
    run, _, _ = nano_run
    state = load_state(run)
    for stage_id in [*ACCEPTED_BEFORE_BUILD, "s4_build", "s5_acceptance"]:
        state["stage_status"][stage_id] = "accepted"
    state["stage_status"]["s6_security"] = "review"
    save_state(run, state)

    gate_reject("run-01", gate_id="gate_s6", approver="02-independent-verifier",
                evidence=["path traversal found"], root=run.parent)

    state = load_state(run)
    assert state["stage_status"]["s4_build"] == "in_progress"
    assert state["stage_status"]["s5_acceptance"] == "pending"
    assert state["stage_status"]["s6_security"] == "pending"
    assert state["current_stage"] == "s4_build"


def test_gate_reject_escalates_after_max_attempts(nano_run):
    run, _, _ = nano_run
    for attempt in range(3):
        gate_reject("run-01", gate_id="gate_s00", approver="human:reviewer",
                    evidence=[f"reject {attempt}"], root=run.parent)

    state = load_state(run)
    assert state["stage_status"]["s00_intake"] == "escalated"
    assert state["veto_counters"]["s00_intake"] == 1

    order = next_action("run-01", root=run.parent)
    assert order["action"] == "blocked"
    assert order["stage"] == "s00_intake"
    assert "escalated" in order["instructions"]
    assert order["next_command"].startswith("factory gate accept-risk")


def test_escalated_task_blocks_the_build_stage(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    _advance_to(run, "s4_build")
    _file(run, "product/src/todo.py")
    for attempt in range(3):
        task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
        task_submit("run-01", "TASK-001", "12-fullstack-developer", ["product/src/todo.py"],
                    root=run.parent)
        task_reject("run-01", "TASK-001", f"attempt {attempt}", root=run.parent)

    state = load_state(run)
    assert state["tasks"]["TASK-001"]["status"] == "escalated"
    for stage_id in ACCEPTED_BEFORE_BUILD:
        state["stage_status"][stage_id] = "accepted"
    state["stage_status"]["s4_build"] = "in_progress"
    save_state(run, state)

    order = next_action("run-01", root=run.parent)
    assert order["action"] == "blocked"
    assert order["task_id"] == "TASK-001"
    assert "escalated" in order["instructions"]


def test_blocked_stage_does_not_report_produce(nano_run):
    run, _, _ = nano_run
    state = load_state(run)
    state["stage_status"]["s00_intake"] = "blocked"
    save_state(run, state)
    order = next_action("run-01", root=run.parent)
    assert order["action"] == "blocked"
    assert order["artifacts"] == []


def test_next_action_validates_its_work_order(monkeypatch, nano_run):
    run, _, _ = nano_run
    monkeypatch.setattr(
        "factory.engine._next_action",
        lambda run_id, root=None: {"action": "produce"},
    )
    with pytest.raises(ConfigError, match="work order"):
        next_action("run-01", root=run.parent)


def test_task_attempt_limit_is_configurable(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    state = load_state(run)
    reject_task(state, "TASK-001", "first", max_attempts=2)
    assert get_task(state, "TASK-001")["status"] == "pending"
    reject_task(state, "TASK-001", "second", max_attempts=2)
    assert get_task(state, "TASK-001")["status"] == "escalated"


def test_task_default_attempt_limit_matches_budget(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    state = load_state(run)
    for attempt in range(2):
        reject_task(state, "TASK-001", f"attempt {attempt}")
    assert get_task(state, "TASK-001")["status"] == "pending"
    reject_task(state, "TASK-001", "last")
    assert get_task(state, "TASK-001")["status"] == "escalated"
