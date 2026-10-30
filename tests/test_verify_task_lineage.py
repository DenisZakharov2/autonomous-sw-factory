"""`gate_s4` binding: a dependent task may rewrite a shared artifact.

`nano` assigns `product/src/todo.py` to both `TASK-001` and its dependent
`TASK-002`.  The journal therefore records the same path twice with different
hashes.  Only the newest record may bind the file — but a real overwrite after
the last recorded submission must still be caught.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from factory.config import load_pipeline
from factory.engine import (
    import_plan,
    init_run,
    task_accept,
    task_claim,
    task_submit,
)
from factory.state import load_state, save_state
from factory.verify import verify_run

PLAN = (
    "# Plan\n## 1. Задачи\n"
    "| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Проверка |\n"
    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
    "| `TASK-001` | `12-fullstack-developer` | `REQ-001` | `-` | `product/src/todo.py` | `true` |\n"
    "| `TASK-002` | `12-fullstack-developer` | `REQ-002` | `TASK-001` | `product/src/todo.py` | `true` |\n"
)
DELIVERABLE = (
    "## 1. Что сделано\n\nСделано.\n"
    "## 2. REQ\n\nREQ-001.\n"
    "## 3. Проверка\n\npytest.\n"
    "## 4. Diff\n\nНовый файл.\n"
)


@pytest.fixture()
def s4_run(tmp_path: Path) -> tuple[Path, str]:
    run_dir, _, _ = init_run("run-01", "nano", root=tmp_path)
    pipeline = load_pipeline()
    state = load_state(run_dir)
    order = {stage["id"]: stage["order"] for stage in pipeline["stages"]}
    for stage in pipeline["stages"]:
        if stage["order"] < order["s4_build"]:
            state["stage_status"][stage["id"]] = "accepted"
            state["gate_status"][stage["gate"]["id"]]["status"] = "approved"
    state["stage_status"]["s4_build"] = "in_progress"
    state["current_stage"] = "s4_build"
    save_state(run_dir, state)
    _write(run_dir, "artifacts/plan.md", PLAN)
    import_plan("run-01", root=tmp_path)
    _write(run_dir, "product/tests/test_cli.py", "def test_ok():\n    assert True\n")
    return run_dir, "12-fullstack-developer"


def _write(run: Path, relative: str, text: str) -> Path:
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _accept_task(run: Path, task_id: str, role: str, body: str) -> None:
    _write(run, "product/src/todo.py", body)
    _write(run, f"tasks/{task_id}/deliverable.md", DELIVERABLE)
    task_claim("run-01", task_id, role, root=run.parent)
    task_submit(
        "run-01", task_id, role,
        ["product/src/todo.py", f"tasks/{task_id}/deliverable.md"],
        root=run.parent,
    )
    task_accept("run-01", task_id, root=run.parent)


def test_dependent_task_may_rewrite_the_shared_file(s4_run):
    run, role = s4_run
    _accept_task(run, "TASK-001", role, "TASKS = []\n")
    _accept_task(run, "TASK-002", role, "TASKS = []\n\n\ndef add(title):\n    TASKS.append(title)\n")
    assert verify_run(run, "s4_build") == []


def test_file_changed_after_the_last_submission_is_still_tampering(s4_run):
    run, role = s4_run
    _accept_task(run, "TASK-001", role, "TASKS = []\n")
    _accept_task(run, "TASK-002", role, "TASKS = []\n\n\ndef add(title):\n    TASKS.append(title)\n")
    _write(run, "product/src/todo.py", "TASKS = []\n\n\ndef add(title):\n    return title\n")
    errors = verify_run(run, "s4_build")
    assert any(
        "tampered after submission/approval: product/src/todo.py" in error
        for error in errors
    )


def test_rejected_task_does_not_mask_the_accepted_version(s4_run):
    run, role = s4_run
    _accept_task(run, "TASK-001", role, "TASKS = []\n")
    _write(run, "product/src/todo.py", "TASKS = ['draft']\n")
    _write(run, "tasks/TASK-002/deliverable.md", DELIVERABLE)
    task_claim("run-01", "TASK-002", role, root=run.parent)
    task_submit(
        "run-01", "TASK-002", role,
        ["product/src/todo.py", "tasks/TASK-002/deliverable.md"],
        root=run.parent,
    )
    from factory.engine import task_reject

    task_reject("run-01", "TASK-002", "wrong shape", root=run.parent)
    errors = verify_run(run, "s4_build")
    assert any(
        "tampered after submission/approval: product/src/todo.py" in error
        for error in errors
    )
