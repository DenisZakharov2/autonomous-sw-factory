import json
import threading
import time
from pathlib import Path

import pytest

from factory.config import load_pipeline
from factory.engine import init_run, task_accept
from factory.errors import FactoryError
from factory.runtime.base import ExecutionResult
from factory.runtime.loop import run_loop
from factory.runtime.worktree import (
    normalise_allowed,
    parallel_conflicts,
    paths_overlap,
    run_parallel_tasks,
)
from factory.state import add_task, find_stage, load_state, save_state, stage_gate
from factory.verify import verify_run

OWNER = "12-fullstack-developer"


def _task(task_id: str, files: list[str], deps: list[str] | None = None) -> dict:
    return {
        "task_id": task_id,
        "stage": "s4_build",
        "owner": OWNER,
        "reqs": ["REQ-001"],
        "deps": deps or [],
        "allowed_files": files,
        "status": "pending",
        "attempts": 0,
        "artifacts": [],
        "lease": None,
    }


@pytest.fixture()
def build_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    init_run("run-01", "standard", root=runs)
    run_dir = runs / "run-01"
    pipeline = load_pipeline()
    state = load_state(run_dir)
    for stage in pipeline["stages"]:
        if stage["id"] == "s4_build":
            break
        state["stage_status"][stage["id"]] = "accepted"
        stage_gate(state, stage)["status"] = "approved"
    state["stage_status"]["s4_build"] = "in_progress"
    add_task(state, _task("TASK-A", ["product/src/a.py"]))
    add_task(state, _task("TASK-B", ["product/src/b.py"]))
    save_state(run_dir, state)
    return runs, run_dir, find_stage(pipeline, "s4_build")


class RecordingExecutor:
    name = "recording"

    def __init__(self, *, sleep: float = 0.2, fail: set[str] | None = None) -> None:
        self.sleep = sleep
        self.fail = fail or set()
        self.starts: dict[str, float] = {}
        self.ends: dict[str, float] = {}
        self.worktrees: dict[str, str] = {}
        self.extras: dict[str, tuple[str, ...]] = {}
        self.lock = threading.Lock()

    def execute(self, order, context):
        task_id = str(context.task["task_id"])
        with self.lock:
            self.starts[task_id] = time.monotonic()
            self.worktrees[task_id] = str(context.run_dir)
            self.extras[task_id] = tuple(context.extra_read_write)
        try:
            if task_id in self.fail:
                raise RuntimeError(f"executor failed for {task_id}")
            time.sleep(self.sleep)
            for relative in context.task["allowed_files"]:
                target = context.run_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"# {task_id}\n", encoding="utf-8")
            deliverable = context.run_dir / "tasks" / task_id / "deliverable.md"
            deliverable.parent.mkdir(parents=True, exist_ok=True)
            deliverable.write_text(
                "# Отчёт\n## 1. Что сделано\nok\n## 2. REQ\nREQ-001\n"
                "## 3. Проверка\nok\n## 4. Diff\nok\n",
                encoding="utf-8",
            )
            return ExecutionResult(exit_code=0, stdout="ok", usage={"tokens_total": 10})
        finally:
            with self.lock:
                self.ends[task_id] = time.monotonic()


def _tasks(run_dir: Path) -> dict:
    return load_state(run_dir)["tasks"]


def test_overlapping_allowed_files_are_a_rejection_not_a_fallback():
    assert paths_overlap("product/src/a.py", "product/src/a.py")
    assert paths_overlap("product/src/", "product/src/a.py")
    assert not paths_overlap("product/src/a.py", "product/src/b.py")
    conflicts = parallel_conflicts(
        [_task("TASK-A", ["product/src/a.py"]), _task("TASK-B", ["product/src/a.py"])]
    )
    assert conflicts and "TASK-A" in conflicts[0]


def test_two_tasks_build_in_separate_worktrees_and_merge(build_env):
    runs, run_dir, stage = build_env
    tasks = list(_tasks(run_dir).values())
    executor = RecordingExecutor(sleep=0.25)

    batch = run_parallel_tasks(
        "run-01", run_dir, stage, tasks, executor, root=runs, max_workers=2
    )

    assert batch.ok
    assert all(item.ok for item in batch.outcomes)
    assert executor.worktrees["TASK-A"] != executor.worktrees["TASK-B"]
    assert "worktrees" in executor.worktrees["TASK-A"]
    # Both workers were inside their sleep window at the same time.
    assert max(executor.starts.values()) < min(executor.ends.values())
    assert (run_dir / "product" / "src" / "a.py").read_text() == "# TASK-A\n"
    assert (run_dir / "product" / "src" / "b.py").read_text() == "# TASK-B\n"
    # The sandbox opens the worktree and the control-plane usage directory,
    # never the run root and never the other task's tree.
    for task_id, paths in executor.extras.items():
        worktree = executor.worktrees[task_id]
        for path in paths:
            assert path.startswith(worktree) or Path(path).as_posix().endswith("runtime/agent"), (task_id, path)
        assert str(run_dir) not in paths
    assert (run_dir / "tasks" / "TASK-A" / "deliverable.md").is_file()
    state = load_state(run_dir)
    assert state["tasks"]["TASK-A"]["status"] == "review"
    assert state["tasks"]["TASK-B"]["status"] == "review"
    events = [
        json.loads(line)
        for line in (run_dir / "blackbox.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert sum(1 for event in events if event["event_type"] == "ARTIFACT_SUBMIT") == 2
    assert not (run_dir / "runtime" / "worktrees").exists()


def test_overlapping_tasks_are_rejected_before_a_worktree_is_created(build_env):
    runs, run_dir, stage = build_env
    state = load_state(run_dir)
    state["tasks"]["TASK-B"]["allowed_files"] = ["product/src/a.py"]
    save_state(run_dir, state)

    with pytest.raises(FactoryError, match="parallel tasks rejected"):
        run_parallel_tasks(
            "run-01", run_dir, stage, list(_tasks(run_dir).values()), RecordingExecutor(), root=runs
        )

    assert not (run_dir / "runtime" / "worktrees").exists()
    after = load_state(run_dir)
    assert after["tasks"]["TASK-A"]["status"] == "pending"
    assert after["tasks"]["TASK-B"]["status"] == "pending"
    assert not (run_dir / "product" / "src" / "a.py").exists()


def test_one_failed_task_leaves_no_partial_state(build_env):
    runs, run_dir, stage = build_env
    tasks = list(_tasks(run_dir).values())
    batch = run_parallel_tasks(
        "run-01",
        run_dir,
        stage,
        tasks,
        RecordingExecutor(sleep=0.05, fail={"TASK-B"}),
        root=runs,
    )

    by_id = {item.task_id: item for item in batch.outcomes}
    assert by_id["TASK-A"].ok
    assert not by_id["TASK-B"].ok
    assert "executor failed" in (by_id["TASK-B"].error or "")
    assert (run_dir / "product" / "src" / "a.py").is_file()
    assert not (run_dir / "product" / "src" / "b.py").exists()
    assert not (run_dir / "tasks" / "TASK-B" / "deliverable.md").exists()
    state = load_state(run_dir)
    assert state["tasks"]["TASK-A"]["status"] == "review"
    assert state["tasks"]["TASK-B"]["status"] == "pending"
    assert not (run_dir / "runtime" / "worktrees").exists()


def test_merge_conflict_rolls_back_the_batch(build_env):
    runs, run_dir, stage = build_env
    tasks = list(_tasks(run_dir).values())

    class ConflictingExecutor(RecordingExecutor):
        def execute(self, order, context):
            result = super().execute(order, context)
            if context.task["task_id"] == "TASK-B":
                # Someone changed the target outside the worktree after the
                # base commit; merging A must notice, not overwrite it.
                target = Path(context.control_dir) / "product" / "src" / "a.py"
                target.write_text("# changed outside the worktree\n", encoding="utf-8")
            return result

    with pytest.raises(FactoryError, match="merge conflict"):
        run_parallel_tasks(
            "run-01",
            run_dir,
            stage,
            tasks,
            ConflictingExecutor(sleep=0.05),
            root=runs,
        )

    # A's file was never copied over the conflicting content, and no task
    # was submitted.
    assert (run_dir / "product" / "src" / "a.py").read_text() == "# changed outside the worktree\n"
    state = load_state(run_dir)
    assert state["tasks"]["TASK-A"]["status"] == "pending"
    assert state["tasks"]["TASK-B"]["status"] == "pending"
    assert not (run_dir / "tasks" / "TASK-A" / "deliverable.md").exists()
    assert not (run_dir / "runtime" / "worktrees").exists()


def test_normalise_allowed_rejects_escape(tmp_path):
    with pytest.raises(FactoryError):
        normalise_allowed("../outside.py")
    with pytest.raises(FactoryError):
        normalise_allowed("/etc/passwd")
    assert normalise_allowed("./product/src/a.py") == "product/src/a.py"


def test_parallel_results_verify_as_a_build_stage(build_env):
    runs, run_dir, stage = build_env
    tasks = list(_tasks(run_dir).values())
    run_parallel_tasks("run-01", run_dir, stage, tasks, RecordingExecutor(sleep=0.02), root=runs)
    (run_dir / "product" / "tests" / "test_all.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    for task_id in ("TASK-A", "TASK-B"):
        task_accept("run-01", task_id, root=runs)
    assert verify_run(run_dir, "s4_build", require_gate=False) == []


def test_run_loop_uses_the_parallel_branch_and_records_usage(build_env):
    runs, run_dir, _stage = build_env
    result = run_loop("run-01", RecordingExecutor(sleep=0.05), max_steps=1, root=runs, parallel_tasks=2)
    assert result.steps == 1
    state = load_state(run_dir)
    assert state["tasks"]["TASK-A"]["status"] == "review"
    assert state["tasks"]["TASK-B"]["status"] == "review"
    rows = [
        json.loads(line)
        for line in (run_dir / "runtime" / "usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert all(row["parallel"] is True for row in rows)
    assert {row["task_id"] for row in rows} == {"TASK-A", "TASK-B"}
    evidence = list((run_dir / "runtime" / "evidence").glob("*.json"))
    assert any("parallel_batch" in path.read_text(encoding="utf-8") for path in evidence)
