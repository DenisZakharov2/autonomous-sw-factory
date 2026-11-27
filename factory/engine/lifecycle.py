"""Run lifecycle: creation and archiving."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import load_pipeline
from ..errors import RunError
from ..journal import record_event, run_lock
from ..paths import run_dir as safe_run_dir, runs_root, validate_run_id
from ..state import load_state, new_state, save_state
from .common import _model_for


def _archive_existing(run_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = run_dir.parent / ".archive" / f"{run_dir.name}-{stamp}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(archive))
    return archive


def init_run(
    run_id: str,
    mode: str,
    *,
    root: str | Path | None = None,
    force: bool = False,
    idea: str | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    validate_run_id(run_id)
    pipeline = load_pipeline()
    if mode not in pipeline["modes"]:
        raise RunError(f"unknown mode {mode!r}; choose from {sorted(pipeline['modes'])}")
    base = runs_root(root)
    target = safe_run_dir(run_id, base)
    if target.exists() and any(target.iterdir()):
        if not force:
            raise RunError(f"run {run_id!r} already exists; use --force to archive it")
        _archive_existing(target)
    target.mkdir(parents=True, exist_ok=True)
    for sub in [
        "backups", "artifacts", "artifacts/adr", "artifacts/screens", "artifacts/patches",
        "artifacts/verification", "tasks", "product/src", "product/docs", "product/tests",
        "product/migrations", "runtime", "runtime/agent", "runtime/evidence",
    ]:
        (target / sub).mkdir(parents=True, exist_ok=True)
    # Create usage.jsonl as a regular file so ro-bind works from the first step.
    (target / "runtime" / "usage.jsonl").write_text("", encoding="utf-8")
    if idea:
        (target / "idea.md").write_text(idea.strip() + "\n", encoding="utf-8")
    model = _model_for("00-orchestrator", "tier-3-frontier")
    with run_lock(target):
        state = new_state(run_id, mode, pipeline)
        event = record_event(
            target,
            state,
            stage="init",
            task_id=None,
            role="00-orchestrator",
            event_type="INIT",
            why="Explicit human init command",
            for_what=f"Create isolated run {run_id} in mode {mode}",
            action=f"Initialised runs/{run_id}, state.json and blackbox.jsonl",
            verdict_status="INIT_OK",
            verified_by="00-orchestrator",
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
            evidence=["factory init"],
        )
        save_state(target, state)
    return target, load_state(target), event
