"""The run lifecycle: init, next, submit, gates and tasks."""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import artifact_path, validate_stage
from .config import load_artifacts, load_pipeline, resolve_model
from .errors import RunError, VerificationError
from .journal import append_event
from .paths import run_dir as safe_run_dir
from .paths import runs_root, validate_run_id, validate_task_id
from .pipeline import effective_roles, stage_artifacts
from .state import (
    accept_task,
    add_task,
    claim_task,
    current_stage,
    find_stage,
    gate_record,
    load_state,
    new_state,
    reject_task,
    save_state,
    stage_gate,
    submit_task,
)
from .verify import verify_run


def _archive_existing(run_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = run_dir.parent / ".archive" / f"{run_dir.name}-{stamp}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(archive))
    return archive



def _check_artifact_paths(run_dir: Path, artifacts: list[str]) -> None:
    for item in artifacts:
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = run_dir / candidate
        if not candidate.exists():
            raise RunError(f"artifact does not exist: {candidate}")


def _check_gate_approver(stage: dict[str, Any], approver: str) -> None:
    """A gate is decided only by its declared approver, on approve and on reject."""
    expected = stage["gate"]["approver"]
    gate_id = stage["gate"]["id"]
    if expected == "human":
        if not approver.startswith("human:"):
            raise RunError(f"gate {gate_id} requires a human approver, got {approver!r}")
    elif approver != expected:
        raise RunError(f"gate {gate_id} must be decided by {expected}, got {approver!r}")


def _declared_artifact_base(run_dir: Path, spec: dict[str, Any]) -> tuple[Path, bool]:
    """Resolved base path of a declared artifact and whether it scopes a directory."""
    path = str(spec["path"])
    if spec.get("per_pattern") and "{num}" in path:
        return (run_dir / path.rsplit("/", 1)[0]).resolve(), True
    if spec.get("root"):
        return artifact_path(run_dir, spec), False
    return (run_dir / path).resolve(), bool(spec.get("directory"))


def _check_stage_artifact_owners(
    run_dir: Path,
    stage: dict[str, Any],
    role: str,
    artifacts: list[str],
    pipeline: dict[str, Any],
    state: dict[str, Any],
    registry: dict[str, Any],
) -> None:
    """A stage submit may only carry artifacts owned by the submitting role."""
    if not artifacts:
        return
    declared = []
    for spec in stage_artifacts(pipeline, registry, state["mode"], stage, run_dir=run_dir):
        if spec.get("per_task"):
            continue
        base, is_dir = _declared_artifact_base(run_dir, spec)
        declared.append((str(spec.get("owner", "")), base, is_dir))
    for item in artifacts:
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = run_dir / candidate
        candidate = candidate.resolve()
        owner = next(
            (
                candidate_owner
                for candidate_owner, base, is_dir in declared
                if candidate == base or (is_dir and base in candidate.parents)
            ),
            None,
        )
        if owner is None:
            raise RunError(f"artifact {item!r} is not declared for stage {stage['id']}")
        if owner != role:
            raise RunError(
                f"artifact {item!r} on stage {stage['id']} is owned by {owner}, not {role}"
            )


def init_run(
    run_id: str,
    mode: str,
    *,
    root: str | Path | None = None,
    force: bool = False,
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
        "tasks", "product/src", "product/docs", "product/tests", "product/migrations",
    ]:
        (target / sub).mkdir(parents=True, exist_ok=True)
    state = new_state(run_id, mode, pipeline)
    save_state(target, state)
    model = resolve_model("tier-3-frontier")
    event = append_event(
        target,
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
    state = load_state(target)
    return target, state, event


def _task_work_order(
    run_dir: Path,
    run_id: str,
    stage: dict[str, Any],
    task: dict[str, Any],
    pipeline: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    artifacts = stage_artifacts(
        pipeline, registry, load_state(run_dir)["mode"], stage, task_id=task["task_id"], run_dir=run_dir
    )
    action = "verify" if task["status"] == "review" else "produce"
    return {
        "schema_version": "1.0.0",
        "action": action,
        "run_id": run_id,
        "stage": stage["id"],
        "stage_title": stage["title"],
        "roles": [task["owner"]],
        "task_id": task["task_id"],
        "artifacts": artifacts,
        "instructions": (
            f"Task {task['task_id']} ({task['owner']}): produce only the allowed files, "
            f"run the task check, then submit with `factory task submit`."
        ),
        "gate": stage["gate"],
        "next_command": f"factory task submit --run-id {run_id} --task-id {task['task_id']} --role {task['owner']} --artifact <path>",
    }


def _stage_work_order(
    run_dir: Path,
    run_id: str,
    state: dict[str, Any],
    stage: dict[str, Any],
    pipeline: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    artifacts = stage_artifacts(pipeline, registry, state["mode"], stage, run_dir=run_dir)
    gate = state["gate_status"].get(stage["gate"]["id"], {})
    if gate.get("status") == "needs_human":
        action = "gate"
        instructions = f"Gate {stage['gate']['id']} needs a human decision."
        next_command = f"factory gate approve --run-id {run_id} --gate {stage['gate']['id']} --approver human:<id> --evidence <text>"
    elif state["stage_status"].get(stage["id"]) == "review":
        action = "verify"
        instructions = "Artifacts are submitted; run deterministic verification and then the gate."
        next_command = f"factory verify --run-id {run_id} --stage {stage['id']} --require-gate"
    elif not validate_stage(run_dir, stage, registry, state["tasks"]):
        action = "gate"
        instructions = "All required artifacts validate. Ask the gate approver to decide."
        next_command = f"factory gate approve --run-id {run_id} --gate {stage['gate']['id']} --approver {stage['gate']['approver']} --evidence <text>"
    else:
        action = "produce"
        instructions = (
            f"Stage {stage['id']}: produce the listed artifacts using the templates, "
            "then submit them. Do not write outside your role's scope."
        )
        next_command = f"factory submit --run-id {run_id} --stage {stage['id']} --role <role> --artifact <path>"
    return {
        "schema_version": "1.0.0",
        "action": action,
        "run_id": run_id,
        "stage": stage["id"],
        "stage_title": stage["title"],
        "roles": effective_roles(pipeline, state["mode"], stage),
        "task_id": None,
        "artifacts": artifacts,
        "instructions": instructions,
        "gate": stage["gate"],
        "next_command": next_command,
    }


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
            # Keep the rejected gate record as the audit trail of the decision;
            # only the stage is reopened, and its attempts already counted.
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
    return {
        "schema_version": "1.0.0",
        "action": "blocked",
        "run_id": run_id,
        "stage": stage["id"],
        "stage_title": stage["title"],
        "roles": effective_roles(pipeline, state["mode"], stage),
        "task_id": task_id,
        "artifacts": [],
        "instructions": (
            f"Human decision required: {reason}. Review the journal with "
            f"`factory log show --run-id {run_id}`, then accept the risk with an "
            "explicit RISK_ACCEPTANCE event or reset the stage."
        ),
        "gate": stage["gate"],
        "next_command": f"factory status --run-id {run_id} --json",
    }


def next_action(run_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    pipeline = load_pipeline()
    registry = load_artifacts()
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
            "instructions": "All stages are accepted.",
            "gate": None,
            "next_command": f"factory metrics --run-id {run_id}",
        }
    blocked, blocked_task = _blocked_reason(state, stage)
    if blocked:
        return _blocked_work_order(run_id, stage, blocked, blocked_task, pipeline, state)
    gate = stage_gate(state, stage)
    if gate.get("status") == "approved":
        state["stage_status"][stage["id"]] = "accepted"
        save_state(run_dir, state)
        return next_action(run_id, root=root)
    if stage["id"] == "s4_build":
        tasks = [task for task in state["tasks"].values() if task.get("stage") == "s4_build"]
        if not tasks:
            tasks = import_plan(run_id, root=root)
        if tasks:
            pending = [task for task in tasks if task["status"] == "pending"]
            review = [task for task in tasks if task["status"] == "review"]
            active = [task for task in tasks if task["status"] == "in_progress"]
            if pending:
                return _task_work_order(run_dir, run_id, stage, pending[0], pipeline, registry)
            if review:
                return _task_work_order(run_dir, run_id, stage, review[0], pipeline, registry)
            if active:
                return {
                    "schema_version": "1.0.0",
                    "action": "wait",
                    "run_id": run_id,
                    "stage": stage["id"],
                    "stage_title": stage["title"],
                    "roles": [active[0]["owner"]],
                    "task_id": active[0]["task_id"],
                    "artifacts": [],
                    "instructions": f"Task {active[0]['task_id']} is in progress.",
                    "gate": stage["gate"],
                    "next_command": f"factory status --run-id {run_id} --json",
                }
    return _stage_work_order(run_dir, run_id, state, stage, pipeline, registry)


def submit_work(
    run_id: str,
    *,
    stage_id: str,
    role: str,
    artifacts: list[str],
    task_id: str | None = None,
    root: str | Path | None = None,
    why: str = "Producer submitted artifacts",
) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    pipeline = load_pipeline()
    registry = load_artifacts()
    stage = find_stage(pipeline, stage_id)
    if role not in effective_roles(pipeline, state["mode"], stage):
        raise RunError(f"role {role} is not active on {stage_id} in mode {state['mode']}")
    _check_artifact_paths(run_dir, artifacts)
    if task_id:
        submit_task(state, task_id, role, artifacts, run_dir=run_dir)
    else:
        _check_stage_artifact_owners(
            run_dir, stage, role, artifacts, pipeline, state, registry
        )
    state["stage_status"][stage_id] = "review"
    save_state(run_dir, state)
    model = resolve_model("tier-2-balanced")
    return append_event(
        run_dir,
        stage=stage_id,
        task_id=task_id,
        role=role,
        event_type="ARTIFACT_SUBMIT",
        why=why,
        for_what=f"Submit artifacts for {stage_id}",
        action=f"Submitted {', '.join(artifacts) or 'no artifacts'}",
        verdict_status="DRAFT",
        verified_by=role,
        artifacts=artifacts,
        model_tier=model["tier"],
        model_id=model["model_id"],
        reasoning_effort=model["effort"],
    )


def gate_approve(
    run_id: str,
    *,
    gate_id: str,
    approver: str,
    evidence: list[str],
    root: str | Path | None = None,
) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    pipeline = load_pipeline()
    stage = next((s for s in pipeline["stages"] if s["gate"]["id"] == gate_id), None)
    if stage is None:
        raise RunError(f"unknown gate: {gate_id}")
    _check_gate_approver(stage, approver)
    errors = verify_run(run_dir, stage["id"], require_gate=False)
    if errors:
        raise VerificationError("gate preconditions failed:\n- " + "\n- ".join(errors))
    gate_record(state, stage, status="approved", decided_by=approver, evidence=evidence)
    if stage["id"] == "s1_spec":
        state["human_spec_approved"] = True
    if stage["id"] == "s_in":
        state["restore_drill_passed"] = True
    current_stage(state, pipeline)
    save_state(run_dir, state)
    model = resolve_model("tier-3-frontier")
    role = "00-orchestrator" if approver.startswith("human:") else approver
    event = append_event(
        run_dir,
        stage=stage["id"],
        task_id=None,
        role=role,
        event_type="GATE_DECISION",
        why=f"Gate {gate_id} decision",
        for_what=f"Close {stage['id']}",
        action=f"Gate {gate_id} approved by {approver}",
        verdict_status="PASS",
        verified_by=approver,
        evidence=evidence or ["gate approved"],
        model_tier=model["tier"],
        model_id=model["model_id"],
        reasoning_effort=model["effort"],
    )
    if stage["id"] == "s3_plan":
        import_plan(run_id, root=root)
    return event


def gate_reject(
    run_id: str,
    *,
    gate_id: str,
    approver: str,
    evidence: list[str],
    root: str | Path | None = None,
) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    pipeline = load_pipeline()
    stage = next((s for s in pipeline["stages"] if s["gate"]["id"] == gate_id), None)
    if stage is None:
        raise RunError(f"unknown gate: {gate_id}")
    _check_gate_approver(stage, approver)
    gate_record(state, stage, status="rejected", decided_by=approver, evidence=evidence)
    gate = stage_gate(state, stage)
    if gate["attempts"] >= stage["max_attempts"]:
        state["stage_status"][stage["id"]] = "escalated"
        state["veto_counters"][stage["id"]] = state["veto_counters"].get(stage["id"], 0) + 1
        summary = (
            f"Gate {gate_id} rejected by {approver}; "
            f"{gate['attempts']}/{stage['max_attempts']} attempts exhausted, escalated to human"
        )
    else:
        if stage["rework_to"] != stage["id"]:
            _reset_rework_chain(state, pipeline, stage["rework_to"], stage)
        summary = f"Gate {gate_id} rejected by {approver}; rework returned to {stage['rework_to']}"
    current_stage(state, pipeline)
    save_state(run_dir, state)
    model = resolve_model("tier-2-balanced")
    role = "00-orchestrator" if approver.startswith("human:") else approver
    return append_event(
        run_dir,
        stage=stage["id"],
        task_id=None,
        role=role,
        event_type="GATE_DECISION",
        why=f"Gate {gate_id} rejected",
        for_what=f"Rework {stage['rework_to']}",
        action=summary,
        verdict_status="FAIL",
        verified_by=approver,
        evidence=evidence or ["gate rejected"],
        model_tier=model["tier"],
        model_id=model["model_id"],
        reasoning_effort=model["effort"],
        reason_code="GATE_REJECTED",
    )


def import_plan(run_id: str, *, root: str | Path | None = None) -> list[dict[str, Any]]:
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    plan = run_dir / "artifacts" / "plan.md"
    if not plan.exists():
        return []
    created: list[dict[str, Any]] = []
    for line in plan.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|") or "TASK-" not in line:
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        task_id = cells[0]
        if not re.fullmatch(r"TASK-[A-Z0-9][A-Z0-9-]*", task_id):
            continue
        if task_id in state["tasks"]:
            continue
        reqs = [req for req in re.findall(r"REQ-[A-Z0-9-]+", cells[2])]
        deps = [dep for dep in re.findall(r"TASK-[A-Z0-9-]+", cells[3])]
        allowed = [item.strip().strip("`") for item in re.split(r"[,<]", cells[4]) if item.strip()]
        task = {
            "task_id": task_id,
            "stage": "s4_build",
            "owner": cells[1],
            "reqs": reqs,
            "allowed_files": allowed,
            "deps": deps,
            "status": "pending",
            "attempts": 0,
            "artifacts": [],
            "lease": None,
        }
        add_task(state, task)
        created.append(task)
    if created:
        save_state(run_dir, state)
        model = resolve_model("tier-2-balanced")
        for task in created:
            append_event(
                run_dir,
                stage="s4_build",
                task_id=task["task_id"],
                role="04-team-lead",
                event_type="TASK_ADD",
                why="Plan imported",
                for_what=f"Build {task['task_id']}",
                action=f"Added {task['task_id']} for {task['owner']}",
                verdict_status="DRAFT",
                verified_by="04-team-lead",
                model_tier=model["tier"],
                model_id=model["model_id"],
                reasoning_effort=model["effort"],
            )
    return created


def task_claim(run_id: str, task_id: str, owner: str, *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    claim_task(state, task_id, owner)
    save_state(run_dir, state)
    model = resolve_model("tier-2-balanced")
    return append_event(
        run_dir, stage="s4_build", task_id=task_id, role=owner, event_type="TASK_CLAIM",
        why="Task claimed", for_what=f"Build {task_id}", action=f"{owner} claimed {task_id}",
        verdict_status="DRAFT", verified_by=owner, model_tier=model["tier"],
        model_id=model["model_id"], reasoning_effort=model["effort"],
    )


def task_submit(run_id: str, task_id: str, role: str, artifacts: list[str], *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    _check_artifact_paths(run_dir, artifacts)
    submit_task(state, task_id, role, artifacts, run_dir=run_dir)
    save_state(run_dir, state)
    model = resolve_model("tier-2-balanced")
    return append_event(
        run_dir, stage="s4_build", task_id=task_id, role=role, event_type="ARTIFACT_SUBMIT",
        why="Task submitted", for_what=f"Review {task_id}", action=f"Submitted {task_id}",
        verdict_status="DRAFT", verified_by=role, artifacts=artifacts,
        model_tier=model["tier"], model_id=model["model_id"], reasoning_effort=model["effort"],
    )


def task_accept(run_id: str, task_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    accept_task(state, task_id)
    save_state(run_dir, state)
    model = resolve_model("tier-3-frontier")
    return append_event(
        run_dir, stage="s4_build", task_id=task_id, role="02-independent-verifier",
        event_type="TASK_DONE", why="Independent verification passed", for_what=f"Accept {task_id}",
        action=f"Accepted {task_id}", verdict_status="PASS", verified_by="02-independent-verifier",
        evidence=[f"task {task_id} accepted"], model_tier=model["tier"],
        model_id=model["model_id"], reasoning_effort=model["effort"],
    )


def task_reject(run_id: str, task_id: str, reason: str, *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    reject_task(state, task_id, reason)
    save_state(run_dir, state)
    model = resolve_model("tier-3-frontier")
    return append_event(
        run_dir, stage="s4_build", task_id=task_id, role="02-independent-verifier",
        event_type="REWORK", why="Independent verification failed", for_what=f"Rework {task_id}",
        action=f"Rejected {task_id}: {reason}", verdict_status="FAIL",
        verified_by="02-independent-verifier", evidence=[reason], reason_code="TASK_REJECTED",
        model_tier=model["tier"], model_id=model["model_id"], reasoning_effort=model["effort"],
    )


def status_summary(run_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    state = load_state(run_dir)
    pipeline = load_pipeline()
    stage = current_stage(state, pipeline)
    tasks = list(state["tasks"].values())
    return {
        "run_id": run_id,
        "mode": state["mode"],
        "current_stage": stage["id"] if stage else None,
        "stage_title": stage["title"] if stage else "complete",
        "stage_status": state["stage_status"],
        "gate_status": {k: v["status"] for k, v in state["gate_status"].items()},
        "tasks": {
            "total": len(tasks),
            "accepted": sum(1 for task in tasks if task["status"] == "accepted"),
            "review": sum(1 for task in tasks if task["status"] == "review"),
            "escalated": sum(1 for task in tasks if task["status"] == "escalated"),
        },
        "event_count": state["event_count"],
        "last_event_hash": state["last_event_hash"],
        "human_spec_approved": state["human_spec_approved"],
        "restore_drill_passed": state["restore_drill_passed"],
    }


def model_for(tier: str) -> dict[str, str]:
    return resolve_model(tier)
