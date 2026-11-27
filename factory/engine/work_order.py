"""Work-order construction: task-level and stage-level orders."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import (
    collect_artifact_entries,
    resolve_recorded_path,
    validate_artifact,
    validate_stage,
)
from ..errors import RunError
from ..pipeline import effective_roles, stage_artifacts
from ..state import load_state
from .common import VERIFIER, VERIFICATION_IDS, verification_policy
from .core import _collect_paths, _declared_artifact_base, _root_paths  # noqa: F401
from .verification import (
    _declared_tests,
    _feedback,
    _relative,
    _report_is_current,
    stage_declares_verification,
    verification_report,
)


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
    if status == "review":
        artifacts = [spec for spec in artifacts if spec.get("id") == "task_verification"]
    else:
        artifacts = [spec for spec in artifacts if spec.get("id") not in VERIFICATION_IDS]
    if status == "review":
        report = run_dir / "tasks" / str(task["task_id"]) / "verification" / "verification.md"
        report_rel = _relative(run_dir, report)
        deliverables = ", ".join(
            f"`{item}`"
            for item in [f"tasks/{task['task_id']}/deliverable.md"]
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
            action = "produce"
            instructions = (
                "Artifacts are submitted but deterministic validation fails: "
                + "; ".join(errors[:3])
            )
            next_command = f"factory verify --run-id {run_id} --stage {stage['id']}"
            pending = _pending_artifacts(run_dir, state, stage, pipeline, registry)
            artifacts = [
                spec for spec in artifacts if spec.get("id") not in VERIFICATION_IDS
            ]
        elif declares_witness and (
            report_errors
            or not _report_is_current(
                run_dir, stage_id=stage["id"], task_id=None, report_path=report_path
            )
        ):
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
        producer_errors = validate_stage(
            run_dir, stage, registry, state["tasks"], verification_policy=None
        )
        verify_due = bool(report_errors) or not _report_is_current(
            run_dir, stage_id=stage["id"], task_id=None, report_path=report_path
        )
        if declares_witness and not producer_pending and not producer_errors and verify_due:
            action, artifacts, instructions, next_command = verifier_step()
        else:
            action = "produce"
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
            order["pending_artifacts"] = [
                item
                for item in order["pending_artifacts"]
                if item["id"] not in VERIFICATION_IDS
            ]
    order["next_role"] = _next_role(order["pending_artifacts"], order, stage, None)
    return order
