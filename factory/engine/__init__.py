"""The run lifecycle: init, next, submit, gates, tasks and escalation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import (
    artifact_path,
    collect_artifact_entries,
    pattern_paths,
    resolve_recorded_path,
    substitute_path,
    validate_artifact,
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
from ..paths import runs_root, validate_run_id, validate_task_id
from ..pipeline import effective_owner, effective_roles, stage_artifacts
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

from .lifecycle import _archive_existing, init_run

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


def _check_stage_artifact_owners(
    run_dir: Path,
    stage: dict[str, Any],
    role: str,
    entries: list[dict[str, Any]],
    pipeline: dict[str, Any],
    state: dict[str, Any],
    registry: dict[str, Any],
) -> None:
    """Every submitted entry must belong to an artifact owned by the role."""
    policy = verification_policy(state)
    declared = []
    for spec in stage_artifacts(
        pipeline, registry, state["mode"], stage, run_dir=run_dir, verification_policy=policy
    ):
        if spec.get("per_task"):
            continue
        base, is_dir = _declared_artifact_base(run_dir, spec, stage_id=stage["id"])
        declared.append((str(spec.get("owner", "")), base, is_dir))
    roots = _root_paths(pipeline, registry, state["mode"], stage, run_dir, policy)
    for entry in entries:
        path = str(entry["path"])
        candidate = resolve_recorded_path(run_dir, path, root_paths=roots)
        owner = next(
            (
                candidate_owner
                for candidate_owner, base, is_dir in declared
                if candidate == base or (is_dir and base in candidate.parents)
            ),
            None,
        )
        if owner is None:
            raise RunError(f"artifact {path!r} is not declared for stage {stage['id']}")
        if owner != role:
            raise RunError(
                f"artifact {path!r} on stage {stage['id']} is owned by {owner}, not {role}"
            )


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


def _task_entries(
    run_dir: Path, task: dict[str, Any], *, include_verification: bool = True
) -> list[dict[str, Any]]:
    paths = [str(item) for item in task.get("artifacts", [])]
    task_id = str(task["task_id"])
    names = ["deliverable.md", "verification.md"]
    if include_verification:
        names.append("verification/verification.md")
    for name in names:
        candidate = run_dir / "tasks" / task_id / name
        if candidate.exists():
            paths.append(str(candidate))
    return collect_artifact_entries(run_dir, paths)


def _pending_artifacts(
    run_dir: Path,
    state: dict[str, Any],
    stage: dict[str, Any],
    pipeline: dict[str, Any],
    registry: dict[str, Any],
    *,
    task: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Declared artifacts of this order that are missing or fail validation.

    A work order that only lists `roles` and `artifacts` forces a runtime to
    guess who still owes what on a multi-role stage.  This is the answer.
    """
    specs = stage_artifacts(
        pipeline,
        registry,
        state["mode"],
        stage,
        task_id=task["task_id"] if task else None,
        run_dir=run_dir,
        verification_policy=verification_policy(state),
    )
    pending: list[dict[str, Any]] = []
    task_scope = state["tasks"] if task is None else {str(task["task_id"]): task}
    for spec in specs:
        if spec.get("per_task"):
            spec["stage"] = stage["id"]
        errors = validate_artifact(run_dir, spec, task_scope, stage_id=stage["id"])
        if not errors:
            continue
        owner = str(spec.get("owner", ""))
        if owner == "{task.owner}" and task:
            owner = str(task["owner"])
        # Report the concrete path: `{stage}` and `{task_id}` are already
        # resolved in `resolved_path`, and a prompt that names a literal
        # `{stage}` teaches the model to create that directory.
        declared_path = str(spec.get("path", ""))
        resolved = str(spec.get("resolved_path") or "")
        if resolved and Path(resolved).is_absolute():
            declared_path = _relative(run_dir, Path(resolved))
        pending.append(
            {
                "id": str(spec.get("id", spec.get("path", ""))),
                "path": declared_path,
                "owner": owner,
                "errors": errors[:3],
            }
        )
    return pending


def _next_role(
    pending: list[dict[str, Any]],
    order: dict[str, Any],
    stage: dict[str, Any],
    task: dict[str, Any] | None,
) -> str | None:
    if task is not None:
        if task.get("status") == "review":
            return VERIFIER
        return str(task.get("owner") or "") or None
    if order.get("action") == "verify":
        # The only stage-level verify order is the verifier's own step.
        return VERIFIER
    if order.get("action") == "gate":
        approver = str(stage["gate"]["approver"])
        return None if approver == "human" else approver
    if pending:
        owner = str(pending[0].get("owner") or "")
        if owner and not owner.startswith("{"):
            return owner
    for spec in order.get("artifacts") or []:
        owner = str(spec.get("owner") or "")
        if owner and not owner.startswith("{"):
            return owner
    roles = order.get("roles") or []
    return str(roles[0]) if roles else None


def _task_work_order(
    run_dir: Path,
    run_id: str,
    stage: dict[str, Any],
    task: dict[str, Any],
    pipeline: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    state = load_state(run_dir)
    policy = verification_policy(state)
    artifacts = stage_artifacts(
        pipeline,
        registry,
        state["mode"],
        stage,
        task_id=task["task_id"],
        run_dir=run_dir,
        verification_policy=policy,
    )
    status = task["status"]
    # A producer never gets the verifier's report: it is the witness, not an
    # input to the work it judges.  Only a verifier step carries it.
    if status == "review":
        artifacts = [spec for spec in artifacts if spec.get("id") == "task_verification"]
    else:
        artifacts = [spec for spec in artifacts if spec.get("id") not in VERIFICATION_IDS]
    if status == "review":
        # The verifier writes one report and gets nothing else writable: the
        # producer's deliverable and files are listed read-only on purpose.
        report = run_dir / "tasks" / str(task["task_id"]) / "verification" / "verification.md"
        report_rel = _relative(run_dir, report)
        deliverables = ", ".join(
            f"`{item}`" for item in [f"tasks/{task['task_id']}/deliverable.md"]
            + list(task.get("allowed_files") or [])
        )
        action = "verify"
        roles = [VERIFIER]
        instructions = (
            f"Task {task['task_id']} is submitted and waits for independent verification. "
            f"Read (do not edit) {deliverables}. Run at least three adversarial checks — "
            "one of them the task's own check command from `plan.md` — and write "
            f"`{report_rel}` with the commands and their real output. In the "
            "`- Вердикт:` line write exactly one word: `PASS` or `FAIL`. Leaving the "
            "template's `PASS | FAIL` choice in place is invalid: the runtime rejects "
            "the report and the step repeats. You may write only that report."
        )
        next_command = (
            f"factory task accept --run-id {run_id} --task-id {task['task_id']}"
        )
        feedback: list[str] = []
        declared: list[str] = []
    elif status == "in_progress":
        action = "produce"
        roles = [task["owner"]]
        instructions = (
            f"Task {task['task_id']} ({task['owner']}): finish the allowed files, run the "
            "check command, then submit them."
        )
        next_command = (
            f"factory task submit --run-id {run_id} --task-id {task['task_id']} "
            f"--role {task['owner']} --artifact <path>"
        )
        feedback = _feedback(run_dir, stage_id=stage["id"], task_id=str(task["task_id"]))
        declared = _declared_tests(run_dir, task)
    else:
        action = "produce"
        roles = [task["owner"]]
        instructions = (
            f"Task {task['task_id']} ({task['owner']}): claim it, then produce only the "
            "allowed files."
        )
        next_command = (
            f"factory task claim --run-id {run_id} --task-id {task['task_id']} "
            f"--role {task['owner']}"
        )
        feedback = _feedback(run_dir, stage_id=stage["id"], task_id=str(task["task_id"]))
        declared = _declared_tests(run_dir, task)
    pending_task = _pending_artifacts(run_dir, state, stage, pipeline, registry, task=task)
    if status != "review":
        # The producer's debt only; the verifier's report is not something this
        # role can or should produce.
        pending_task = [item for item in pending_task if item["id"] not in VERIFICATION_IDS]
    return {
        "schema_version": "1.0.0",
        "action": action,
        "run_id": run_id,
        "stage": stage["id"],
        "stage_title": stage["title"],
        "roles": roles,
        "task_id": task["task_id"],
        "artifacts": artifacts,
        "pending_artifacts": pending_task,
        "next_role": _next_role([], {"action": action}, stage, task),
        "instructions": instructions,
        "feedback": feedback,
        "declared_tests": declared,
        "gate": stage["gate"],
        "next_command": next_command,
    }


def _stage_work_order(
    run_dir: Path,
    run_id: str,
    state: dict[str, Any],
    stage: dict[str, Any],
    pipeline: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    policy = verification_policy(state)
    artifacts = stage_artifacts(
        pipeline, registry, state["mode"], stage, run_dir=run_dir, verification_policy=policy
    )
    gate = state["gate_status"].get(stage["gate"]["id"], {})
    status = state["stage_status"].get(stage["id"])
    approver = str(stage["gate"]["approver"])
    feedback: list[str] = []
    report_path, report_errors, report_verdict, _reason = (
        verification_report(run_dir, registry, stage_id=stage["id"])
        if policy == "role" and stage_declares_verification(registry, stage)
        else (run_dir, ["verification is not part of this run"], None, "")
    )
    declares_witness = (
        policy == "role"
        and approver == VERIFIER
        and stage_declares_verification(registry, stage)
    )

    def verifier_step() -> tuple[str, list[dict[str, Any]], str, str]:
        """The verifier's order: one report spec, so only its directory is writable."""
        report_rel = _relative(run_dir, report_path)
        readable = ", ".join(
            f"`{spec['path']}`" for spec in artifacts if spec.get("id") != "verification"
        )
        instructions = (
            f"Stage {stage['id']} artifacts validate; now verify them as "
            "`02-independent-verifier`. Read (do not edit) " + (readable or "—")
            + ". Run at least three adversarial checks — one of them "
            f"`factory verify --run-id {run_id} --stage {stage['id']}` — and write "
            f"`{report_rel}` with the commands and their real output. In the "
            "`- Вердикт:` line write exactly one word: `PASS` or `FAIL`. Leaving "
            "the template's `PASS | FAIL` choice in place is invalid: the runtime "
            "rejects the report and the step repeats. You may write only that report."
        )
        next_command = (
            f"factory verify --run-id {run_id} --stage {stage['id']} && "
            f"factory submit --run-id {run_id} --stage {stage['id']} "
            f"--role {VERIFIER} --artifact {report_rel}"
        )
        return (
            "verify",
            [spec for spec in artifacts if spec.get("id") == "verification"],
            instructions,
            next_command,
        )

    if gate.get("status") == "needs_human":
        action = "gate"
        instructions = f"Gate {stage['gate']['id']} needs a human decision."
        next_command = (
            f"factory gate approve --run-id {run_id} --gate {stage['gate']['id']} "
            "--approver human:<id> --evidence <text>"
        )
    elif status == "review":
        errors = validate_stage(
            run_dir, stage, registry, state["tasks"], verification_policy=policy
        )
        if errors:
            # The stage is in review but no longer validates (an artifact was
            # edited after submission, or a rule tightened).  That is a
            # producer's repair, not a verifier's job: the verifier has no
            # write scope for the files that are wrong.
            action = "produce"
            instructions = (
                "Artifacts are submitted but deterministic validation fails: "
                + "; ".join(errors[:3])
            )
            next_command = f"factory verify --run-id {run_id} --stage {stage['id']}"
            pending = _pending_artifacts(run_dir, state, stage, pipeline, registry)
            # The offender is a producer artifact; the witness stays out of
            # the repair's write scope — always, and not only when the report
            # happens to be valid.  A producer that may write the report may
            # write the verdict.
            artifacts = [
                spec for spec in artifacts if spec.get("id") not in VERIFICATION_IDS
            ]
        elif declares_witness and (
            report_errors
            or not _report_is_current(
                run_dir, stage_id=stage["id"], task_id=None, report_path=report_path
            )
        ):
            # A verdict that predates the bytes it judges is not a licence:
            # the verifier has to look at what the producer just submitted,
            # and the gate cannot close on an older revision.
            action, artifacts, instructions, next_command = verifier_step()
        else:
            action = "gate"
            instructions = "All required artifacts validate. Ask the gate approver to decide."
            gate_approver = "human:<id>" if approver == "human" else approver
            next_command = (
                f"factory gate approve --run-id {run_id} --gate {stage['gate']['id']} "
                f"--approver {gate_approver} --evidence <text>"
            )
    else:
        pending = _pending_artifacts(run_dir, state, stage, pipeline, registry)
        producer_pending = [item for item in pending if item["id"] not in VERIFICATION_IDS]
        # Artifact shape is not the whole contract: acceptance rows, REQ
        # parity, the plan graph and the task graph are stage-level rules.
        # If any of them still fails, the work belongs to a producer — the
        # verifier has no write scope to fix it, and asking it to look again
        # only burns an attempt.
        producer_errors = validate_stage(
            run_dir, stage, registry, state["tasks"], verification_policy=None
        )
        # The one rule for both branches: the stage moves on only with a valid
        # report that is newer than the last producer submission.  A FAIL the
        # producer has not answered yet is rework, not another verification.
        verify_due = bool(report_errors) or not _report_is_current(
            run_dir, stage_id=stage["id"], task_id=None, report_path=report_path
        )
        if declares_witness and not producer_pending and not producer_errors and verify_due:
            action, artifacts, instructions, next_command = verifier_step()
        else:
            action = "produce"
            # A producer never gets the verifier's report writable: the report
            # is the witness, not an input to the work it judges.
            artifacts = [
                spec for spec in artifacts if spec.get("id") not in VERIFICATION_IDS
            ]
            feedback = _feedback(run_dir, stage_id=stage["id"])
            instructions = (
                f"Stage {stage['id']}: produce the listed artifacts using the templates, "
                "then submit them. Do not write outside your role's scope."
            )
            next_command = (
                f"factory submit --run-id {run_id} --stage {stage['id']} "
                "--role <role> --artifact <path>"
            )
    order = {
        "schema_version": "1.0.0",
        "action": action,
        "run_id": run_id,
        "stage": stage["id"],
        "stage_title": stage["title"],
        # A verifier step is addressed to one role; a produce or gate order
        # still names every role active on the stage.
        "roles": [VERIFIER] if action == "verify" else effective_roles(pipeline, state["mode"], stage),
        "task_id": None,
        "artifacts": artifacts,
        "pending_artifacts": [],
        "next_role": None,
        "instructions": instructions,
        "feedback": feedback,
        "gate": stage["gate"],
        "next_command": next_command,
    }
    if action in {"produce", "verify", "gate"}:
        order["pending_artifacts"] = _pending_artifacts(
            run_dir, state, stage, pipeline, registry
        )
        if action == "produce":
            # A producer's order names the producer's debt only; the verifier's
            # report is not something this role can or should produce.
            order["pending_artifacts"] = [
                item
                for item in order["pending_artifacts"]
                if item["id"] not in VERIFICATION_IDS
            ]
    order["next_role"] = _next_role(order["pending_artifacts"], order, stage, None)
    return order


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