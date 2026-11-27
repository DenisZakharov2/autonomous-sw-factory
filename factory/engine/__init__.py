"""The run lifecycle: init, next, submit, gates, tasks and escalation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import (
    artifact_path,
    collect_artifact_entries,
    pattern_paths,
    resolve_recorded_path,
    validate_stage,
)
from ..config import (
    load_artifacts,
    load_pipeline,
    resolve_model,
    validate_against_schema,
)
from ..errors import JournalError, RunError, VerificationError
from ..journal import (
    ARTIFACT_RECORDS,
    read_events,
    record_event,
    run_lock,
)
from ..paths import run_dir as safe_run_dir
from ..paths import validate_task_id
from ..pipeline import effective_owner, effective_roles
from ..state import (
    accept_task,
    claim_task,
    current_stage,
    find_stage,
    get_task,
    load_state,
    reject_task,
    save_state,
    stage_gate,
    submit_task,
)

from .common import VERIFIER, VERIFICATION_IDS, _model_for, verification_policy
from .core import (
    _import_plan_state,
    _prepare_plan,
    _reopen_stage_tasks,
    _require_current,
    _require_stage_status,
    _reset_rework_chain,
    _stage_entries,
    _validate_plan_graph,
)
from .verification import (
    _declared_tests,
    _feedback,
    _judged_entries,
    _latest_judgement,
    _record_verification_locked,
    _relative,
    _report_is_current,
    _task_deliverable_errors as _task_deliverable_errors,
    _verdict_is_fresh,
    _write_verification,
    record_verification as record_verification,
    stage_declares_verification,
    verification_is_stale as verification_is_stale,
    verification_report,
)

from .lifecycle import _archive_existing, init_run  # noqa: F401
from .work_order import (  # noqa: F401
    _check_stage_artifact_owners,
    _collect_paths,
    _declared_artifact_base,
    _next_role,
    _pending_artifacts,
    _root_paths,
    _stage_work_order,
    _task_entries,
    _task_work_order,
)
from .next import (  # noqa: F401
    _blocked_reason,
    _blocked_work_order,
    _next_action,
    next_action,
)

# Gate functions (moved to gates.py, re-exported here for the public surface)
from .gates import (
    APPROVAL_ACTIONS,
    DEFAULT_APPROVAL_TTL_S,
    MAX_APPROVAL_TTL_S,
    MIN_APPROVAL_KEY_BYTES,
    accept_risk,
    approval_key_bytes,
    gate_approve,
    gate_reject,
    make_approval_token,
    stage_reset,
    task_reset,
    veto,
)

__all__ = [
    # Run lifecycle
    "init_run",
    "next_action",
    "submit_work",
    "artifact_rerecord",
    "import_plan",
    "task_claim",
    "task_submit",
    "task_accept",
    "task_reject",
    "status_summary",
    "model_for",
    # Gates
    "APPROVAL_ACTIONS",
    "DEFAULT_APPROVAL_TTL_S",
    "MAX_APPROVAL_TTL_S",
    "MIN_APPROVAL_KEY_BYTES",
    "accept_risk",
    "approval_key_bytes",
    "gate_approve",
    "gate_reject",
    "make_approval_token",
    "stage_reset",
    "task_reset",
    "veto",
    # Shared constants and helpers
    "VERIFIER",
    "VERIFICATION_IDS",
    "_model_for",
    "verification_policy",
    "_import_plan_state",
    "_prepare_plan",
    "_reopen_stage_tasks",
    "_require_current",
    "_require_stage_status",
    "_reset_rework_chain",
    "_stage_entries",
    "_validate_plan_graph",
    # Verification
    "_declared_tests",
    "_feedback",
    "_judged_entries",
    "_latest_judgement",
    "_record_verification_locked",
    "_relative",
    "_report_is_current",
    "_task_deliverable_errors",
    "_verdict_is_fresh",
    "_write_verification",
    "record_verification",
    "stage_declares_verification",
    "verification_is_stale",
    "verification_report",
]


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
    order = _next_action(run_id, root=root)
    validate_against_schema(order, "work-order.schema.json", f"work order ({order['action']})")
    return order


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
    if not artifacts:
        raise RunError("at least one --artifact must be provided")
    if task_id and stage_id != "s4_build":
        raise RunError("task submissions belong to s4_build")
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    stage = find_stage(pipeline, stage_id)
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        _require_current(state, pipeline, stage)
        _require_stage_status(state, stage, {"pending", "in_progress", "review"})
        if role not in effective_roles(pipeline, state["mode"], stage):
            raise RunError(f"role {role} is not active on {stage_id} in mode {state['mode']}")
        if task_id:
            submit_task(state, task_id, role, artifacts, run_dir=run_dir)
            # A producer cannot submit the verifier's report: it is not theirs,
            # and binding its hash at submit time would blame them for a file
            # the verifier writes afterwards.
            entries = _task_entries(
                run_dir, state["tasks"][task_id], include_verification=False
            )
            model = _model_for(role)
            event = record_event(
                run_dir,
                state,
                stage=stage_id,
                task_id=task_id,
                role=role,
                event_type="ARTIFACT_SUBMIT",
                why=why,
                for_what=f"Submit artifacts for {stage_id}",
                action=f"Submitted {task_id}: {', '.join(artifacts)}",
                verdict_status="DRAFT",
                verified_by=role,
                artifacts=entries,
                model_tier=model["tier"],
                model_id=model["model_id"],
                reasoning_effort=model["effort"],
            )
            save_state(run_dir, state)
            return event
        roots = _root_paths(pipeline, registry, state["mode"], stage, run_dir)
        entries = collect_artifact_entries(run_dir, artifacts, root_paths=roots)
        _check_stage_artifact_owners(
            run_dir, stage, role, entries, pipeline, state, registry
        )
        errors = validate_stage(
            run_dir, stage, registry, state["tasks"], verification_policy=verification_policy(state)
        )
        state["stage_status"][stage_id] = "review" if not errors else "in_progress"
        model = _model_for(role)
        event = record_event(
            run_dir,
            state,
            stage=stage_id,
            task_id=None,
            role=role,
            event_type="ARTIFACT_SUBMIT",
            why=why,
            for_what=f"Submit artifacts for {stage_id}",
            action=f"Submitted {', '.join(artifacts)}",
            verdict_status="DRAFT",
            verified_by=role,
            artifacts=entries,
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def _declared_for_path(
    run_dir: Path,
    pipeline: dict[str, Any],
    registry: dict[str, Any],
    state: dict[str, Any],
    target: Path,
    preferred_stage: str | None = None,
) -> list[tuple[dict[str, Any], str, str | None]]:
    """Every declaration whose path is `target`: (stage, owner, task_id).

    The registry is walked directly instead of through `stage_artifacts`,
    because per-task declarations (and their `{task.owner}`) are filtered out
    there unless a task id is already known.
    """
    matches: list[tuple[dict[str, Any], str, str | None]] = []

    def scan(stages: list[dict[str, Any]]) -> None:
        for stage in stages:
            for artifact_id in stage["artifacts"]:
                spec = dict(registry["artifacts"][artifact_id])
                spec["id"] = artifact_id
                owner = effective_owner(
                    pipeline, state["mode"], artifact_id, str(spec.get("owner", ""))
                )
                if spec.get("per_task"):
                    for task in state["tasks"].values():
                        if task.get("stage") != stage["id"]:
                            continue
                        base = artifact_path(run_dir, spec, task_id=str(task["task_id"]))
                        if base == target:
                            matches.append(
                                (
                                    stage,
                                    str(task.get("owner") or owner),
                                    str(task["task_id"]),
                                )
                            )
                    continue
                if spec.get("per_pattern"):
                    for item in pattern_paths(run_dir, spec):
                        resolved = item.resolve()
                        if resolved == target or (
                            resolved.is_dir() and resolved in target.parents
                        ):
                            matches.append((stage, owner, None))
                    continue
                base, is_dir = _declared_artifact_base(run_dir, spec, stage_id=stage["id"])
                if base == target or (is_dir and base in target.parents):
                    matches.append((stage, owner, None))

    if preferred_stage:
        scan([stage for stage in pipeline["stages"] if stage["id"] == preferred_stage])
    if not matches:
        scan(pipeline["stages"])
    return matches


def artifact_rerecord(
    run_id: str,
    *,
    path: str,
    role: str,
    reason: str,
    evidence: list[str],
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Record that an already-accepted artifact changed, on purpose.

    This is the legal alternative to a silent rewrite: the file is already on
    disk with its new content, the owner states why, and the journal keeps both
    the hash the gate approved and the hash that now binds.  It does not
    reopen the gate, does not move any stage or task status, and does not
    legitimise a later edit — any byte written after this event is tampering
    again.
    """
    reason = str(reason or "").strip()
    if not reason:
        raise RunError("re-record requires a non-empty --reason")
    evidence_list = [str(item) for item in evidence if str(item).strip()]
    if not evidence_list:
        raise RunError("re-record requires at least one --evidence reference")
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        roots = [
            str(spec["path"])
            for spec in registry["artifacts"].values()
            if spec.get("root")
        ]
        entry = collect_artifact_entries(run_dir, [path], root_paths=roots)[0]
        target = resolve_recorded_path(run_dir, str(entry["path"]), root_paths=roots)
        events = read_events(run_dir / "blackbox.jsonl")
        previous: dict[str, Any] | None = None
        previous_stage = ""
        for event in events:
            if event.get("event_type") not in ARTIFACT_RECORDS:
                continue
            for item in event.get("artifacts") or []:
                recorded = str(item.get("path", ""))
                if not recorded:
                    continue
                if resolve_recorded_path(run_dir, recorded, root_paths=roots) == target:
                    previous = item
                    previous_stage = str(event.get("stage") or "")
        if previous is None:
            raise RunError(
                f"artifact {path!r} was never recorded in this journal; "
                "submit it through its stage instead of re-recording it"
            )
        matches = _declared_for_path(
            run_dir, pipeline, registry, state, target, preferred_stage=previous_stage
        )
        if not matches:
            raise RunError(f"artifact {entry['path']!r} is not declared by any stage or task")
        stage, owner, task_id = matches[0]
        if owner and owner != role:
            raise JournalError(
                f"artifact {entry['path']!r} is owned by {owner}, not {role}"
            )
        if state["stage_status"].get(stage["id"]) == "pending":
            raise RunError(f"stage {stage['id']} is pending; nothing has been accepted there yet")
        if task_id:
            task = state["tasks"].get(task_id, {})
            if task.get("status") == "pending":
                raise RunError(f"{task_id} is pending; claim it before re-recording its artifacts")
        old_hash = str(previous.get("sha256", ""))
        new_hash = str(entry["sha256"])
        # Keep the path exactly as the record being superseded wrote it: `verify`
        # keys the "newest record wins" map by that string.
        recorded_path = str(previous.get("path") or entry["path"])
        if old_hash == new_hash:
            raise RunError(
                f"artifact {recorded_path!r} is unchanged ({new_hash[:8]}); nothing to re-record"
            )
        model = _model_for(role)
        event = record_event(
            run_dir,
            state,
            stage=stage["id"],
            task_id=task_id,
            role=role,
            event_type="ARTIFACT_RERECORD",
            why=reason,
            for_what=f"Keep {stage['id']} auditable after the artifact changed",
            action=(
                f"Re-recorded {recorded_path} {old_hash[:8]} -> {new_hash[:8]}: {reason}"
            ),
            verdict_status="DRAFT",
            verified_by=role,
            artifacts=[{**entry, "path": recorded_path, "old_sha256": old_hash}],
            evidence=evidence_list,
            reason=reason,
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def import_plan(run_id: str, *, root: str | Path | None = None) -> list[dict[str, Any]]:
    run_dir = safe_run_dir(run_id, root)
    with run_lock(run_dir):
        state = load_state(run_dir)
        prepared = _prepare_plan(run_dir, state)
        created = _import_plan_state(run_dir, state, prepared)
        # The state is saved even when no task was added: the plan may have
        # changed the owner, the requirements or the allowed files of a task
        # that already existed.  Losing those changes would leave the
        # producer working against a plan nobody agreed to.
        save_state(run_dir, state)
        return created


def task_claim(run_id: str, task_id: str, owner: str, *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    with run_lock(run_dir):
        state = load_state(run_dir)
        _require_current(state, pipeline, find_stage(pipeline, "s4_build"))
        claim_task(state, task_id, owner)
        model = _model_for(owner)
        event = record_event(
            run_dir, state, stage="s4_build", task_id=task_id, role=owner, event_type="TASK_CLAIM",
            why="Task claimed", for_what=f"Build {task_id}", action=f"{owner} claimed {task_id}",
            verdict_status="DRAFT", verified_by=owner, model_tier=model["tier"],
            model_id=model["model_id"], reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def task_submit(run_id: str, task_id: str, role: str, artifacts: list[str], *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    if not artifacts:
        raise RunError("at least one --artifact must be provided")
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    with run_lock(run_dir):
        state = load_state(run_dir)
        _require_current(state, pipeline, find_stage(pipeline, "s4_build"))
        submit_task(state, task_id, role, artifacts, run_dir=run_dir)
        entries = _task_entries(
            run_dir, state["tasks"][task_id], include_verification=False
        )
        model = _model_for(role)
        event = record_event(
            run_dir, state, stage="s4_build", task_id=task_id, role=role,
            event_type="ARTIFACT_SUBMIT", why="Task submitted", for_what=f"Review {task_id}",
            action=f"Submitted {task_id}", verdict_status="DRAFT", verified_by=role,
            artifacts=entries, model_tier=model["tier"], model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def task_accept(run_id: str, task_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        _require_current(state, pipeline, find_stage(pipeline, "s4_build"))
        task = get_task(state, task_id)
        if task["status"] != "review":
            raise RunError(f"task {task_id} is {task['status']}, cannot accept")
        if verification_policy(state) == "role":
            report_path, report_errors, verdict, reason = verification_report(
                run_dir, registry, stage_id="s4_build", task_id=task_id
            )
            if report_errors:
                raise VerificationError(
                    f"task {task_id} has no valid verification report:\n- "
                    + "\n- ".join(report_errors[:6])
                )
            if verdict != "PASS":
                raise VerificationError(
                    f"the verifier's report for {task_id} says {verdict}: "
                    f"{reason or 'no reason given'}"
                )
            if not _verdict_is_fresh(
                run_dir, stage_id="s4_build", task_id=task_id, report_path=report_path
            ):
                raise VerificationError(
                    f"the verifier's report for {task_id} predates the last "
                    "submission: the verifier has to look at the current "
                    "artifacts again before the task can be accepted"
                )
            _record_verification_locked(
                run_dir, state, registry, stage_id="s4_build", task_id=task_id,
                evidence=[f"task {task_id} acceptance"],
            )
        else:
            _write_verification(run_dir, task)
        accept_task(state, task_id)
        entries = _task_entries(run_dir, state["tasks"][task_id])
        model = _model_for(VERIFIER, "tier-3-frontier")
        event = record_event(
            run_dir, state, stage="s4_build", task_id=task_id, role=VERIFIER,
            event_type="TASK_DONE", why="Independent verification passed", for_what=f"Accept {task_id}",
            action=f"Accepted {task_id}", verdict_status="PASS", verified_by=VERIFIER,
            evidence=[f"task {task_id} accepted"], artifacts=entries, model_tier=model["tier"],
            model_id=model["model_id"], reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def task_reject(run_id: str, task_id: str, reason: str, *, root: str | Path | None = None) -> dict[str, Any]:
    validate_task_id(task_id)
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        _require_current(state, pipeline, find_stage(pipeline, "s4_build"))
        task = get_task(state, task_id)
        # A task that was never submitted, or already accepted, is not
        # something to reject: the transition would be a state the machine
        # never meant to allow.
        if task["status"] not in {"review", "in_progress"}:
            raise RunError(
                f"task {task_id} is {task['status']}; only a task in review or "
                "in progress can be rejected"
            )
        witness: list[dict[str, Any]] = []
        if verification_policy(state) == "role":
            report_path, report_errors, verdict, _report_reason = verification_report(
                run_dir, registry, stage_id="s4_build", task_id=task_id
            )
            if not report_errors and verdict == "FAIL":
                _record_verification_locked(
                    run_dir, state, registry, stage_id="s4_build", task_id=task_id,
                    evidence=[f"task {task_id} rejected: {reason}"],
                )
            if report_path.is_file():
                # A rejection is a judgement too.  Recording the bytes it
                # looked at is what lets `task_accept` tell a fresh report
                # from one that was already judged before the producer
                # changed the work: the producer can only take the task back
                # through a rejection or a human reset, and both are recorded.
                witness = _judged_entries(
                    run_dir, state, registry,
                    stage_id="s4_build", task_id=task_id, report_path=report_path,
                )
        reject_task(state, task_id, reason)
        model = _model_for(VERIFIER, "tier-3-frontier")
        event = record_event(
            run_dir, state, stage="s4_build", task_id=task_id, role=VERIFIER,
            event_type="REWORK", why="Independent verification failed", for_what=f"Rework {task_id}",
            action=f"Rejected {task_id}: {reason}", verdict_status="FAIL",
            verified_by=VERIFIER, evidence=[reason], reason_code="TASK_REJECTED",
            artifacts=witness,
            model_tier=model["tier"], model_id=model["model_id"], reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


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