"""Verification bookkeeping: reports, staleness, freshness and judgements."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..artifacts import (
    artifact_path,
    collect_artifact_entries,
    resolve_recorded_path,
    sha256_dir,
    validate_artifact,
    validate_markdown,
    validate_verification_report,
    verification_reason,
    verification_verdict,
)
from ..config import load_artifacts, load_pipeline
from ..errors import ConfigError, RunError, VerificationError
from ..journal import (
    in_verdict_scope,
    read_events,
    record_event,
    run_lock,
    sha256_file,
)
from ..paths import run_dir as safe_run_dir, safe_write_text
from ..state import find_stage, load_state, save_state
from .common import VERIFIER, _model_for, verification_policy


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
    safe_write_text(
        path,
        f"# Проверка {task_id}\n\n"
        "## 1. Артефакты\n\n"
        f"{artifacts}\n\n"
        "## 2. Вердикт\n\n"
        "PASS: принято без независимого верификатора — прогон без "
        "`verification_policy`. Это не проверка верификатора, и событие "
        "`TASK_DONE` в `blackbox.jsonl` говорит ровно это.\n",
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
    if verdict is None:
        # `python -O` drops asserts, and the verdict would reach the journal
        # as `None`: a fail-open by accident instead of by design.
        raise VerificationError(
            "the verification report states no verdict; it cannot be recorded"
        )
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
    replay: Any | None = None
    if verdict == "PASS":
        from ..host_verify import replay_report

        try:
            stage_spec = find_stage(load_pipeline(), stage_id)
        except RunError:
            stage_spec = {}
        accepts_code = any(
            str(item) in {"product_src", "product_tests"}
            for item in stage_spec.get("artifacts") or []
        )
        replay = replay_report(
            path,
            run_dir,
            require_executable=task_id is None and accepts_code,
            registry=registry,
        )
        if replay.errors:
            raise VerificationError(
                "the verification report's checks do not survive the host's replay:\n- "
                + "\n- ".join(replay.errors[:6])
            )
    reason_text = f"; reason: {reason}" if reason else ""
    event_evidence = list(evidence) + [f"{relative}: sha256 {digest[:12]} verdict {verdict}{reason_text}"]
    if replay is not None:
        event_evidence.extend(replay.evidence)
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

    `_stage_entries` lives in the package `__init__`; importing it at module
    level would make the package and this module import each other.

    A verdict is a statement about bytes.  Recording the hashes of the
    artifacts it judged is what lets a later reader tell whether the verdict
    still describes what is on disk — the answer comes from the journal, not
    from file timestamps the executor could forge.

    Paths that are gone are left out: the verdict is about what existed when
    it was made, and the artifact hashes cover the rest.
    """
    from . import _stage_entries

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
