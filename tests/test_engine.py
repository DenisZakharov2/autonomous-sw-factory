"""Invariants: gate approvers and task ownership are enforced, not advisory."""
import json

import pytest

from factory.engine import (
    gate_reject,
    import_plan,
    submit_work,
    task_accept,
    task_claim,
    task_reject,
    task_submit,
)
from factory.errors import RunError
from factory.state import get_task, load_state

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
    with pytest.raises(RunError, match="must be decided by 02-independent-verifier"):
        gate_reject("run-01", gate_id="gate_s2", approver="12-fullstack-developer",
                    evidence=["rogue"], root=run.parent)
    gate_reject("run-01", gate_id="gate_s2", approver="02-independent-verifier",
                evidence=["rework architecture"], root=run.parent)


def test_task_submit_rejects_foreign_owner(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    _file(run, "product/src/todo.py")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    with pytest.raises(RunError, match="is owned by 12-fullstack-developer"):
        task_submit("run-01", "TASK-001", "03-product-owner", ["product/src/todo.py"],
                    root=run.parent)
    assert get_task(load_state(run), "TASK-001")["status"] == "in_progress"


def test_task_submit_rejects_file_outside_allowed_files(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
    _file(run, "artifacts/rogue.md")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    with pytest.raises(RunError, match="outside allowed_files"):
        task_submit("run-01", "TASK-001", "12-fullstack-developer", ["artifacts/rogue.md"],
                    root=run.parent)
    assert get_task(load_state(run), "TASK-001")["status"] == "in_progress"


def test_task_submit_accepts_allowed_file_and_deliverable(nano_run):
    run, _, _ = nano_run
    _plan_run(run)
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
    _file(run, "artifacts/interview-brief.md")
    submit_work("run-01", stage_id="s00_intake", role="01-smart-interviewer",
                artifacts=["artifacts/interview-brief.md"], root=run.parent)
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
    _file(run, "product/src/todo.py")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    task_submit("run-01", "TASK-001", "12-fullstack-developer", ["product/src/todo.py"],
                root=run.parent)
    task_reject("run-01", "TASK-001", "test missing", root=run.parent)
    task = get_task(load_state(run), "TASK-001")
    assert task["status"] == "pending"
    assert task["reject_reasons"] == ["test missing"]
    json.dumps(task)  # the state schema accepts the recorded reason list
