"""Turn a work order into the exact paths the engine may accept.

A runtime must submit only artifacts the stage declared and that exist on
disk.  An executor's own report of "files created" is evidence for the
transcript, never a submission list: otherwise an agent could name a path it
never wrote, or a path outside the run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import project_root


def _is_populated(path: Path) -> bool:
    if path.is_symlink() or not path.exists():
        return False
    if path.is_dir():
        return any(item.is_file() and not item.is_symlink() for item in path.rglob("*"))
    return True


def _recorded_run_path(path: Path, run_dir: Path) -> str:
    try:
        return path.relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def work_order_submit_plan(
    order: dict[str, Any], run_dir: Path, task: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    """`[{"owner": ..., "path": ...}]` for every declared artifact that exists.

    For a build task the only submittable paths are the task's `Allowed Files`
    and its deliverable: a task cannot claim a whole `product/src/` directory
    that other tasks also write into.
    """
    run_dir = run_dir.resolve()
    if task is not None:
        owner = str(task["owner"])
        candidates = [f"tasks/{task['task_id']}/deliverable.md"]
        candidates.extend(str(item) for item in task.get("allowed_files") or [])
        plan: list[dict[str, str]] = []
        for path in dict.fromkeys(candidates):
            if _is_populated(run_dir / path):
                plan.append({"owner": owner, "path": path})
        return plan
    fallback_owner = str((order.get("roles") or [""])[0])
    plan = []
    seen: set[str] = set()
    for spec in order.get("artifacts") or []:
        raw = str(spec.get("resolved_path") or spec.get("path") or "")
        if not raw:
            continue
        owner = str(spec.get("owner") or "") or fallback_owner
        if owner.startswith("{"):
            owner = fallback_owner
        if spec.get("root"):
            candidate = Path(raw) if Path(raw).is_absolute() else project_root() / raw
            recorded = str(spec.get("path") or raw)
        elif spec.get("per_task") or Path(raw).is_absolute():
            candidate = Path(raw)
            recorded = _recorded_run_path(candidate, run_dir)
        else:
            candidate = run_dir / raw
            recorded = raw
        if not _is_populated(candidate):
            continue
        if recorded in seen:
            continue
        seen.add(recorded)
        plan.append({"owner": owner, "path": recorded})
    return plan


def work_order_submit_paths(order: dict[str, Any], run_dir: Path) -> list[str]:
    """The declared, existing artifact paths of this order, in order."""
    return [entry["path"] for entry in work_order_submit_plan(order, run_dir)]
