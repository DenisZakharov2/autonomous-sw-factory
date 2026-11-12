"""The run lifecycle: init, next, submit, gates, tasks and escalation."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import (
    artifact_path,
    collect_artifact_entries,
    pattern_paths,
    plan_rows,
    policy_allows,
    resolve_recorded_path,
    sha256_dir,
    substitute_path,
    validate_artifact,
    validate_markdown,
    validate_stage,
    validate_verification_report,
    verification_reason,
    verification_verdict,
)
from .config import (
    PACKAGE_ROOT,
    load_artifacts,
    load_budget,
    load_pipeline,
    resolve_model,
    validate_against_schema,
)
from .errors import ConfigError, JournalError, RunError, VerificationError
from .journal import (
    ARTIFACT_RECORDS,
    in_verdict_scope,
    read_events,
    record_event,
    run_lock,
    sha256_file,
)
from .paths import run_dir as safe_run_dir
from .paths import runs_root, validate_run_id, validate_task_id
from .pipeline import effective_owner, effective_roles, stage_artifacts
from .state import (
    accept_task,
    add_task,
    claim_task,
    current_stage,
    find_stage,
    gate_record,
    get_task,
    load_state,
    new_state,
    reject_task,
    save_state,
    stage_gate,
    submit_task,
)
from .verify import parse_role, verify_run

VERIFIER = "02-independent-verifier"
VERIFICATION_IDS = {"verification", "task_verification"}


def verification_policy(state: dict[str, Any]) -> str | None:
    """`role` for runs whose gates execute the verifier; `None` for history."""
    return str(state.get("verification_policy") or "") or None


def _archive_existing(run_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = run_dir.parent / ".archive" / f"{run_dir.name}-{stamp}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(archive))
    return archive


def _model_for(role: str, fallback: str = "tier-2-balanced") -> dict[str, str]:
    """Resolve the model tier declared by the role card, not a hard-coded one."""
    path = PACKAGE_ROOT / "roles" / f"{role}.md"
    if path.exists():
        try:
            frontmatter, _ = parse_role(path)
            tier = str(frontmatter.get("default_model_tier", fallback))
            effort = str(frontmatter.get("reasoning_effort", "medium"))
            model = dict(resolve_model(tier))
            model["effort"] = effort
            return model
        except Exception:
            pass
    return resolve_model(fallback)


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


def _write_verification(run_dir: Path, task: dict[str, Any]) -> Path:
    """Legacy stub for historical runs that predate the verifier step.

    A run with `verification_policy: role` never reaches this function: its
    report is written by `02-independent-verifier` and must pass
    `validate_verification_report`.  Keeping the stub for old runs means an
    operator can still accept a task in a journal that never had the step,
    without rewriting history.
    """
    task_id = str(task["task_id"])
    path = run_dir / "tasks" / task_id / "verification.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    artifacts = "\n".join(f"- `{item}`" for item in task.get("artifacts", [])) or "- none"
    path.write_text(
        f"# Проверка {task_id}\n\n"
        "## 1. Артефакты\n\n"
        f"{artifacts}\n\n"
        "## 2. Вердикт\n\n"
        "PASS: `02-independent-verifier` принял задачу; факт и хеши зафиксированы "
        "событием `TASK_DONE` в `blackbox.jsonl`.\n",
        encoding="utf-8",
    )
    return path


def _verification_spec(registry: dict[str, Any], *, task: bool) -> dict[str, Any]:
    artifact_id = "task_verification" if task else "verification"
    spec = dict(registry["artifacts"][artifact_id])
    spec["id"] = artifact_id
    return spec


def stage_declares_verification(registry: dict[str, Any], stage: dict[str, Any]) -> bool:
    return "verification" in stage["artifacts"] and "verification" in registry["artifacts"]


def verification_report(
    run_dir: Path,
    registry: dict[str, Any],
    *,
    stage_id: str,
    task_id: str | None = None,
) -> tuple[Path, list[str], str | None, str]:
    """`(path, errors, verdict, reason)` for a report, valid or not.

    A report is valid only when it exists, carries the required sections, has
    at least three checks with a command and an output each, and states one
    verdict — never the template's `PASS | FAIL` choice.
    """
    spec = _verification_spec(registry, task=task_id is not None)
    path = artifact_path(run_dir, spec, task_id=task_id, stage_id=stage_id)
    if not path.is_file():
        return path, [f"missing artifact: {path}"], None, ""
    text = path.read_text(encoding="utf-8")
    errors = validate_markdown(path, list(spec.get("required_sections") or []))
    errors.extend(validate_verification_report(path, require_deterministic_check=task_id is None))
    verdict, verdict_errors = verification_verdict(text)
    return path, errors, verdict, verification_reason(text)


def _relative(run_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _latest_verify(
    events: list[dict[str, Any]], *, stage_id: str, task_id: str | None
) -> dict[str, Any] | None:
    """The newest VERIFY event for this stage (or task), ignoring other stages."""
    for event in reversed(events):
        if event.get("event_type") != "VERIFY":
            continue
        if task_id is not None:
            if event.get("task_id") == task_id:
                return event
        elif event.get("stage") == stage_id and not event.get("task_id"):
            return event
    return None


def verification_is_stale(
    run_dir: Path,
    *,
    stage_id: str,
    task_id: str | None = None,
    events: list[dict[str, Any]] | None = None,
) -> bool:
    """True when the newest verdict no longer describes the current artifacts.

    A report is a statement about specific files.  If a producer submitted
    after it, or a veto or a rejected gate reopened the stage, the old PASS is
    history, not a licence: the verifier has to look again.
    """
    events = events if events is not None else read_events(run_dir / "blackbox.jsonl")
    last: int | None = None
    for index, event in enumerate(events):
        if event.get("event_type") != "VERIFY":
            continue
        if task_id is not None:
            if event.get("task_id") == task_id:
                last = index
        elif event.get("stage") == stage_id and not event.get("task_id"):
            last = index
    if last is None:
        return True
    for event in events[last + 1 :]:
        if task_id is not None:
            if event.get("task_id") != task_id:
                continue
        elif str(event.get("stage") or "") != stage_id or event.get("task_id"):
            continue
        event_type = str(event.get("event_type") or "")
        if event_type == "VETO":
            return True
        if event_type == "GATE_DECISION" and (event.get("verdict") or {}).get("status") != "PASS":
            return True
        if event_type == "ARTIFACT_SUBMIT":
            # The verifier submits its own report; that is not new evidence
            # about the producers' work.  Any other submit is.
            if str(event.get("role") or "") == VERIFIER:
                continue
            return True
    return False


def _record_verification_locked(
    run_dir: Path,
    state: dict[str, Any],
    registry: dict[str, Any],
    *,
    stage_id: str,
    task_id: str | None,
    evidence: list[str],
) -> dict[str, Any]:
    """Write one VERIFY event for the report currently on disk.

    The event is idempotent per report hash: re-approving after a crash does
    not mint a second witness for the same bytes.  The caller must hold the
    run lock.
    """
    path, errors, verdict, reason = verification_report(
        run_dir, registry, stage_id=stage_id, task_id=task_id
    )
    if errors:
        raise VerificationError(
            "verification report is not valid:\n- " + "\n- ".join(errors[:6])
        )
    assert verdict is not None
    relative = _relative(run_dir, path)
    digest = sha256_file(path)
    events = read_events(run_dir / "blackbox.jsonl")
    latest = _latest_verify(events, stage_id=stage_id, task_id=task_id)
    if latest is not None and not verification_is_stale(
        run_dir, stage_id=stage_id, task_id=task_id, events=events
    ):
        same_verdict = str((latest.get("verdict") or {}).get("status")) == verdict
        same_report = any(
            str(item.get("path")) == relative and str(item.get("sha256")) == digest
            for item in latest.get("artifacts") or []
            if isinstance(item, dict)
        )
        if same_verdict and same_report:
            return latest
    reason_text = f"; reason: {reason}" if reason else ""
    event_evidence = list(evidence) + [f"{relative}: sha256 {digest[:12]} verdict {verdict}{reason_text}"]
    model = _model_for(VERIFIER, "tier-3-frontier")
    return record_event(
        run_dir,
        state,
        stage=stage_id,
        task_id=task_id,
        role=VERIFIER,
        event_type="VERIFY",
        why="Independent verification of the declared artifacts",
        for_what=f"Decide {'task ' + task_id if task_id else stage_id}",
        action=f"VERIFY {verdict} for {'task ' + task_id if task_id else stage_id}",
        verdict_status=verdict,
        verified_by=VERIFIER,
        artifacts=_judged_entries(
            run_dir, state, registry, stage_id=stage_id, task_id=task_id, report_path=path
        ),
        evidence=event_evidence,
        model_tier=model["tier"],
        model_id=model["model_id"],
        reasoning_effort=model["effort"],
        # Only a FAIL carries a reason worth replaying: a PASS report's
        # `Причина` line is template noise, and leaving it out keeps a passing
        # VERIFY byte-identical to the one 1.0.2 wrote.
        reason=reason if verdict == "FAIL" else "",
    )


def _judged_entries(
    run_dir: Path,
    state: dict[str, Any],
    registry: dict[str, Any],
    *,
    stage_id: str,
    task_id: str | None,
    report_path: Path,
) -> list[dict[str, Any]]:
    """The artifact entries a verdict judges, the report included.

    A verdict is a statement about bytes.  Recording the hashes of the
    artifacts it judged is what lets a later reader tell whether the verdict
    still describes what is on disk — the answer comes from the journal, not
    from file timestamps the executor could forge.

    Paths that are gone are left out: the verdict is about what existed when
    it was made, and the artifact hashes cover the rest.
    """
    pipeline = load_pipeline()
    stage = find_stage(pipeline, stage_id)
    if task_id is not None:
        task = state["tasks"].get(task_id) or {}
        base = run_dir / "tasks" / str(task_id)
        paths = [str(item) for item in task.get("artifacts") or []]
        paths += [str(base / name) for name in ("deliverable.md", "verification/verification.md")]
        paths = [
            raw
            for raw in paths
            if (Path(raw) if Path(raw).is_absolute() else run_dir / raw).exists()
        ]
        entries = collect_artifact_entries(run_dir, paths) if paths else []
    else:
        entries = _stage_entries(run_dir, stage, pipeline, registry, state)
    report = _report_entry(run_dir, report_path)
    # The report leads: it is the artifact the event is about, and the judged
    # bytes follow it.
    judged = [item for item in entries if str(item.get("path")) != report["path"]]
    return [report, *judged]


def _report_entry(run_dir: Path, path: Path) -> dict[str, Any]:
    return {
        "path": _relative(run_dir, path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def record_verification(
    run_id: str,
    *,
    stage_id: str,
    task_id: str | None = None,
    evidence: list[str],
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Record the verifier's verdict from the report on disk."""
    run_dir = safe_run_dir(run_id, root)
    with run_lock(run_dir):
        state = load_state(run_dir)
        if verification_policy(state) != "role":
            raise RunError(f"run {run_id} has no role verification policy")
        registry = load_artifacts(state["mode"])
        event = _record_verification_locked(
            run_dir, state, registry, stage_id=stage_id, task_id=task_id, evidence=evidence
        )
        save_state(run_dir, state)
        return event


def _verify_fail_reason(event: dict[str, Any]) -> str:
    """The verifier's own words for a FAIL: the event field, or the pre-1.0.3 line."""
    reason = str(event.get("reason") or "").strip()
    if reason:
        return reason
    for item in (event.get("verdict") or {}).get("evidence") or []:
        text = str(item)
        if "reason: " in text:
            return text.split("reason: ", 1)[1].strip()
    return ""


def _feedback(
    run_dir: Path, *, stage_id: str, task_id: str | None = None
) -> list[str]:
    """The verifier's last FAIL for this task and for its stage, from the journal.

    The producer is the only role that can repair a rejection, and until now
    the reason lived only in `GATE_DECISION.evidence` — invisible in the next
    work order and lost on restart.  Reading the journal (not the report on
    disk) keeps the note even when the report is about older bytes.
    """
    events = read_events(run_dir / "blackbox.jsonl")
    subjects: list[tuple[str | None, str]] = []
    if task_id:
        subjects.append((task_id, f"задача {task_id}"))
    subjects.append((None, f"стадия {stage_id}"))
    notes: list[str] = []
    for subject, label in subjects:
        latest = _latest_verify(events, stage_id=stage_id, task_id=subject)
        if latest is None or str((latest.get("verdict") or {}).get("status")) != "FAIL":
            continue
        reason = _verify_fail_reason(latest) or "вердикт FAIL без причины"
        notes.append(f"{label}: {reason} (VERIFY FAIL, {latest.get('event_id')})")
    return notes


def _declared_tests(run_dir: Path, task: dict[str, Any]) -> list[str]:
    """The test references `traceability.json` froze for this task's REQs.

    The matrix is written at `s1_spec`, before any code exists, so these names
    are a contract: a builder that invents its own names leaves the REQ
    uncovered while every check stays green.
    """
    path = run_dir / "artifacts" / "traceability.json"
    if not path.is_file():
        return []
    try:
        requirements = json.loads(path.read_text(encoding="utf-8")).get("requirements") or {}
    except (OSError, json.JSONDecodeError):
        return []
    reqs = set(task.get("reqs") or [])
    tests: list[str] = []
    for req, item in requirements.items():
        if req not in reqs or not isinstance(item, dict):
            continue
        tests.extend(str(ref) for ref in item.get("tests") or [] if ref)
    return list(dict.fromkeys(tests))


def _report_is_current(
    run_dir: Path, *, stage_id: str, task_id: str | None, report_path: Path
) -> bool:
    """True when no producer submission came after this exact report was submitted.

    A stage sits in `review` either because the verifier submitted its report
    or because a producer submitted a fix.  Only the first may reach a gate: a
    verdict that predates the bytes it judges is history, and `verification_is_stale`
    cannot tell the two apart — it has no VERIFY event to compare with yet.
    """
    if not report_path.is_file():
        return False
    relative = _relative(run_dir, report_path)
    digest = sha256_file(report_path)
    report_seq = producer_seq = 0
    for event in read_events(run_dir / "blackbox.jsonl"):
        if event.get("event_type") != "ARTIFACT_SUBMIT":
            continue
        if str(event.get("stage") or "") != stage_id:
            continue
        if task_id is not None and str(event.get("task_id") or "") != task_id:
            continue
        # A stage report covers the whole product tree, so a task's submission
        # invalidates it too; a task report is only about that task.
        seq = int(event.get("seq") or 0)
        if str(event.get("role") or "") == VERIFIER:
            if any(
                str(item.get("path")) == relative and str(item.get("sha256")) == digest
                for item in event.get("artifacts") or []
                if isinstance(item, dict)
            ):
                report_seq = max(report_seq, seq)
        else:
            producer_seq = max(producer_seq, seq)
    return report_seq > producer_seq


def _latest_judgement(
    events: list[dict[str, Any]], *, stage_id: str, task_id: str | None
) -> dict[str, Any] | None:
    """The newest event that judged this subject: a verdict or a rejection.

    Both carry the artifact hashes that were on disk when the verifier
    looked, which is what makes "is this verdict still about the current
    bytes?" answerable from the journal alone.
    """
    for event in reversed(events):
        if event.get("event_type") not in {"VERIFY", "REWORK"}:
            continue
        if in_verdict_scope(event, stage_id=stage_id, task_id=task_id):
            return event
    return None


def _verdict_is_fresh(
    run_dir: Path, *, stage_id: str, task_id: str | None, report_path: Path
) -> bool:
    """Is the report on disk still a verdict about the bytes on disk?

    A verdict is a statement about specific bytes.  The `VERIFY` event
    records the hashes of the artifacts it judged, so the question has an
    answer that does not depend on file timestamps — those belong to the
    executor and can be forged.

    The verdict is stale when the judged bytes moved after it was recorded
    *and* the report on disk is the very report that was recorded: then the
    report still describes the old bytes.  A report the verifier wrote after
    the change is a new verdict about the new bytes and is accepted.

    Fail-closed: a report that cannot be placed against the journal is not
    fresh.
    """
    if not report_path.is_file():
        return False
    if task_id is None and not _report_is_current(
        run_dir, stage_id=stage_id, task_id=None, report_path=report_path
    ):
        # A stage report reaches the gate through the verifier's handover, and
        # the handover is what says the verifier saw the current artifacts.  A
        # task report has no handover of its own: the runtime hands the task
        # over to `task_accept` in the same breath, and `task_reject` records
        # the bytes it judged.
        return False
    events = read_events(run_dir / "blackbox.jsonl")
    judgement = _latest_judgement(events, stage_id=stage_id, task_id=task_id)
    if judgement is None:
        # The first verdict for this subject: there is nothing to be stale
        # against.  A producer that changed the work without submitting it is
        # caught by the artifact hashes instead.
        return True
    relative = _relative(run_dir, report_path)
    recorded_report = ""
    moved = False
    for item in judgement.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if path == relative:
            recorded_report = str(item.get("sha256") or "")
            continue
        target = resolve_recorded_path(run_dir, path)
        try:
            current = sha256_dir(target) if path.endswith("/") else sha256_file(target)
        except (OSError, ConfigError):
            moved = True
            continue
        if current != str(item.get("sha256") or ""):
            moved = True
    if not moved:
        return True
    # The bytes moved.  Only a report the verifier wrote after that can
    # describe them.
    return bool(recorded_report) and sha256_file(report_path) != recorded_report


def _task_deliverable_errors(
    run_dir: Path, task: dict[str, Any], registry: dict[str, Any]
) -> list[str]:
    """A task is acceptable only when its deliverable and files are on disk."""
    errors: list[str] = []
    spec = dict(registry["artifacts"]["deliverable"])
    spec["id"] = "deliverable"
    spec["stage"] = "s4_build"
    errors.extend(
        validate_artifact(
            run_dir, spec, {str(task["task_id"]): task}, stage_id="s4_build"
        )
    )
    for path in task.get("artifacts") or []:
        candidate = run_dir / str(path)
        if not candidate.exists():
            errors.append(f"missing task artifact: {path}")
    return errors


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
        "product/migrations",
    ]:
        (target / sub).mkdir(parents=True, exist_ok=True)
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
        current_stage(state, pipeline)
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
        current_stage(state, pipeline)
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
        current_stage(state, pipeline)
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
        current_stage(state, pipeline)
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
        current_stage(state, pipeline)
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


def _validate_plan_graph(tasks: list[dict[str, Any]], mode_roles: list[str]) -> None:
    ids = {task["task_id"] for task in tasks}
    owners = {task["task_id"]: task.get("owner", "") for task in tasks}
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


def import_plan(run_id: str, *, root: str | Path | None = None) -> list[dict[str, Any]]:
    run_dir = safe_run_dir(run_id, root)
    with run_lock(run_dir):
        state = load_state(run_dir)
        prepared = _prepare_plan(run_dir, state)
        created = _import_plan_state(run_dir, state, prepared)
        if created:
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
