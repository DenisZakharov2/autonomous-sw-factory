"""Shared helpers used by both the engine package and the gates module."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..artifacts import (
    artifact_path,
    collect_artifact_entries,
    pattern_paths,
    plan_rows,
    substitute_path,
)
from ..config import load_pipeline
from ..errors import RunError
from ..journal import record_event
from ..pipeline import effective_owner, effective_roles, stage_artifacts
from ..state import (
    add_task,
    current_stage,
    find_stage,
    stage_gate,
)
from .common import _model_for, verification_policy


def _require_current(state: dict[str, Any], pipeline: dict[str, Any], stage: dict[str, Any]) -> None:
    current = current_stage(state, pipeline)
    current_id = current["id"] if current else None
    if current_id != stage["id"]:
        raise RunError(
            f"stage {stage['id']} is not current (current stage is {current_id!r}); "
            "stages cannot be skipped or closed out of order"
        )


def _require_stage_status(state: dict[str, Any], stage: dict[str, Any], allowed: set[str]) -> str:
    status = state["stage_status"].get(stage["id"], "pending")
    if status not in allowed:
        raise RunError(
            f"stage {stage['id']} is {status}; expected one of {sorted(allowed)}"
        )
    return status


def _declared_artifact_base(
    run_dir: Path, spec: dict[str, Any], *, stage_id: str | None = None
) -> tuple[Path, bool]:
    """Resolved base path of a declared artifact and whether it scopes a directory."""
    path = str(spec["path"])
    if spec.get("per_pattern") and "{num}" in path:
        return (run_dir / path.rsplit("/", 1)[0]).resolve(), True
    if spec.get("root"):
        return artifact_path(run_dir, spec), False
    return (run_dir / substitute_path(path, stage_id=stage_id)).resolve(), bool(spec.get("directory"))


def _root_paths(
    pipeline: dict[str, Any],
    registry: dict[str, Any],
    mode: str,
    stage: dict[str, Any],
    run_dir: Path,
    policy: str | None = None,
) -> list[str]:
    return [
        str(spec["path"])
        for spec in stage_artifacts(
            pipeline, registry, mode, stage, run_dir=run_dir, verification_policy=policy
        )
        if spec.get("root")
    ]


def _collect_paths(
    run_dir: Path,
    stage: dict[str, Any],
    pipeline: dict[str, Any],
    registry: dict[str, Any],
    state: dict[str, Any],
) -> list[str]:
    """Paths of all declared stage artifacts that currently exist."""
    policy = verification_policy(state)
    paths: list[str] = []
    for spec in stage_artifacts(
        pipeline, registry, state["mode"], stage, run_dir=run_dir, verification_policy=policy
    ):
        if spec.get("per_task"):
            for task in state["tasks"].values():
                if task.get("stage") == stage["id"]:
                    candidate = artifact_path(
                        run_dir, spec, task_id=task["task_id"], stage_id=stage["id"]
                    )
                    if candidate.exists():
                        paths.append(str(candidate))
            continue
        if spec.get("per_pattern"):
            paths.extend(str(item) for item in pattern_paths(run_dir, spec) if item.exists())
            continue
        candidate = artifact_path(run_dir, spec, stage_id=stage["id"])
        if candidate.exists():
            if candidate.is_dir() and not any(
                item.is_file() for item in candidate.rglob("*")
            ):
                continue
            paths.append(str(candidate))
    return paths


def _stage_entries(
    run_dir: Path,
    stage: dict[str, Any],
    pipeline: dict[str, Any],
    registry: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    return collect_artifact_entries(
        run_dir,
        _collect_paths(run_dir, stage, pipeline, registry, state),
        root_paths=_root_paths(
            pipeline, registry, state["mode"], stage, run_dir, verification_policy(state)
        ),
    )


def _reopen_stage_tasks(state: dict[str, Any], stage_id: str) -> None:
    """Return every closed or open task of a stage to `pending`, lease released.

    A rejection of the stage is a rejection of the work its tasks produced:
    leaving them `accepted` means nobody is asked to fix anything, and the
    next step is a fresh verification of the same bytes.
    """
    for task in state["tasks"].values():
        if task.get("stage") == stage_id and task.get("status") in {
            "accepted", "review", "in_progress",
        }:
            task["status"] = "pending"
            task["lease"] = None


def _reset_rework_chain(
    state: dict[str, Any],
    pipeline: dict[str, Any],
    target_id: str,
    from_stage: dict[str, Any],
) -> None:
    """Reopen the rework target and every stage between it and the rejected one."""
    target = next(stage for stage in pipeline["stages"] if stage["id"] == target_id)
    for stage in pipeline["stages"]:
        if stage["id"] == from_stage["id"]:
            state["stage_status"][stage["id"]] = "pending"
            continue
        if stage["id"] == target_id:
            state["stage_status"][stage["id"]] = "in_progress"
        elif target["order"] < stage["order"] < from_stage["order"]:
            state["stage_status"][stage["id"]] = "pending"
        else:
            continue
        gate = stage_gate(state, stage)
        gate["status"] = "pending"
        gate["decided_by"] = None
        gate["decided_at"] = None
        gate["evidence"] = []
    _reopen_stage_tasks(state, target_id)


def _validate_plan_graph(tasks: list[dict[str, Any]], mode_roles: list[str]) -> None:
    ids = {task["task_id"] for task in tasks}
    for task in tasks:
        if task.get("owner") not in mode_roles:
            raise RunError(
                f"plan task {task['task_id']} owner {task.get('owner')!r} is not active in this mode"
            )
        for dep in task.get("deps", []):
            if dep not in ids:
                raise RunError(f"plan task {task['task_id']} depends on unknown task {dep}")
            if dep == task["task_id"]:
                raise RunError(f"plan task {task['task_id']} depends on itself")
    for task in tasks:
        seen: set[str] = set()
        stack = list(task.get("deps", []))
        while stack:
            dep = stack.pop()
            if dep == task["task_id"]:
                raise RunError(f"plan dependency cycle involving {task['task_id']}")
            if dep in seen:
                continue
            seen.add(dep)
            stack.extend(next((t.get("deps", []) for t in tasks if t["task_id"] == dep), []))
    for index, left in enumerate(tasks):
        for right in tasks[index + 1:]:
            shared = set(left.get("allowed_files", [])) & set(right.get("allowed_files", []))
            if not shared:
                continue
            if right["task_id"] in left.get("deps", []) or left["task_id"] in right.get("deps", []):
                continue
            raise RunError(
                f"plan tasks {left['task_id']} and {right['task_id']} write the same files "
                f"without a dependency: {sorted(shared)}"
            )


def _prepare_plan(run_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    pipeline = load_pipeline()
    mode_roles = effective_roles(
        pipeline, state["mode"], find_stage(pipeline, "s4_build")
    )
    rows = plan_rows(run_dir)
    if not rows:
        return []
    parsed: list[dict[str, Any]] = []
    for cells in rows:
        parsed.append(
            {
                "task_id": cells[0],
                "stage": "s4_build",
                "owner": cells[1],
                "reqs": re.findall(r"REQ-[A-Z0-9-]+", cells[2]),
                "deps": re.findall(r"TASK-[A-Z0-9-]+", cells[3]),
                "allowed_files": [
                    item.strip().strip("`") for item in re.split(r"[,<]", cells[4]) if item.strip()
                ],
                "status": "pending",
                "attempts": 0,
                "artifacts": [],
                "lease": None,
            }
        )
    _validate_plan_graph(parsed, mode_roles)
    return parsed


def _import_plan_state(
    run_dir: Path,
    state: dict[str, Any],
    parsed: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    pipeline = load_pipeline()
    mode_roles = effective_roles(
        pipeline, state["mode"], find_stage(pipeline, "s4_build")
    )
    if parsed is None:
        parsed = _prepare_plan(run_dir, state)
    if not parsed:
        return []
    author = effective_owner(pipeline, state["mode"], "plan", "04-team-lead")
    if author not in mode_roles:
        author = "00-orchestrator"
    created: list[dict[str, Any]] = []
    for task in parsed:
        existing = state["tasks"].get(task["task_id"])
        # The report directory exists from the start: a directory the
        # sandbox cannot protect is a directory the producer may fill.
        (run_dir / "tasks" / str(task["task_id"]) / "verification").mkdir(
            parents=True, exist_ok=True
        )
        if existing is None:
            add_task(state, task)
            created.append(task)
            continue
        if existing.get("status") == "pending":
            for key in ("owner", "reqs", "allowed_files", "deps"):
                existing[key] = task[key]
    if created:
        model = _model_for(author)
        for task in created:
            record_event(
                run_dir,
                state,
                stage="s4_build",
                task_id=task["task_id"],
                role=author,
                event_type="TASK_ADD",
                why="Plan imported",
                for_what=f"Build {task['task_id']}",
                action=f"Added {task['task_id']} for {task['owner']}",
                verdict_status="DRAFT",
                verified_by=author,
                model_tier=model["tier"],
                model_id=model["model_id"],
                reasoning_effort=model["effort"],
            )
    return created
