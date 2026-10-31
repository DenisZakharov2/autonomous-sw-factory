"""Append-only, hash-chained flight recorder.

Design rules:
- one writer at a time via an advisory file lock;
- `seq` and `prev_hash` are read and written inside the lock;
- every event is validated against `schemas/event.schema.json`;
- `PASS` is only accepted from the independent verifier, except for human
  gate/risk decisions;
- `state.json` is updated atomically with the last hash and event count, which
  makes tail truncation detectable;
- `AUDIT_TRAIL.md` is a rendered mirror, never a second source of truth.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import validate_against_schema
from .errors import ConfigError, JournalError
from .paths import ensure_within

try:  # Linux/macOS
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

GENESIS_PREV_HASH = "0" * 64
EVENT_TYPES = {
    "INIT", "STAGE_RESET", "TASK_ADD", "TASK_CLAIM", "TASK_DONE", "TASK_RESET",
    "ARTIFACT_SUBMIT", "ARTIFACT_RERECORD", "GATE_DECISION", "VETO", "REWORK",
    "RISK_ACCEPTANCE", "SKILL_INSTALL",
}
VERDICTS = {"INIT_OK", "DRAFT", "PASS", "FAIL", "NEEDS_HUMAN", "ACCEPTED_RISK", "BLOCKED"}
HUMAN_EXEMPT = {"GATE_DECISION", "RISK_ACCEPTANCE"}
# Events that bind the hash of an artifact to the file on disk.  `verify`
# compares the newest of these per path with the file.
ARTIFACT_RECORDS = {"GATE_DECISION", "TASK_DONE", "ARTIFACT_SUBMIT", "ARTIFACT_RERECORD"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_event_hash(prev_hash: str, payload_without_hash: dict[str, Any]) -> str:
    raw = (prev_hash + canonical_json(payload_without_hash)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _locked(lock_path: Path):
    """Exclusive advisory lock around read-tail/write/update.

    POSIX uses `flock`; Windows has no `fcntl`, and silently dropping the lock
    would let two processes mint the same `seq`/`prev_hash`.  `msvcrt.locking`
    locks one byte at offset 0 of the same file, with the same exclusive
    semantics; a mixed POSIX/Windows share is not supported, which matches the
    rest of the toolchain.
    """
    if fcntl is None and msvcrt is None:  # pragma: no cover - no known platform
        raise JournalError("no file locking primitive is available on this platform")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:  # Windows
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def run_lock(run_dir: Path):
    """The single transaction lock for one run.

    Every state mutation must run inside this lock.  `record_event` writes the
    journal line and updates the in-memory state anchor, but never saves the
    state itself; the caller saves after the journal write succeeded.  A crash
    therefore leaves a journal entry that the state has not acknowledged
    (`verify` reports it) instead of a state that claims an event which was
    never written.
    """
    with _locked(run_dir / "blackbox.lock"):
        yield


def read_events(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for number, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalError(f"{log_path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise JournalError(f"{log_path}:{number}: event must be a JSON object")
        events.append(event)
    return events


def _normalise_artifacts(run_dir: Path, artifacts: Iterable[str | dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in artifacts:
        if isinstance(item, dict):
            path = str(item.get("path", ""))
            if not path:
                raise JournalError("artifact object must contain path")
            if "sha256" in item and "bytes" in item:
                entry = {"path": path, "sha256": str(item["sha256"]), "bytes": int(item["bytes"])}
                if item.get("old_sha256"):
                    entry["old_sha256"] = str(item["old_sha256"])
                result.append(entry)
                continue
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = run_dir / candidate
        else:
            candidate = Path(item)
            if not candidate.is_absolute():
                candidate = run_dir / candidate
            path = str(item)
        if not candidate.exists() or not candidate.is_file():
            raise JournalError(f"artifact does not exist: {candidate}")
        try:
            ensure_within(candidate, run_dir)
        except Exception as exc:
            raise JournalError(str(exc)) from exc
        result.append({"path": path, "sha256": sha256_file(candidate), "bytes": candidate.stat().st_size})
    return result


def _check_rerecord_artifacts(event_type: str, artifacts: list[dict[str, Any]]) -> None:
    """A re-record must name both hashes for every artifact it rebinds."""
    if event_type != "ARTIFACT_RERECORD":
        return
    if not artifacts:
        raise JournalError("ARTIFACT_RERECORD requires at least one artifact")
    for item in artifacts:
        if not item.get("old_sha256"):
            raise JournalError(
                f"ARTIFACT_RERECORD requires old_sha256 for {item.get('path')!r}"
            )
        if str(item.get("old_sha256")) == str(item.get("sha256")):
            raise JournalError(
                f"ARTIFACT_RERECORD of {item.get('path')!r} has an unchanged hash"
            )


def _check_semantics(
    event_type: str,
    role: str,
    verdict: str,
    verified_by: str,
    evidence: list[str],
    reason: str = "",
) -> None:
    if event_type not in EVENT_TYPES:
        raise JournalError(f"unknown event_type: {event_type}")
    if verdict not in VERDICTS:
        raise JournalError(f"unknown verdict status: {verdict}")
    if role == "00-orchestrator" and event_type == "INIT" and verdict != "INIT_OK":
        raise JournalError("INIT must use INIT_OK")
    if event_type == "INIT" and role != "00-orchestrator":
        raise JournalError("INIT must be written by 00-orchestrator")
    if event_type == "ARTIFACT_RERECORD":
        # Re-record is an owner's explicit act, not a verification verdict: it
        # never carries PASS, and it is worthless without a stated reason.
        if verdict != "DRAFT":
            raise JournalError("ARTIFACT_RERECORD must use DRAFT, not a verdict")
        if not str(reason).strip():
            raise JournalError("ARTIFACT_RERECORD requires a non-empty reason")
        if not evidence:
            raise JournalError("ARTIFACT_RERECORD requires at least one evidence reference")
    if verdict == "PASS" and event_type not in HUMAN_EXEMPT:
        if role != "02-independent-verifier" or verified_by != role:
            raise JournalError(
                "Producer != Verifier: only 02-independent-verifier may record PASS "
                "for artifact events"
            )
        if not evidence:
            raise JournalError("PASS requires at least one evidence reference")
    if verdict in {"FAIL", "BLOCKED"} and not evidence:
        raise JournalError(f"{verdict} requires at least one evidence reference")


def _load_state_file(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JournalError(f"state.json: invalid JSON: {exc}") from exc
    return data if isinstance(data, dict) else None


def _save_state_file(run_dir: Path, state: dict[str, Any]) -> None:
    path = run_dir / "state.json"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record_event(
    run_dir: Path,
    state: dict[str, Any] | None,
    *,
    stage: str,
    task_id: str | None,
    role: str,
    event_type: str,
    why: str,
    for_what: str,
    action: str,
    verdict_status: str,
    verified_by: str,
    artifacts: Iterable[str | dict[str, Any]] = (),
    model_tier: str = "tier-2-balanced",
    model_id: str = "runtime-default",
    reasoning_effort: str = "medium",
    reason_code: str = "",
    evidence: Iterable[str] = (),
    parent_event_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Append one event while the caller holds `run_lock`.

    The log line is written first; the in-memory state anchor is updated
    afterwards.  The caller must persist the state only after this returns.
    """
    run_dir = run_dir.resolve()
    log_path = run_dir / "blackbox.jsonl"
    evidence_list = [str(x) for x in evidence]
    reason = str(reason)
    _check_semantics(event_type, role, verdict_status, verified_by, evidence_list, reason)
    prepared_artifacts = _normalise_artifacts(run_dir, artifacts)
    _check_rerecord_artifacts(event_type, prepared_artifacts)

    events = read_events(log_path)
    seq = len(events) + 1
    prev_hash = events[-1]["hash"] if events else GENESIS_PREV_HASH
    payload: dict[str, Any] = {
        "event_id": f"EVT-{seq:06d}",
        "run_id": run_dir.name,
        "seq": seq,
        "timestamp_utc": utc_now(),
        "stage": stage,
        "task_id": task_id,
        "role": role,
        "event_type": event_type,
        "model": {
            "tier": model_tier,
            "model_id": model_id,
            "effort": reasoning_effort,
        },
        "why": why,
        "for_what": for_what,
        "action": action,
        "artifacts": prepared_artifacts,
        "verdict": {
            "status": verdict_status,
            "verified_by": verified_by,
            **({"reason_code": reason_code} if reason_code else {}),
            **({"evidence": evidence_list} if evidence_list else {}),
        },
        "parent_event_id": parent_event_id,
        "prev_hash": prev_hash,
    }
    if reason:
        # Top-level `reason` is what makes a re-record auditable in isolation.
        # Other event types omit the key, so their hashes are unchanged.
        payload["reason"] = reason
    event_hash = compute_event_hash(prev_hash, payload)
    event = {**payload, "hash": event_hash}
    validate_against_schema(event, "event.schema.json", f"event {event['event_id']}")

    line = canonical_json(event) + "\n"
    fd = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    if state is not None:
        state["last_event_hash"] = event_hash
        state["event_count"] = seq
        state["updated_at"] = event["timestamp_utc"]
    write_audit_trail(run_dir)
    return event


def append_event(
    run_dir: Path,
    *,
    stage: str,
    task_id: str | None,
    role: str,
    event_type: str,
    why: str,
    for_what: str,
    action: str,
    verdict_status: str,
    verified_by: str,
    artifacts: Iterable[str | dict[str, Any]] = (),
    model_tier: str = "tier-2-balanced",
    model_id: str = "runtime-default",
    reasoning_effort: str = "medium",
    reason_code: str = "",
    evidence: Iterable[str] = (),
    parent_event_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Standalone append for callers that do not own a transaction.

    Engine mutations must use `run_lock` + `record_event`; this wrapper exists
    for one-off writers (quarantine installs) and keeps the old behaviour.
    """
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    with run_lock(run_dir):
        state = _load_state_file(run_dir)
        event = record_event(
            run_dir,
            state,
            stage=stage,
            task_id=task_id,
            role=role,
            event_type=event_type,
            why=why,
            for_what=for_what,
            action=action,
            verdict_status=verdict_status,
            verified_by=verified_by,
            artifacts=artifacts,
            model_tier=model_tier,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
            reason_code=reason_code,
            evidence=evidence,
            parent_event_id=parent_event_id,
            reason=reason,
        )
        if state is not None:
            _save_state_file(run_dir, state)
        return event


def verify_chain(log_path: Path, state_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not log_path.exists():
        return [f"journal not found: {log_path}"]
    try:
        events = read_events(log_path)
    except JournalError as exc:
        return [str(exc)]
    if not events:
        return ["journal is empty: an empty journal cannot prove anything"]
    prev_hash = GENESIS_PREV_HASH
    run_id = events[0].get("run_id")
    for index, event in enumerate(events, 1):
        label = f"line {index}"
        try:
            validate_against_schema(event, "event.schema.json", label)
        except ConfigError as exc:
            errors.append(f"{label}: schema error: {exc}")
        if event.get("seq") != index:
            errors.append(f"{label}: seq must be {index}, got {event.get('seq')}")
        if event.get("event_id") != f"EVT-{index:06d}":
            errors.append(f"{label}: event_id must be EVT-{index:06d}")
        if event.get("run_id") != run_id:
            errors.append(f"{label}: run_id changed inside one journal")
        if event.get("prev_hash") != prev_hash:
            errors.append(f"{label}: prev_hash mismatch")
        verdict = event.get("verdict") or {}
        try:
            _check_semantics(
                str(event.get("event_type", "")),
                str(event.get("role", "")),
                str(verdict.get("status", "")),
                str(verdict.get("verified_by", "")),
                [str(item) for item in verdict.get("evidence", [])],
                str(event.get("reason") or ""),
            )
            _check_rerecord_artifacts(
                str(event.get("event_type", "")),
                [item for item in event.get("artifacts") or [] if isinstance(item, dict)],
            )
        except JournalError as exc:
            errors.append(f"{label}: semantic error: {exc}")
        recorded = event.get("hash")
        payload = {k: v for k, v in event.items() if k != "hash"}
        expected = compute_event_hash(prev_hash, payload)
        if recorded != expected:
            errors.append(f"{label}: hash mismatch")
        prev_hash = str(recorded)
    if state_path is not None and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"state.json: invalid JSON: {exc}")
            state = {}
        if state:
            if state.get("last_event_hash") != prev_hash:
                errors.append("state.json: last_event_hash does not match the journal head")
            if state.get("event_count") != len(events):
                errors.append("state.json: event_count does not match the journal length")
    return errors


def render_audit_trail(events: list[dict[str, Any]]) -> str:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = [
        "# Бортовой журнал прогона (`AUDIT_TRAIL.md`)",
        "",
        "| Event ID | UTC | Stage | Task | Role | Type | Action | Verdict | Prev (8) | Hash (8) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for event in events:
        verdict = event.get("verdict", {})
        lines.append(
            "| `{id}` | `{ts}` | `{stage}` | `{task}` | `{role}` | `{type}` | {action} | `{status}` | `{prev}` | `{hash}` |".format(
                id=cell(event.get("event_id", "")),
                ts=cell(event.get("timestamp_utc", "")),
                stage=cell(event.get("stage", "")),
                task=cell(event.get("task_id") or "-"),
                role=cell(event.get("role", "")),
                type=cell(event.get("event_type", "")),
                action=cell(event.get("action", "")),
                status=cell(verdict.get("status", "")),
                prev=cell(str(event.get("prev_hash", ""))[:8]),
                hash=cell(str(event.get("hash", ""))[:8]),
            )
        )
    return "\n".join(lines) + "\n"


def write_audit_trail(run_dir: Path) -> None:
    events = read_events(run_dir / "blackbox.jsonl")
    content = render_audit_trail(events)
    path = run_dir / "AUDIT_TRAIL.md"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
