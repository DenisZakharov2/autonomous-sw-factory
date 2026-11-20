"""Human gates, approval tokens, and gate decision logic."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any

from ..config import PACKAGE_ROOT, load_artifacts, load_budget, load_pipeline
from ..errors import RunError, VerificationError
from ..journal import read_events, record_event, run_lock
from ..paths import run_dir as safe_run_dir, validate_task_id
from ..pipeline import effective_roles
from ..state import (
    find_stage,
    gate_record,
    load_state,
    save_state,
    set_current_stage,
    stage_gate,
)
from ..verify import parse_role, verify_run
from .common import VERIFIER, _model_for, verification_policy
from .core import (
    _import_plan_state,
    _prepare_plan,
    _reopen_stage_tasks,
    _require_current,
    _require_stage_status,
    _reset_rework_chain,
    _stage_entries,
)
from .verification import (
    _judged_entries,
    _latest_judgement,
    _record_verification_locked,
    _verdict_is_fresh,
    stage_declares_verification,
    verification_report,
)


APPROVAL_ACTIONS = frozenset({"approve", "reject", "accept-risk", "reset", "task-reset"})
MAX_APPROVAL_TTL_S = 24 * 60 * 60
DEFAULT_APPROVAL_TTL_S = 15 * 60
MIN_APPROVAL_KEY_BYTES = 32


def approval_key_bytes() -> bytes | None:
    """HMAC key for human decisions, or None when the gate is unauthenticated.

    A set but short key is refused: falling through to "unauthenticated" would
    turn a typo into a bypass. The key is the raw bytes of the variable — the
    same convention as the journal key file — and must be at least 32 bytes.
    """
    env = os.environ.get("FACTORY_APPROVAL_KEY", "")
    if not env.strip():
        return None
    raw = env.strip().encode("utf-8")
    if len(raw) < MIN_APPROVAL_KEY_BYTES:
        raise RunError(
            f"FACTORY_APPROVAL_KEY is {len(raw)} bytes; "
            f"need at least {MIN_APPROVAL_KEY_BYTES}"
        )
    return raw


def make_approval_token(
    *,
    key: bytes,
    run_id: str,
    subject: str,
    approver: str,
    action: str,
    ttl_s: int = DEFAULT_APPROVAL_TTL_S,
    now: int | None = None,
) -> str:
    """Token `exp.hmac` bound to one run, one subject, one approver, one action."""
    if action not in APPROVAL_ACTIONS:
        raise RunError(f"unknown approval action {action!r}")
    if not 1 <= int(ttl_s) <= MAX_APPROVAL_TTL_S:
        raise RunError(f"approval ttl must be 1..{MAX_APPROVAL_TTL_S} seconds")
    exp = int(now if now is not None else time.time()) + int(ttl_s)
    digest = hmac.new(
        key,
        f"{run_id}|{subject}|{approver}|{action}|{exp}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{exp}.{digest}"


def _check_human_approval(
    run_id: str, gate_id: str, approver: str, token: str | None, action: str
) -> str:
    """Optional HMAC check for human gates.

    With `FACTORY_APPROVAL_KEY` set, a human decision must carry a token
    `exp.HMAC(key, run_id|subject|approver|action|exp)`. The expiry is at most
    24 hours ahead, so a captured token cannot approve a different action or
    be replayed next week. Without the key the decision is recorded as
    unauthenticated: a local single-user setup cannot prove that a human,
    rather than the calling agent, typed `human:<id>`.
    """
    if action not in APPROVAL_ACTIONS:
        raise RunError(f"unknown approval action {action!r}")
    key = approval_key_bytes()
    if key is None:
        return "unauthenticated"
    if not token or token.count(".") != 1:
        raise RunError(
            "FACTORY_APPROVAL_KEY is set: a valid --approval-token is required "
            "for human gate decisions"
        )
    exp_text, digest = token.split(".", 1)
    try:
        exp = int(exp_text)
    except ValueError as exc:
        raise RunError("approval token expiry is not an integer") from exc
    now = int(time.time())
    if exp < now:
        raise RunError("approval token has expired; mint a new --approval-token")
    if exp > now + MAX_APPROVAL_TTL_S:
        raise RunError("approval token expiry is too far in the future")
    expected = hmac.new(
        key,
        f"{run_id}|{gate_id}|{approver}|{action}|{exp}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, digest):
        raise RunError(
            "approval token does not match this run, gate, approver and action"
        )
    return "hmac"


def _check_gate_approver(stage: dict[str, Any], approver: str) -> None:
    """A gate is decided only by its declared approver, on approve and on reject."""
    expected = stage["gate"]["approver"]
    gate_id = stage["gate"]["id"]
    if expected == "human":
        if not approver.startswith("human:"):
            raise RunError(f"gate {gate_id} requires a human approver, got {approver!r}")
    elif approver != expected:
        raise RunError(f"gate {gate_id} must be decided by {expected}, got {approver!r}")


def gate_approve(
    run_id: str,
    *,
    gate_id: str,
    approver: str,
    evidence: list[str],
    root: str | Path | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    stage = next((s for s in pipeline["stages"] if s["gate"]["id"] == gate_id), None)
    if stage is None:
        raise RunError(f"unknown gate: {gate_id}")
    _check_gate_approver(stage, approver)
    approval = (
        _check_human_approval(run_id, gate_id, approver, approval_token, "approve")
        if approver.startswith("human:")
        else "role"
    )
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        _require_current(state, pipeline, stage)
        _require_stage_status(state, stage, {"review"})
        errors = verify_run(run_dir, stage["id"], require_gate=False)
        if errors:
            raise VerificationError("gate preconditions failed:\n- " + "\n- ".join(errors))
        if verification_policy(state) == "role" and stage_declares_verification(
            registry, stage
        ):
            if approver != VERIFIER:
                raise VerificationError(
                    f"gate {gate_id} closes a stage that requires an independent "
                    f"verification report; only {VERIFIER} may approve it"
                )
            path, report_errors, verdict, reason = verification_report(
                run_dir, registry, stage_id=stage["id"]
            )
            if report_errors:
                raise VerificationError(
                    "gate requires a valid verification report:\n- "
                    + "\n- ".join(report_errors[:6])
                )
            if verdict != "PASS":
                raise VerificationError(
                    f"the verifier's report says {verdict}: {reason or 'no reason given'}"
                )
            if not _verdict_is_fresh(
                run_dir, stage_id=stage["id"], task_id=None, report_path=path
            ):
                raise VerificationError(
                    f"the verifier's report for {stage['id']} predates the last "
                    "submission: the verifier has to look at the current "
                    "artifacts again before this gate can close"
                )
            # The verdict is recorded before the gate decision, so the journal
            # always reads producer → VERIFY → GATE_DECISION.
            verify_event = _record_verification_locked(
                run_dir, state, registry, stage_id=stage["id"], task_id=None,
                evidence=[f"gate {gate_id} approval"],
            )
            evidence = list(evidence) + [f"VERIFY {verify_event['event_id']}"]
        entries = _stage_entries(run_dir, stage, pipeline, registry, state)
        prepared_plan = _prepare_plan(run_dir, state) if stage["id"] == "s3_plan" else None
        gate_record(state, stage, status="approved", decided_by=approver, evidence=evidence)
        if stage["id"] == "s1_spec":
            state["human_spec_approved"] = True
        if stage["id"] == "s_in":
            state["restore_drill_passed"] = True
        set_current_stage(state, pipeline)
        model = _model_for("00-orchestrator" if approver.startswith("human:") else approver, "tier-3-frontier")
        role = "00-orchestrator" if approver.startswith("human:") else approver
        event = record_event(
            run_dir,
            state,
            stage=stage["id"],
            task_id=None,
            role=role,
            event_type="GATE_DECISION",
            why=f"Gate {gate_id} decision",
            for_what=f"Close {stage['id']}",
            action=f"Gate {gate_id} approved by {approver} (approval: {approval})",
            verdict_status="PASS",
            verified_by=approver,
            evidence=evidence or ["gate approved"],
            artifacts=entries,
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        if prepared_plan:
            _import_plan_state(run_dir, state, prepared_plan)
        save_state(run_dir, state)
        return event


def gate_reject(
    run_id: str,
    *,
    gate_id: str,
    approver: str,
    evidence: list[str],
    root: str | Path | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    stage = next((s for s in pipeline["stages"] if s["gate"]["id"] == gate_id), None)
    if stage is None:
        raise RunError(f"unknown gate: {gate_id}")
    _check_gate_approver(stage, approver)
    approval = (
        _check_human_approval(run_id, gate_id, approver, approval_token, "reject")
        if approver.startswith("human:")
        else "role"
    )
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        _require_current(state, pipeline, stage)
        if verification_policy(state) == "role" and stage_declares_verification(
            registry, stage
        ):
            _path, report_errors, verdict, _reason = verification_report(
                run_dir, registry, stage_id=stage["id"]
            )
            if not report_errors and verdict == "FAIL":
                _record_verification_locked(
                    run_dir, state, registry, stage_id=stage["id"], task_id=None,
                    evidence=evidence or ["gate rejected"],
                )
        entries = _stage_entries(run_dir, stage, pipeline, registry, state)
        gate_record(state, stage, status="rejected", decided_by=approver, evidence=evidence)
        gate = stage_gate(state, stage)
        budget = load_budget()
        max_attempts = min(
            int(stage.get("max_attempts", budget["default_max_attempts"])),
            int(budget["max_veto_retries"]),
        )
        if gate["attempts"] >= max_attempts:
            exhausted = str(budget.get("on_attempts_exhausted", "escalate"))
            state["stage_status"][stage["id"]] = "blocked" if exhausted == "pause" else "escalated"
            state["veto_counters"][stage["id"]] = state["veto_counters"].get(stage["id"], 0) + 1
            summary = (
                f"Gate {gate_id} rejected by {approver}; "
                f"{gate['attempts']}/{max_attempts} attempts exhausted, escalated to human"
            )
        else:
            if stage["rework_to"] != stage["id"]:
                _reset_rework_chain(state, pipeline, stage["rework_to"], stage)
            else:
                # A stage that reworks into itself (`s4_build` and every
                # verifier stage) used to keep its tasks accepted: the next
                # order asked the verifier to look at the same files again and
                # the gate escalated after `max_attempts`.  The stage is already
                # `in_progress` (gate_record); its tasks go back to `pending`.
                _reopen_stage_tasks(state, stage["id"])
            summary = f"Gate {gate_id} rejected by {approver}; rework returned to {stage['rework_to']}"
        set_current_stage(state, pipeline)
        model = _model_for("00-orchestrator" if approver.startswith("human:") else approver)
        role = "00-orchestrator" if approver.startswith("human:") else approver
        event = record_event(
            run_dir,
            state,
            stage=stage["id"],
            task_id=None,
            role=role,
            event_type="GATE_DECISION",
            why=f"Gate {gate_id} rejected",
            for_what=f"Rework {stage['rework_to']}",
            action=f"{summary} (approval: {approval})",
            verdict_status="FAIL",
            verified_by=approver,
            evidence=evidence or ["gate rejected"],
            artifacts=entries,
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
            reason_code="GATE_REJECTED",
        )
        save_state(run_dir, state)
        return event


def accept_risk(
    run_id: str,
    *,
    gate_id: str,
    approver: str,
    risk: str,
    mitigation: str,
    expires: str | None = None,
    root: str | Path | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Close an escalated gate under an explicit, journaled human risk acceptance."""
    if not approver.startswith("human:"):
        raise RunError("risk acceptance requires a human approver (human:<id>)")
    if not risk.strip() or not mitigation.strip():
        raise RunError("risk and mitigation are required for a risk acceptance")
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    stage = next((s for s in pipeline["stages"] if s["gate"]["id"] == gate_id), None)
    if stage is None:
        raise RunError(f"unknown gate: {gate_id}")
    approval = _check_human_approval(run_id, gate_id, approver, approval_token, "accept-risk")
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        _require_current(state, pipeline, stage)
        status = state["stage_status"].get(stage["id"])
        if status not in {"escalated", "blocked"}:
            # A stage in `review` is waiting for its verdict, not carrying a
            # risk: accepting one there would close the stage behind the
            # verifier's back.
            raise RunError(f"stage {stage['id']} is {status}; there is no risk to accept")
        events = read_events(run_dir / "blackbox.jsonl")
        overridden = _latest_judgement(events, stage_id=stage["id"], task_id=None)
        entries = _stage_entries(run_dir, stage, pipeline, registry, state)
        evidence = [f"risk: {risk}", f"mitigation: {mitigation}"]
        if expires:
            evidence.append(f"expires: {expires}")
        if overridden is not None:
            evidence.append(
                f"overrides {overridden.get('event_id')} "
                f"({(overridden.get('verdict') or {}).get('status', 'unknown')})"
            )
        gate_record(state, stage, status="approved", decided_by=approver, evidence=evidence)
        if stage["id"] == "s_in":
            state["restore_drill_passed"] = True
        if stage["id"] == "s1_spec":
            state["human_spec_approved"] = True
        set_current_stage(state, pipeline)
        model = _model_for("00-orchestrator", "tier-3-frontier")
        event = record_event(
            run_dir,
            state,
            stage=stage["id"],
            task_id=None,
            role="00-orchestrator",
            event_type="RISK_ACCEPTANCE",
            why=f"Risk accepted for gate {gate_id}",
            for_what=f"Close {stage['id']} under accepted risk",
            action=f"Risk accepted by {approver}: {risk} (approval: {approval})",
            verdict_status="ACCEPTED_RISK",
            verified_by=approver,
            evidence=evidence,
            artifacts=entries,
            # The verdict this decision stands against, if there was one.  A
            # risk acceptance that overrides nothing is not an override.
            parent_event_id=(
                str(overridden.get("event_id")) if overridden is not None else None
            ),
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def stage_reset(
    run_id: str,
    *,
    stage_id: str,
    approver: str,
    reason: str,
    root: str | Path | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Reset an escalated/blocked stage after a human decision."""
    if not approver.startswith("human:"):
        raise RunError("stage reset requires a human approver (human:<id>)")
    if not reason.strip():
        raise RunError("a reset reason is required")
    _check_human_approval(run_id, stage_id, approver, approval_token, "reset")
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    stage = find_stage(pipeline, stage_id)
    with run_lock(run_dir):
        state = load_state(run_dir)
        _require_current(state, pipeline, stage)
        status = state["stage_status"].get(stage["id"])
        if status not in {"escalated", "blocked", "review", "in_progress"}:
            raise RunError(f"stage {stage['id']} is {status}; nothing to reset")
        state["stage_status"][stage["id"]] = "pending"
        gate = stage_gate(state, stage)
        gate["status"] = "pending"
        gate["attempts"] = 0
        gate["decided_by"] = None
        gate["decided_at"] = None
        gate["evidence"] = []
        state["veto_counters"][stage["id"]] = 0
        for task in state["tasks"].values():
            if task.get("stage") == stage["id"] and task.get("status") in {
                "escalated", "blocked", "review", "in_progress",
            }:
                task["status"] = "pending"
                task["attempts"] = 0
                task["lease"] = None
        set_current_stage(state, pipeline)
        model = _model_for("00-orchestrator", "tier-3-frontier")
        event = record_event(
            run_dir,
            state,
            stage=stage["id"],
            task_id=None,
            role="00-orchestrator",
            event_type="STAGE_RESET",
            why="Human reset an escalated stage",
            for_what=f"Retry {stage['id']}",
            action=f"Stage {stage['id']} reset by {approver}: {reason}",
            verdict_status="BLOCKED",
            verified_by=approver,
            evidence=[reason],
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def task_reset(
    run_id: str,
    *,
    task_id: str,
    approver: str,
    reason: str,
    root: str | Path | None = None,
    approval_token: str | None = None,
) -> dict[str, Any]:
    if not approver.startswith("human:"):
        raise RunError("task reset requires a human approver (human:<id>)")
    validate_task_id(task_id)
    _check_human_approval(run_id, task_id, approver, approval_token, "task-reset")
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    stage = find_stage(pipeline, "s4_build")
    with run_lock(run_dir):
        state = load_state(run_dir)
        registry = load_artifacts(state["mode"])
        _require_current(state, pipeline, stage)
        task = state["tasks"].get(task_id)
        if task is None:
            raise RunError(f"unknown task: {task_id}")
        if task["status"] not in {"escalated", "blocked", "review", "in_progress"}:
            raise RunError(f"task {task_id} is {task['status']}; nothing to reset")
        task["status"] = "pending"
        task["attempts"] = 0
        task["lease"] = None
        witness: list[dict[str, Any]] = []
        if verification_policy(state) == "role":
            report_path, _errors, _verdict, _reason = verification_report(
                run_dir, registry, stage_id="s4_build", task_id=task_id
            )
            if report_path.is_file():
                # A reset sends the task back to the producer, so the report
                # that was on disk stops being a verdict about the new work.
                witness = _judged_entries(
                    run_dir, state, registry,
                    stage_id="s4_build", task_id=task_id, report_path=report_path,
                )
        model = _model_for("00-orchestrator", "tier-3-frontier")
        event = record_event(
            run_dir,
            state,
            stage="s4_build",
            task_id=task_id,
            role="00-orchestrator",
            event_type="TASK_RESET",
            why="Human reset an escalated task",
            for_what=f"Retry {task_id}",
            action=f"Task {task_id} reset by {approver}: {reason}",
            verdict_status="BLOCKED",
            verified_by=approver,
            evidence=[reason],
            artifacts=witness,
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event


def veto(
    run_id: str,
    *,
    stage_id: str,
    role: str,
    code: str,
    evidence: list[str],
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Blocking veto by a role that owns one on the current stage."""
    if not evidence:
        raise RunError("a veto requires evidence")
    run_dir = safe_run_dir(run_id, root)
    pipeline = load_pipeline()
    stage = find_stage(pipeline, stage_id)
    card = PACKAGE_ROOT / "roles" / f"{role}.md"
    if not card.exists():
        raise RunError(f"unknown role: {role}")
    frontmatter, _ = parse_role(card)
    if frontmatter.get("veto_type") != "blocking":
        raise RunError(f"role {role} has no blocking veto")
    with run_lock(run_dir):
        state = load_state(run_dir)
        _require_current(state, pipeline, stage)
        if role not in effective_roles(pipeline, state["mode"], stage):
            raise RunError(f"role {role} is not active on {stage_id}")
        budget = load_budget()
        limit = int(budget["max_veto_retries"])
        state["veto_counters"][stage["id"]] = state["veto_counters"].get(stage["id"], 0) + 1
        count = state["veto_counters"][stage["id"]]
        if count >= limit:
            state["stage_status"][stage["id"]] = "escalated"
            action = f"Veto {code} by {role}: limit {limit} reached, escalated to human"
        else:
            state["stage_status"][stage["id"]] = "in_progress"
            _reopen_stage_tasks(state, stage["id"])
            if stage["id"] == "s1_spec":
                state["human_spec_approved"] = False
            elif stage["id"] == "s_in":
                state["restore_drill_passed"] = False
            gate = stage_gate(state, stage)
            gate["status"] = "pending"
            action = f"Veto {code} by {role}: stage reopened for rework"
        set_current_stage(state, pipeline)
        model = _model_for(role)
        event = record_event(
            run_dir,
            state,
            stage=stage["id"],
            task_id=None,
            role=role,
            event_type="VETO",
            why=f"Blocking veto {code}",
            for_what=f"Rework {stage['id']}",
            action=action,
            verdict_status="FAIL",
            verified_by=role,
            evidence=evidence,
            reason_code=code,
            model_tier=model["tier"],
            model_id=model["model_id"],
            reasoning_effort=model["effort"],
        )
        save_state(run_dir, state)
        return event