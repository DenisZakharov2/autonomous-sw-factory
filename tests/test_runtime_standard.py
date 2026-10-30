import shutil
from pathlib import Path

import pytest

from factory.config import load_pipeline
from factory.engine import gate_approve, gate_reject, init_run, next_action, submit_work
from factory.runtime.fake import FakeExecutor
from factory.runtime.loop import run_loop
from factory.state import add_task, find_stage, load_state, save_state, stage_gate
from factory.verify import verify_run

FIXTURES = Path(__file__).resolve().parent.parent / "factory" / "runtime" / "fixtures" / "nano"


@pytest.fixture()
def standard_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    init_run("run-01", "standard", root=runs, idea="CLI todo без зависимостей")
    return runs


def _run_standard(run_id, runs, max_segments=20):
    result = None
    for _ in range(max_segments):
        result = run_loop(run_id, FakeExecutor(), max_steps=60, root=runs)
        if result.code == 2:
            gate_approve(
                run_id,
                gate_id=result.order["gate"]["id"],
                approver="human:test",
                evidence=["test approval"],
                root=runs,
            )
            continue
        return result
    raise AssertionError("standard run did not finish within the segment budget")


def _advance_to(standard_env, stage_id: str) -> Path:
    runs = standard_env
    run_dir = runs / "run-01"
    pipeline = load_pipeline()
    state = load_state(run_dir)
    for stage in pipeline["stages"]:
        if stage["id"] == stage_id:
            break
        state["stage_status"][stage["id"]] = "accepted"
        stage_gate(state, stage)["status"] = "approved"
    state["stage_status"][stage_id] = "in_progress"
    save_state(run_dir, state)
    return run_dir


def _copy(relative: str, run_dir: Path) -> None:
    target = run_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURES / relative, target)


def test_full_standard_reaches_complete(standard_env):
    result = _run_standard("run-01", standard_env)
    assert result.status == "complete", result.message
    assert result.code == 0
    run_dir = standard_env / "run-01"
    events = (run_dir / "blackbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) > 33  # nano reference has 33
    assert verify_run(run_dir, "s9_learn", require_gate=True) == []
    state = load_state(run_dir)
    assert all(status == "accepted" for status in state["stage_status"].values())
    assert state["tasks"]["TASK-001"]["status"] == "accepted"
    assert state["tasks"]["TASK-002"]["status"] == "accepted"


def test_multirole_stage_dispatches_one_owner_at_a_time(standard_env):
    run_dir = _advance_to(standard_env, "s1_spec")
    order = next_action("run-01", root=standard_env)
    assert order["action"] == "produce"
    assert order["next_role"] == "05-business-analyst"
    pending = {item["id"]: item["owner"] for item in order["pending_artifacts"]}
    assert pending["domain_model"] == "06-system-analyst"
    assert pending["spec"] == "05-business-analyst"

    # The first role writes only its own three artifacts.
    for relative in (
        "artifacts/spec.md",
        "artifacts/spec-human.md",
        "artifacts/traceability.json",
    ):
        _copy(relative, run_dir)
    submit_work(
        "run-01",
        stage_id="s1_spec",
        role="05-business-analyst",
        artifacts=[
            "artifacts/spec.md",
            "artifacts/spec-human.md",
            "artifacts/traceability.json",
        ],
        root=standard_env,
    )

    order = next_action("run-01", root=standard_env)
    assert order["action"] == "produce"
    assert order["next_role"] == "06-system-analyst"
    assert {item["id"] for item in order["pending_artifacts"]} == {
        "domain_model",
        "access_matrix",
    }

    for relative in ("artifacts/domain-model.md", "artifacts/access-matrix.md"):
        _copy(relative, run_dir)
    submit_work(
        "run-01",
        stage_id="s1_spec",
        role="06-system-analyst",
        artifacts=["artifacts/domain-model.md", "artifacts/access-matrix.md"],
        root=standard_env,
    )

    order = next_action("run-01", root=standard_env)
    assert order["action"] == "gate"
    assert order["next_role"] is None  # human gate does not guess a role
    state = load_state(run_dir)
    assert state["stage_status"]["s1_spec"] == "review"
    events = [
        line
        for line in (run_dir / "blackbox.jsonl").read_text(encoding="utf-8").splitlines()
        if "ARTIFACT_SUBMIT" in line
    ]
    assert len(events) == 2
    assert "05-business-analyst" in events[0]
    assert "06-system-analyst" in events[1]


def test_gate_reject_returns_build_tasks_to_pending(standard_env):
    run_dir = _advance_to(standard_env, "s5_acceptance")
    pipeline = load_pipeline()
    state = load_state(run_dir)
    # s4 is already accepted; its tasks carry the work that must be redone.
    state["stage_status"]["s4_build"] = "accepted"
    stage_gate(state, find_stage(pipeline, "s4_build"))["status"] = "approved"
    add_task(
        state,
        {
            "task_id": "TASK-001",
            "stage": "s4_build",
            "owner": "12-fullstack-developer",
            "reqs": ["REQ-001"],
            "deps": [],
            "allowed_files": ["product/src/todo.py"],
            "status": "accepted",
            "attempts": 0,
            "artifacts": ["product/src/todo.py"],
            "lease": None,
        },
    )
    save_state(run_dir, state)

    gate_reject(
        "run-01",
        gate_id="gate_s5",
        approver="02-independent-verifier",
        evidence=["acceptance failed: REQ-002 is not covered"],
        root=standard_env,
    )

    after = load_state(run_dir)
    assert after["tasks"]["TASK-001"]["status"] == "pending"
    assert after["stage_status"]["s4_build"] == "in_progress"
    assert after["stage_status"]["s5_acceptance"] == "pending"
    assert after["gate_status"]["gate_s4"]["status"] == "pending"
    assert after["gate_status"]["gate_s5"]["status"] == "rejected"
    order = next_action("run-01", root=standard_env)
    assert order["stage"] == "s4_build"
    assert order["action"] == "produce"
