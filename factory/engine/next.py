"""Next-action logic: blocked detection and work-order dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import load_artifacts, load_pipeline, validate_against_schema
from ..errors import RunError
from ..paths import run_dir as safe_run_dir
from ..pipeline import effective_roles
from ..state import current_stage, load_state, stage_gate
from .work_order import _stage_work_order, _task_work_order


def _blocked_reason(state: dict[str, Any], stage: dict[str, Any]) -> tuple[str, str | None]:
    status = state["stage_status"].get(stage["id"])
    if status in {"escalated", "blocked"}:
        return f"stage {stage['id']} is {status}", None
    for task in state["tasks"].values():
        if task.get("stage") == stage["id"] and task["status"] in {"escalated", "blocked"}:
            return f"task {task['task_id']} is {task['status']}", str(task["task_id"])
    return "", None


def _blocked_work_order(
    run_id: str,
    stage: dict[str, Any],
    reason: str,
    task_id: str | None,
    pipeline: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    if task_id:
        next_command = (
            f"factory task reset --run-id {run_id} --task-id {task_id} "
            "--approver human:<id> --reason <text>"
        )
    else:
        next_command = (
            f"factory gate accept-risk --run-id {run_id} --gate {stage['gate']['id']} "
            "--approver human:<id> --risk <text> --mitigation <text>"
        )
    return {
        "schema_version": "1.0.0",
        "action": "blocked",
        "run_id": run_id,
        "stage": stage["id"],
        "stage_title": stage["title"],
        "roles": effective_roles(pipeline, state["mode"], stage),
        "task_id": task_id,
        "artifacts": [],
        "pending_artifacts": [],
        "next_role": None,
        "instructions": (
            f"Human decision required: {reason}. Review the journal with "
            f"`factory log show --run-id {run_id}`, then accept the risk or reset the stage."
        ),
        "gate": stage["gate"],
        "next_command": next_command,
    }


def _next_action(run_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    pipeline = load_pipeline()
    registry = load_artifacts(state["mode"])
    stage = current_stage(state, pipeline)
    if stage is None:
        return {
            "schema_version": "1.0.0",
            "action": "complete",
            "run_id": run_id,
            "stage": None,
            "stage_title": "Run complete",
            "roles": [],
            "task_id": None,
            "artifacts": [],
            "pending_artifacts": [],
            "next_role": None,
            "instructions": "All stages are accepted.",
            "gate": None,
            "next_command": f"factory metrics --run-id {run_id}",
        }
    blocked, blocked_task = _blocked_reason(state, stage)
    if blocked:
        return _blocked_work_order(run_id, stage, blocked, blocked_task, pipeline, state)
    gate = stage_gate(state, stage)
    if gate.get("status") == "approved":
        raise RunError(
            f"stage {stage['id']}: gate is approved but the stage is "
            f"{state['stage_status'].get(stage['id'])!r}; run factory verify"
        )
    if stage["id"] == "s4_build":
        tasks = [task for task in state["tasks"].values() if task.get("stage") == "s4_build"]
        if not tasks:
            from . import import_plan

            tasks = import_plan(run_id, root=root)
            state = load_state(run_dir)
            tasks = [task for task in state["tasks"].values() if task.get("stage") == "s4_build"]
        if tasks:
            review = [task for task in tasks if task["status"] == "review"]
            active = [task for task in tasks if task["status"] == "in_progress"]
            ready = [
                task
                for task in tasks
                if task["status"] == "pending"
                and all(
                    state["tasks"].get(dep, {}).get("status") == "accepted"
                    for dep in task.get("deps", [])
                )
            ]
            if review:
                return _task_work_order(run_dir, run_id, stage, review[0], pipeline, registry)
            if active:
                return _task_work_order(run_dir, run_id, stage, active[0], pipeline, registry)
            if ready:
                return _task_work_order(run_dir, run_id, stage, ready[0], pipeline, registry)
            if any(task["status"] == "pending" for task in tasks):
                return {
                    "schema_version": "1.0.0",
                    "action": "wait",
                    "run_id": run_id,
                    "stage": stage["id"],
                    "stage_title": stage["title"],
                    "roles": [],
                    "task_id": None,
                    "artifacts": [],
                    "pending_artifacts": [],
                    "next_role": None,
                    "instructions": "All remaining tasks wait on unfinished dependencies.",
                    "gate": stage["gate"],
                    "next_command": f"factory status --run-id {run_id} --json",
                }
    return _stage_work_order(run_dir, run_id, state, stage, pipeline, registry)


def next_action(run_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    """Return the next work order, validated against its own schema."""
    from factory.engine import _next_action as _next_action_fn
    order = _next_action_fn(run_id, root=root)
    validate_against_schema(order, "work-order.schema.json", f"work order ({order['action']})")
    return order