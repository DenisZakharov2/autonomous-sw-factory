"""A verdict must still be about the bytes it judged.

`AGENTS.md` §2.8 says only `02-independent-verifier` may record a `PASS`.
The journal could hold that rule and still let a producer close a gate on a
verdict that was written before the producer changed the work: the report was
valid, the verdict was `PASS`, and the role was the verifier — but it said
nothing about the code that ended up in the repository.

These tests pin the two doors that matter:

* a task whose work changed after the report was written is not accepted on
  that report;
* a stage whose artifacts changed after the report was handed in does not
  close its gate on that report.

They also pin the two cases that must keep working: resubmitting the very
same bytes is not a change, and a report the verifier wrote after the change
is a new verdict about the new bytes.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import write_stage_verification, write_task_verification

from factory.config import load_pipeline
from factory.engine import (
    gate_approve,
    import_plan,
    init_run,
    submit_work,
    task_accept,
    task_claim,
    task_reject,
    task_submit,
)
from factory.errors import VerificationError
from factory.state import load_state, save_state

VERIFIER = "02-independent-verifier"
ROLE = "12-fullstack-developer"

PLAN = (
    "# Plan\n## 1. Задачи\n"
    "| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Проверка |\n"
    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
    "| `TASK-001` | `12-fullstack-developer` | `REQ-001` | `-` | `product/src/todo.py` | `true` |\n"
)
DELIVERABLE = (
    "## 1. Что сделано\n\nСделано.\n"
    "## 2. REQ\n\nREQ-001.\n"
    "## 3. Проверка\n\npytest.\n"
    "## 4. Diff\n\nНовый файл.\n"
)
ARCHITECTURE = "## 1. Компоненты\n\nКомпонент.\n## 2. REQ mapping\n\nREQ-001.\n## 3. Решения\n\nADR.\n"
ARCHITECTURE_SPECS = {
    "artifacts/architecture.md": ARCHITECTURE,
    "artifacts/api-contract.md": "## 1. Эндпоинты\n\nGET /tasks.\n## 2. Модели\n\nTask.\n## 3. Ошибки\n\n400.\n",
    "artifacts/data-model.md": "## 1. Таблицы\n\ntasks.\n## 2. Индексы\n\nid.\n## 3. Миграции\n\n001.\n",
    "artifacts/threat-model.md": "## 1. Активы\n\nДанные.\n## 2. Векторы\n\nВвод.\n## 3. Контрмеры\n\nВалидация.\n",
    "artifacts/adr/ADR-1.md": "## Контекст\n\nКонтекст.\n## Решение\n\nРешение.\n## Последствия\n\nПоследствия.\n",
    "artifacts/ux-flows.md": "## 1. Экраны\n\nЭкран.\n## 2. Потоки\n\nПоток.\n## 3. Состояния\n\nСостояние.\n",
    "artifacts/ai-design.md": "## 1. Промпты\n\nПромпт.\n## 2. Валидация\n\nВалидация.\n## 3. Fallback\n\nFallback.\n## 4. Eval\n\nEval.\n",
    "artifacts/screens/todo.md": "## 1. Экраны\n\nСписок задач.\n",
}


def _write(run: Path, relative: str, text: str) -> Path:
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _advance_to(run: Path, target: str) -> None:
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


@pytest.fixture()
def build_run(tmp_path: Path) -> Path:
    run_dir, _, _ = init_run("run-01", "nano", root=tmp_path)
    _advance_to(run_dir, "s4_build")
    _write(run_dir, "artifacts/plan.md", PLAN)
    import_plan("run-01", root=tmp_path)
    _write(run_dir, "product/tests/test_cli.py", "def test_ok():\n    assert True\n")
    return run_dir


def _submit_task(run: Path, body: str) -> None:
    _write(run, "product/src/todo.py", body)
    _write(run, "tasks/TASK-001/deliverable.md", DELIVERABLE)
    task_claim("run-01", "TASK-001", ROLE, root=run.parent)
    task_submit(
        "run-01", "TASK-001", ROLE,
        ["product/src/todo.py", "tasks/TASK-001/deliverable.md"],
        root=run.parent,
    )


def test_task_accept_refuses_a_verdict_that_predates_the_change(build_run: Path):
    """The producer changed the code after the report: the report says nothing about it."""
    run = build_run
    _submit_task(run, "TASKS = []\n")
    write_task_verification(run, "TASK-001", verdict="PASS")

    # The producer takes the task back and changes the work.
    task_reject("run-01", "TASK-001", "wrong shape", root=run.parent)
    _submit_task(run, "TASKS = []\n\n\ndef add(title):\n    return title\n")

    with pytest.raises(VerificationError) as failure:
        task_accept("run-01", "TASK-001", root=run.parent)
    assert "predates the last submission" in str(failure.value)
    assert load_state(run)["tasks"]["TASK-001"]["status"] == "review"


def test_task_accept_takes_a_report_written_after_the_change(build_run: Path):
    """A report the verifier wrote after the change is a new verdict about the new bytes."""
    run = build_run
    _submit_task(run, "TASKS = []\n")
    write_task_verification(run, "TASK-001", verdict="PASS")

    task_reject("run-01", "TASK-001", "wrong shape", root=run.parent)
    _submit_task(run, "TASKS = []\n\n\ndef add(title):\n    return title\n")
    # The verifier looks again and writes a different report.
    write_task_verification(run, "TASK-001", verdict="PASS", reason="re-checked after the fix")

    task_accept("run-01", "TASK-001", root=run.parent)
    assert load_state(run)["tasks"]["TASK-001"]["status"] == "accepted"


def test_task_accept_is_not_blocked_by_an_unchanged_resubmission(build_run: Path):
    """Resubmitting the very same bytes is not a change: the verdict still holds."""
    run = build_run
    _submit_task(run, "TASKS = []\n")
    write_task_verification(run, "TASK-001", verdict="PASS")

    task_reject("run-01", "TASK-001", "wrong shape", root=run.parent)
    _submit_task(run, "TASKS = []\n")  # the same bytes as the first submission

    task_accept("run-01", "TASK-001", root=run.parent)
    assert load_state(run)["tasks"]["TASK-001"]["status"] == "accepted"


@pytest.fixture()
def architecture_run(tmp_path: Path) -> Path:
    run_dir, _, _ = init_run("run-01", "nano", root=tmp_path)
    _advance_to(run_dir, "s2_architecture")
    # The report exists before the producer submits: a role-verified stage
    # cannot reach `review` without it.
    write_stage_verification(run_dir, "s2_architecture", verdict="PASS")
    return run_dir


def _submit_architecture(run: Path, changed: bool = False) -> None:
    paths: list[str] = []
    for relative, text in ARCHITECTURE_SPECS.items():
        if relative == "artifacts/architecture.md" and changed:
            text = text + "\nДобавлено после проверки.\n"
        _write(run, relative, text)
        if not relative.startswith("artifacts/screens/"):
            paths.append(relative)
    paths.append("artifacts/screens")
    submit_work(
        "run-01", stage_id="s2_architecture", role=ROLE,
        artifacts=paths, root=run.parent,
    )


def test_gate_refuses_a_verdict_that_predates_the_change(architecture_run: Path):
    """The producer changed the stage after the report was handed in."""
    run = architecture_run
    _submit_architecture(run)
    submit_work(
        "run-01", stage_id="s2_architecture", role=VERIFIER,
        artifacts=["artifacts/verification/s2_architecture/verification.md"], root=run.parent,
    )

    # The producer changes the stage and resubmits it.
    _submit_architecture(run, changed=True)

    with pytest.raises(VerificationError) as failure:
        gate_approve(
            "run-01", gate_id="gate_s2", approver=VERIFIER,
            evidence=["arch ok"], root=run.parent,
        )
    assert "predates the last submission" in str(failure.value)
    assert load_state(run)["stage_status"]["s2_architecture"] != "accepted"


def test_gate_needs_the_report_to_be_handed_in(architecture_run: Path):
    """A report nobody submitted is a rumour, not a witness."""
    run = architecture_run
    _submit_architecture(run)

    with pytest.raises(VerificationError):
        gate_approve(
            "run-01", gate_id="gate_s2", approver=VERIFIER,
            evidence=["arch ok"], root=run.parent,
        )
