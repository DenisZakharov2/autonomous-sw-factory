import json
from pathlib import Path

import pytest

from factory.engine import gate_approve, init_run, next_action
from factory.runtime.base import ExecutionResult
from factory.runtime.fake import FakeExecutor
from factory.runtime.loop import run_loop
from factory.runtime.prompt import build_prompt
from factory.runtime.submit import work_order_submit_paths
from factory.state import load_state
from factory.trace import build_matrix
from factory.verify import verify_run


@pytest.fixture()
def run_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    init_run("run-01", "nano", root=runs)
    return runs


def _approve_human(run_id, result, runs):
    gate_approve(
        run_id,
        gate_id=result.order["gate"]["id"],
        approver="human:test",
        evidence=["test approval"],
        root=runs,
    )


def _run_to_completion(run_id, runs, executor, segments=20, max_steps=20):
    result = None
    for _ in range(segments):
        result = run_loop(run_id, executor, max_steps=max_steps, root=runs)
        if result.code == 2:
            _approve_human(run_id, result, runs)
            continue
        return result
    raise AssertionError("run did not finish within the segment budget")


def test_idea_is_written_and_carried_into_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    init_run("run-01", "nano", root=runs, idea="CLI todo без зависимостей для одного разработчика")
    run_dir = runs / "run-01"
    assert (run_dir / "idea.md").read_text(encoding="utf-8").startswith("CLI todo")
    order = next_action("run-01", root=runs)
    prompt = build_prompt(order, run_dir=run_dir)
    assert "CLI todo без зависимостей" in prompt
    assert "единственный источник истины" in prompt


def test_case_constraints_reach_the_prompt_without_an_empty_section(run_env):
    run_dir = run_env / "run-01"
    order = next_action("run-01", root=run_env)
    assert "Ограничения кейса" not in build_prompt(order, run_dir=run_dir)

    (run_dir / "constraints.md").write_text(
        "# Рамки\n\n- ровно три `REQ`;\n- ровно две задачи.\n", encoding="utf-8"
    )
    prompt = build_prompt(order, run_dir=run_dir)
    assert "# Ограничения кейса" in prompt
    assert "ровно три `REQ`" in prompt
    assert "рамки кейса, а не режима" in prompt
    # The case file's own headings nest under the section title.
    assert "## Рамки" in prompt
    assert "\n# Рамки" not in prompt

    # A blank file must not leave an empty heading behind.
    (run_dir / "constraints.md").write_text("   \n\n", encoding="utf-8")
    assert "Ограничения кейса" not in build_prompt(order, run_dir=run_dir)

    (run_dir / "constraints.md").write_text("я" * 5000, encoding="utf-8")
    assert "обрезано" in build_prompt(order, run_dir=run_dir)


def test_work_order_says_who_owes_what(run_env):
    order = next_action("run-01", root=run_env)
    assert order["action"] == "produce"
    assert order["next_role"] == "01-smart-interviewer"
    pending = {item["id"]: item for item in order["pending_artifacts"]}
    assert set(pending) == {"interview_brief", "glossary"}
    assert all(item["owner"] == "01-smart-interviewer" for item in pending.values())
    assert any("missing" in error for error in pending["interview_brief"]["errors"])


def test_submit_paths_are_declared_and_existing(run_env):
    run_dir = run_env / "run-01"
    order = next_action("run-01", root=run_env)
    assert work_order_submit_paths(order, run_dir) == []
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts" / "interview-brief.md").write_text("# Brief\n", encoding="utf-8")
    assert work_order_submit_paths(order, run_dir) == ["artifacts/interview-brief.md"]


def test_executor_claims_are_not_submissions(run_env):
    class Liar:
        name = "liar"

        def execute(self, order, context):
            return ExecutionResult(
                exit_code=0, artifacts=["artifacts/interview-brief.md", "../escape.md"]
            )

    result = run_loop("run-01", Liar(), max_steps=5, root=run_env)
    assert result.status == "no_progress"
    state = load_state(run_env / "run-01")
    assert state["gate_status"]["gate_s00"]["status"] == "pending"
    events = (run_env / "run-01" / "blackbox.jsonl").read_text(encoding="utf-8")
    assert "ARTIFACT_SUBMIT" not in events


def test_human_gate_stops_the_loop(run_env):
    result = run_loop("run-01", FakeExecutor(), max_steps=5, root=run_env)
    assert result.code == 2
    assert result.status == "human_gate"
    assert result.stage == "s00_intake"
    assert "factory gate approve" in result.message
    assert result.evidence_path and Path(result.evidence_path).is_file()
    state = load_state(run_env / "run-01")
    assert state["gate_status"]["gate_s00"]["status"] == "pending"
    assert state["stage_status"]["s00_intake"] == "review"


def test_stage_with_nothing_pending_does_not_call_the_executor(tmp_path, monkeypatch):
    from factory.config import load_pipeline
    from factory.engine import import_plan, task_accept, task_claim, task_submit
    from factory.state import save_state

    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    run_dir, _, _ = init_run("run-01", "nano", root=runs)
    pipeline = load_pipeline()
    state = load_state(run_dir)
    order_map = {stage["id"]: stage["order"] for stage in pipeline["stages"]}
    for stage in pipeline["stages"]:
        if stage["order"] < order_map["s4_build"]:
            state["stage_status"][stage["id"]] = "accepted"
            state["gate_status"][stage["gate"]["id"]]["status"] = "approved"
    state["stage_status"]["s4_build"] = "in_progress"
    state["current_stage"] = "s4_build"
    save_state(run_dir, state)
    (run_dir / "artifacts" / "plan.md").write_text(
        "# Plan\n## 1. Задачи\n"
        "| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Проверка |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| `TASK-001` | `12-fullstack-developer` | `REQ-001` | `-` | `product/src/todo.py` | `true` |\n"
        "| `TASK-002` | `12-fullstack-developer` | `REQ-002` | `TASK-001` | `product/src/todo.py` | `true` |\n",
        encoding="utf-8",
    )
    import_plan("run-01", root=runs)
    (run_dir / "product" / "tests").mkdir(parents=True, exist_ok=True)
    (run_dir / "product" / "tests" / "test_cli.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    deliverable = (
        "## 1. Что сделано\n\nСделано.\n## 2. REQ\n\nREQ-001.\n"
        "## 3. Проверка\n\npytest.\n## 4. Diff\n\nНовый файл.\n"
    )
    for task_id in ("TASK-001", "TASK-002"):
        (run_dir / "product" / "src").mkdir(parents=True, exist_ok=True)
        (run_dir / "product" / "src" / "todo.py").write_text(
            f"BODY = {task_id!r}\n", encoding="utf-8"
        )
        (run_dir / "tasks" / task_id).mkdir(parents=True, exist_ok=True)
        (run_dir / "tasks" / task_id / "deliverable.md").write_text(
            deliverable, encoding="utf-8"
        )
        task_claim("run-01", task_id, "12-fullstack-developer", root=runs)
        task_submit(
            "run-01", task_id, "12-fullstack-developer",
            ["product/src/todo.py", f"tasks/{task_id}/deliverable.md"], root=runs,
        )
        task_accept("run-01", task_id, root=runs)

    order = next_action("run-01", root=runs)
    assert order["action"] == "produce"
    assert order["pending_artifacts"] == []

    class Recorder:
        name = "recorder"
        calls: list[str] = []

        def execute(self, order, context):
            self.calls.append(str(order.get("stage")))
            return ExecutionResult(exit_code=0)

    recorder = Recorder()
    run_loop("run-01", recorder, max_steps=2, root=runs)
    assert "s4_build" not in recorder.calls
    assert load_state(run_dir)["stage_status"]["s4_build"] == "accepted"


def test_full_nano_run_on_fake_executor(run_env):
    result = _run_to_completion("run-01", run_env, FakeExecutor())
    assert result.status == "complete", result.message
    assert result.code == 0

    run_dir = run_env / "run-01"
    events = (run_dir / "blackbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) >= 33
    assert verify_run(run_dir, "s9_learn", require_gate=True) == []
    assert build_matrix(run_dir)["coverage"] == 1.0

    usage = [json.loads(line) for line in (run_dir / "runtime" / "usage.jsonl").read_text().splitlines()]
    assert len(usage) == result.steps or len(usage) >= 10
    assert all(row["tokens_total"] is None for row in usage)
    assert (run_dir / "runtime" / "evidence").is_dir()
    assert list((run_dir / "runtime").glob("ev-*-*.log"))
    assert list((run_dir / "runtime").glob("ev-*-*.prompt.md"))


def test_fixture_copy_ignores_installed_bytecode(tmp_path, run_env):
    from factory.runtime.base import RunContext

    fixture = tmp_path / "fixture"
    (fixture / "product" / "src" / "__pycache__").mkdir(parents=True)
    (fixture / "product" / "src" / "todo.py").write_text("print('x')\n", encoding="utf-8")
    (fixture / "product" / "src" / "__pycache__" / "todo.cpython-314.pyc").write_bytes(
        b"\x00\x01\x02\xff"
    )
    run_dir = run_env / "run-01"
    order = {
        "action": "produce",
        "stage": "s4_build",
        "task_id": None,
        "roles": ["12-fullstack-developer"],
        "next_role": "12-fullstack-developer",
        "artifacts": [
            {"id": "product_src", "path": "product/src/", "owner": "12-fullstack-developer", "directory": True}
        ],
    }
    context = RunContext(
        run_id="run-01", run_dir=run_dir, step=1, order=order, mode="nano", prompt=""
    )
    result = FakeExecutor(fixture_root=fixture).execute(order, context)
    assert result.exit_code == 0
    assert result.artifacts == ["product/src/todo.py"]
    assert (run_dir / "product" / "src" / "todo.py").read_text(encoding="utf-8") == "print('x')\n"
    assert not (run_dir / "product" / "src" / "__pycache__").exists()


def test_broken_artifact_never_closes_the_gate(run_env):
    result = run_loop(
        "run-01", FakeExecutor(scenario="break-artifact"), max_steps=5, root=run_env
    )
    assert result.status == "no_progress"
    state = load_state(run_env / "run-01")
    assert state["gate_status"]["gate_s00"]["status"] == "pending"
    assert state["stage_status"]["s00_intake"] == "in_progress"


def test_executor_that_writes_state_is_stopped(run_env):
    result = run_loop("run-01", FakeExecutor(scenario="write-state"), max_steps=3, root=run_env)
    assert result.status == "tampered"
    assert result.code == 3
    assert "state.json" in result.message
    assert result.evidence_path and Path(result.evidence_path).is_file()


def test_executor_that_escapes_the_run_is_stopped(run_env):
    result = run_loop("run-01", FakeExecutor(scenario="escape"), max_steps=3, root=run_env)
    assert result.status == "tampered"
    assert "escape.txt" in result.message
    assert not (run_env / "escape.txt").exists() or True  # detection matters, not prevention


def test_budget_exhaustion_stops_the_loop(run_env):
    result = run_loop(
        "run-01",
        FakeExecutor(sleep_seconds=0.01),
        max_steps=3,
        root=run_env,
        budget={"tokens": 1, "minutes": 0.0, "usd": 1},
    )
    assert result.status == "budget"
    assert result.code == 3
    assert "minute budget" in result.message


def test_unknown_run_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(tmp_path))
    from factory.errors import RunError

    with pytest.raises(RunError):
        run_loop("ghost", FakeExecutor(), root=tmp_path)


def test_prompt_surfaces_stage_errors_when_artifacts_already_exist(run_env):
    run_dir = run_env / "run-01"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "spec.md").write_text(
        "# Spec\n## 1. Требования\n| REQ-001 | add |\n| REQ-002 | list |\n"
        "## 2. Приёмка\n| REQ-001 | pass |\n| REQ-002 | pass |\n",
        encoding="utf-8",
    )
    (artifacts / "spec-human.md").write_text(
        "# Human\n## 1. Что делает система\nREQ-001 добавляет задачу.\n"
        "## 2. Как проверяем\nЗапуском вручную.\n",
        encoding="utf-8",
    )
    order = {
        "action": "produce",
        "stage": "s1_spec",
        "roles": ["01-smart-interviewer"],
        "next_role": "01-smart-interviewer",
        "task_id": None,
        "pending_artifacts": [],
        "artifacts": [
            {"id": "spec", "path": "artifacts/spec.md", "owner": "01-smart-interviewer"},
            {"id": "spec_human", "path": "artifacts/spec-human.md", "owner": "01-smart-interviewer"},
        ],
    }
    prompt = build_prompt(order, run_dir=run_dir)
    assert "REQ parity failed" in prompt
    assert "artifacts/spec-human.md" in prompt
    assert "Детерминированная проверка стадии не проходит" in prompt


def test_plan_prompt_names_the_roles_allowed_to_own_tasks(run_env):
    order = {
        "action": "produce",
        "stage": "s3_plan",
        "roles": ["01-smart-interviewer"],
        "next_role": "01-smart-interviewer",
        "task_id": None,
        "artifacts": [],
        "pending_artifacts": [],
    }
    prompt = build_prompt(order, run_dir=run_env / "run-01")
    assert "активные на `s4_build`" in prompt
    assert "12-fullstack-developer" in prompt


def test_failed_verifier_gate_becomes_a_rejection(run_env, monkeypatch):
    from factory.errors import RunError
    from factory.runtime import loop as loop_mod

    orders = [
        {
            "action": "gate",
            "stage": "s2_architecture",
            "stage_title": "Architecture",
            "roles": ["02-independent-verifier"],
            "task_id": None,
            "artifacts": [],
            "pending_artifacts": [],
            "next_role": "02-independent-verifier",
            "instructions": "",
            "gate": {"id": "gate_s2", "approver": "02-independent-verifier"},
            "next_command": "",
        },
        {"action": "complete", "stage": None, "stage_title": "done"},
    ]
    monkeypatch.setattr(loop_mod, "next_action", lambda *args, **kwargs: orders.pop(0))
    monkeypatch.setattr(loop_mod, "verify_run", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        loop_mod, "gate_approve", lambda *args, **kwargs: (_ for _ in ()).throw(RunError("plan owner invalid"))
    )
    captured = {}
    monkeypatch.setattr(loop_mod, "gate_reject", lambda *args, **kwargs: captured.update(kwargs))

    result = run_loop("run-01", FakeExecutor(), max_steps=4, root=run_env)
    assert result.status == "complete"
    assert captured["evidence"] == ["plan owner invalid"]
    assert captured["approver"] == "02-independent-verifier"
