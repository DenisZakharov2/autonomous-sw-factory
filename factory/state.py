"""Run state: stage status, gates, tasks and the journal anchor."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import load_json, load_pipeline, validate_against_schema
from .errors import RunError
from .journal import utc_now

STAGE_STATUSES = {"pending", "in_progress", "review", "accepted", "blocked", "escalated"}
GATE_STATUSES = {"pending", "approved", "rejected", "needs_human"}
TASK_STATUSES = {"pending", "in_progress", "blocked", "review", "accepted", "escalated"}


def new_state(run_id: str, mode: str, pipeline: dict[str, Any]) -> dict[str, Any]:
    stages = pipeline["stages"]
    stage_status = {stage["id"]: "pending" for stage in stages}
    gate_status = {
        stage["gate"]["id"]: {
            "status": "pending",
            "approver": stage["gate"]["approver"],
            "decided_by": None,
            "decided_at": None,
            "evidence": [],
            "attempts": 0,
        }
        for stage in stages
    }
    if mode != "brownfield":
        # A greenfield run never enters S-In. Mark it accepted so the stage
        # machine starts at intake instead of asking for a backup that does
        # not exist.
        stage_status["s_in"] = "accepted"
        gate_status["gate_s_in"] = {
            "status": "approved",
            "approver": "system",
            "decided_by": "system:greenfield",
            "decided_at": utc_now(),
            "evidence": ["greenfield mode: S-In not applicable"],
            "attempts": 0,
        }
    start = "s_in" if mode == "brownfield" else "s00_intake"
    now = utc_now()
    return {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "mode": mode,
        "current_stage": start,
        "stage_status": stage_status,
        "gate_status": gate_status,
        "tasks": {},
        "veto_counters": {},
        "human_spec_approved": False,
        "restore_drill_passed": mode != "brownfield",
        "last_event_hash": "0" * 64,
        "event_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def load_state(run_dir: Path) -> dict[str, Any]:
    path = state_path(run_dir)
    if not path.exists():
        raise RunError(f"run state not found: {path}")
    state = load_json(path)
    validate_against_schema(state, "state.schema.json", str(path))
    return state


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    validate_against_schema(state, "state.schema.json", "state.json")
    path = state_path(run_dir)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def stage_list(pipeline: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(pipeline["stages"], key=lambda stage: stage["order"])


def find_stage(pipeline: dict[str, Any], stage_id: str) -> dict[str, Any]:
    for stage in pipeline["stages"]:
        if stage["id"] == stage_id:
            return stage
    raise RunError(f"unknown stage: {stage_id}")


def current_stage(state: dict[str, Any], pipeline: dict[str, Any]) -> dict[str, Any] | None:
    for stage in stage_list(pipeline):
        if state["stage_status"].get(stage["id"]) != "accepted":
            state["current_stage"] = stage["id"]
            return stage
    state["current_stage"] = None
    return None


def stage_gate(state: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    gate_id = stage["gate"]["id"]
    if gate_id not in state["gate_status"]:
        state["gate_status"][gate_id] = {
            "status": "pending",
            "approver": stage["gate"]["approver"],
            "decided_by": None,
            "decided_at": None,
            "evidence": [],
            "attempts": 0,
        }
    return state["gate_status"][gate_id]


def add_task(state: dict[str, Any], task: dict[str, Any]) -> None:
    task_id = task["task_id"]
    if task_id in state["tasks"]:
        raise RunError(f"task already exists: {task_id}")
    if task.get("status") not in TASK_STATUSES:
        raise RunError(f"invalid task status: {task.get('status')}")
    state["tasks"][task_id] = task


def get_task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    if task_id not in state["tasks"]:
        raise RunError(f"unknown task: {task_id}")
    return state["tasks"][task_id]


def claim_task(state: dict[str, Any], task_id: str, owner: str) -> dict[str, Any]:
    task = get_task(state, task_id)
    if task["owner"] != owner:
        raise RunError(f"task {task_id} is owned by {task['owner']}, not {owner}")
    if task["status"] not in {"pending", "blocked"}:
        raise RunError(f"task {task_id} is {task['status']}, cannot claim")
    for dep in task["deps"]:
        if dep not in state["tasks"] or state["tasks"][dep]["status"] != "accepted":
            raise RunError(f"task {task_id} is blocked by dependency {dep}")
    task["status"] = "in_progress"
    task["lease"] = owner
    return task


def submit_task(state: dict[str, Any], task_id: str, artifacts: list[str]) -> dict[str, Any]:
    task = get_task(state, task_id)
    if task["status"] != "in_progress":
        raise RunError(f"task {task_id} is {task['status']}, cannot submit")
    task["status"] = "review"
    task["artifacts"] = artifacts
    task["lease"] = None
    return task


def accept_task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = get_task(state, task_id)
    if task["status"] != "review":
        raise RunError(f"task {task_id} is {task['status']}, cannot accept")
    task["status"] = "accepted"
    return task


def reject_task(state: dict[str, Any], task_id: str, reason: str) -> dict[str, Any]:
    task = get_task(state, task_id)
    task["attempts"] += 1
    task["status"] = "escalated" if task["attempts"] >= 3 else "pending"
    task["lease"] = None
    task.setdefault("reject_reasons", []).append(reason)
    return task


def gate_record(state: dict[str, Any], stage: dict[str, Any], *, status: str, decided_by: str, evidence: list[str]) -> dict[str, Any]:
    if status not in GATE_STATUSES:
        raise RunError(f"invalid gate status: {status}")
    gate = stage_gate(state, stage)
    gate["status"] = status
    gate["decided_by"] = decided_by
    gate["decided_at"] = utc_now()
    gate["evidence"] = evidence
    if status == "approved":
        state["stage_status"][stage["id"]] = "accepted"
    elif status == "rejected":
        state["stage_status"][stage["id"]] = "in_progress"
        gate["attempts"] += 1
    elif status == "needs_human":
        state["stage_status"][stage["id"]] = "blocked"
    return gate


def initialise_pipeline_state(run_dir: Path, run_id: str, mode: str) -> dict[str, Any]:
    pipeline = load_pipeline()
    state = new_state(run_id, mode, pipeline)
    save_state(run_dir, state)
    return state
