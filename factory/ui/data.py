"""Read-only payload for the dashboard, assembled from the engine's readers.

Every number on the screen comes from `status_summary`, `build_usage_report`,
`build_matrix`, `load_pipeline`, `load_state`, `read_events` or `verify_chain`.
This module only shapes their output for the browser and memoises the expensive
ones by file `(mtime, size)`: `compute_metrics` costs 184-271 ms and
`build_usage_report` re-parses `pipeline.yaml` (65-76 ms) on every call.

Nothing here writes: a dashboard that touched `state.json` or the journal would
invalidate the run it is describing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import load_pipeline
from ..engine import status_summary
from ..errors import FactoryError, RunError
from ..journal import read_events, verify_chain
from ..journal import EVENT_TYPES, VERDICTS
from ..metrics import compute_metrics
from ..paths import run_dir as safe_run_dir, runs_root as default_runs_root
from ..state import load_state
from ..trace import build_matrix
from ..usage_report import build_usage_report

TAIL_LIMIT = 50
TITLE_LIMIT = 120
LINE_RE = re.compile(r"^line (\d+):")

JOURNAL_FILTER_FIELDS = ("verdict", "stage", "event_type")
JOURNAL_LIMIT_MIN = 10
JOURNAL_LIMIT_MAX = 200
JOURNAL_LIMIT_DEFAULT = 50

_cache_lock = threading.Lock()
_cache: dict[str, tuple[Any, Any]] = {}


class RunNotFound(RunError):
    """The dashboard asks for a run id that has no directory."""


class FilterError(FactoryError):
    """A journal filter parameter failed validation."""

    def __init__(self, field: str, value: str, allowed: list[str]):
        self.field = field
        self.value = value
        self.allowed = allowed
        super().__init__(f"unknown {field}: {value!r} (allowed: {', '.join(allowed)})")


def clear_cache() -> None:
    """Drop every memoised value (tests; the payload is otherwise mtime-driven)."""
    with _cache_lock:
        _cache.clear()


def _cached(key: str, signature: Any, produce: Callable[[], Any]) -> Any:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == signature:
            return hit[1]
    value = produce()
    with _cache_lock:
        _cache[key] = (signature, value)
    return value


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _pipeline() -> dict[str, Any]:
    # load_pipeline validates the whole file against its schema on every call
    # (65 ms warm); it is a package asset that cannot change under a server.
    return _cached("pipeline", "package", load_pipeline)


def project_title(run_path: Path) -> str:
    """First line of `idea.md`, at most 120 characters; the run id without it."""
    try:
        text = (run_path / "idea.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return run_path.name
    for line in text.splitlines():
        line = line.lstrip("#").strip()
        if line:
            return line[:TITLE_LIMIT]
    return run_path.name


def _tail_lines(path: Path, limit: int) -> list[str]:
    """The last `limit` non-empty lines, read backwards from the end of the file."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            remaining = handle.tell()
            block = 64 * 1024
            data = b""
            while remaining > 0 and data.count(b"\n") <= limit:
                step = min(block, remaining)
                remaining -= step
                handle.seek(remaining)
                data = handle.read(step) + data
    except OSError:
        return []
    lines = [line for line in data.decode("utf-8", "replace").splitlines() if line.strip()]
    # A block boundary can cut the first line in half; it is not a whole event.
    if remaining > 0 and lines:
        lines = lines[1:]
    return lines[-limit:]


def _event_row(event: dict[str, Any]) -> dict[str, Any]:
    verdict = event.get("verdict") or {}
    return {
        "seq": event.get("seq"),
        "timestamp_utc": event.get("timestamp_utc"),
        "stage": event.get("stage"),
        "role": event.get("role"),
        "event_type": event.get("event_type"),
        "verdict": verdict.get("status"),
        "reason_code": verdict.get("reason_code"),
        "action": event.get("action"),
        "meaning": event.get("for_what") or event.get("why") or event.get("action") or "",
    }


def _filters_key(filters: dict[str, str]) -> str:
    return "|".join(f"{k}={v}" for k, v in sorted(filters.items()) if v)


def _filters_hash(filters: dict[str, str]) -> str:
    return hashlib.sha1(_filters_key(filters).encode()).hexdigest()[:8]


def _matches(event: dict[str, Any], filters: dict[str, str]) -> bool:
    verdict = event.get("verdict") or {}
    for field, value in filters.items():
        if not value:
            continue
        if field == "verdict" and verdict.get("status") != value:
            return False
        if field == "stage" and event.get("stage") != value:
            return False
        if field == "event_type" and event.get("event_type") != value:
            return False
    return True


def parse_journal_filters(run_id: str, root: str | Path | None, query: dict[str, list[str]]) -> tuple[dict[str, str], int]:
    """Validate and normalize journal filter query params.

    Returns ``(filters, limit)`` where ``filters`` contains only non-empty,
    validated values and ``limit`` defaults to ``JOURNAL_LIMIT_DEFAULT``.
    Raises ``FilterError`` or ``RunNotFound``.
    """
    filters = read_journal_filters(run_id, root, query)
    raw_limit = (query.get("limit") or [""])[0].strip()
    limit = JOURNAL_LIMIT_DEFAULT
    if raw_limit:
        try:
            limit = int(raw_limit)
        except ValueError:
            raise FilterError("limit", raw_limit, [f"{JOURNAL_LIMIT_MIN}..{JOURNAL_LIMIT_MAX}"])
        if not (JOURNAL_LIMIT_MIN <= limit <= JOURNAL_LIMIT_MAX):
            raise FilterError("limit", raw_limit, [f"{JOURNAL_LIMIT_MIN}..{JOURNAL_LIMIT_MAX}"])
    return filters, limit


def read_journal_filters(
    run_id: str, root: str | Path | None, query: dict[str, list[str]]
) -> dict[str, str]:
    """Validate verdict/stage/event_type only; ``limit`` is left to the caller.

    CSV export accepts any positive ``limit`` (it has always done so), while the
    payload bounds it to 10..200 for the screen.
    """
    run_path = _run_path(run_id, root)
    state = _state(run_path)
    filters: dict[str, str] = {}
    allowed = {
        "verdict": sorted(VERDICTS),
        "event_type": sorted(EVENT_TYPES),
        "stage": sorted(state["stage_status"].keys()),
    }
    for field in JOURNAL_FILTER_FIELDS:
        raw = (query.get(field) or [""])[0].strip()
        if not raw:
            continue
        if raw not in allowed[field]:
            raise FilterError(field, raw, allowed[field])
        filters[field] = raw
    return filters


def _filtered_journal(run_path: Path, filters: dict[str, str], limit: int) -> dict[str, Any]:
    """Scan the whole journal, apply filters, return the last ``limit`` matches."""
    journal = run_path / "blackbox.jsonl"
    key = f"journal:{run_path}:{_filters_key(filters)}:{limit}"
    signature = _stamp(journal)

    def produce() -> dict[str, Any]:
        events = read_events(journal)
        matched = [e for e in events if _matches(e, filters)]
        return {
            "events": [_event_row(e) for e in matched[-limit:]],
            "matched": len(matched),
        }

    return _cached(key, signature, produce)


def event_by_seq(run_id: str, seq: int, root: str | Path | None = None) -> dict[str, Any] | None:
    """Return the full event dict for ``seq`` (read-only, cached by journal stamp)."""
    run_path = _run_path(run_id, root)
    journal = run_path / "blackbox.jsonl"
    key = f"event:{run_path}:{seq}"
    signature = _stamp(journal)

    def produce() -> dict[str, Any] | None:
        for event in read_events(journal):
            if event.get("seq") == seq:
                return event
        return None

    return _cached(key, signature, produce)


def tail_events(path: Path, limit: int = TAIL_LIMIT) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in _tail_lines(path, limit):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            rows.append(_event_row(event))
    return rows


def _seconds_since(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - when).total_seconds(), 1)


def _expected_action(stage_status: str | None, gate_status: str | None) -> str:
    if gate_status == "needs_human":
        return "ждёт человека"
    if stage_status == "escalated":
        return "нужен человек"
    if stage_status == "blocked":
        return "вето"
    if stage_status == "review":
        return "ждёт независимого верификатора"
    if stage_status == "pending":
        return "ждёт исполнителя"
    if stage_status == "in_progress":
        return "исполнитель работает"
    if stage_status == "accepted":
        return "стадия закрыта"
    return "активной стадии нет"


def _usage(run_path: Path, group_by: str) -> dict[str, Any]:
    signature = _stamp(run_path / "runtime" / "usage.jsonl")

    def produce() -> dict[str, Any]:
        report, _warning = build_usage_report(
            run_path.name, group_by=group_by, root=run_path.parent
        )
        return report

    return _cached(f"usage:{group_by}:{run_path}", signature, produce)


def _state(run_path: Path) -> dict[str, Any]:
    # load_state re-validates the whole document against the JSON schema on
    # every call (22 ms); the file only changes with a new event.
    return _cached(f"state:{run_path}", _stamp(run_path / "state.json"), lambda: load_state(run_path))


def _tail(run_path: Path) -> list[dict[str, Any]]:
    journal = run_path / "blackbox.jsonl"
    return _cached(f"tail:{run_path}", _stamp(journal), lambda: tail_events(journal, TAIL_LIMIT))


def _status(run_id: str, run_path: Path) -> dict[str, Any]:
    def produce() -> dict[str, Any]:
        return status_summary(run_id, root=run_path.parent)

    return _cached(f"status:{run_path}", _stamp(run_path / "state.json"), produce)


def _tests_stamp(run_path: Path) -> tuple[int, int] | None:
    root = run_path / "product" / "tests"
    newest = 0
    size = 0
    for path in root.rglob("*") if root.is_dir() else ():
        if "__pycache__" in path.parts or not path.is_file():
            continue
        stamp = path.stat()
        newest = max(newest, stamp.st_mtime_ns)
        size += stamp.st_size
    return (newest, size)


def _trace(run_path: Path) -> dict[str, Any]:
    artifacts = run_path / "artifacts"
    signature = (
        _stamp(artifacts / "spec.md"),
        _stamp(artifacts / "acceptance.md"),
        _stamp(artifacts / "traceability.json"),
        _tests_stamp(run_path),
        _stamp(run_path / "state.json"),
    )

    def produce() -> dict[str, Any]:
        try:
            matrix = build_matrix(run_path)
        except FactoryError as exc:  # no spec.md yet: the panel says so instead of dying
            return {"available": False, "error": str(exc)}
        return {
            "available": True,
            "total": matrix["total"],
            "covered": matrix["covered"],
            "coverage": matrix["coverage"],
            "rows": matrix["requirements"],
            "uncovered": [row["req"] for row in matrix["requirements"] if not row["covered"]],
        }

    return _cached(f"trace:{run_path}", signature, produce)


def _metrics(run_path: Path) -> dict[str, Any]:
    """`compute_metrics` (184-271 ms) cached by every file it reads."""
    artifacts = run_path / "artifacts"
    signature = (
        _stamp(run_path / "state.json"),
        _stamp(run_path / "blackbox.jsonl"),
        _stamp(artifacts / "spec.md"),
        _stamp(artifacts / "acceptance.md"),
        _stamp(artifacts / "traceability.json"),
        _tests_stamp(run_path),
    )

    def produce() -> dict[str, Any]:
        try:
            return compute_metrics(run_path)
        except FactoryError as exc:  # a half-written run: say so, keep the page alive
            return {"error": str(exc)}

    return _cached(f"metrics:{run_path}", signature, produce)


def _chain_cache(run_path: Path) -> dict[str, Any] | None:
    signature = (
        _stamp(run_path / "blackbox.jsonl"),
        _stamp(run_path / "state.json"),
    )
    with _cache_lock:
        hit = _cache.get(f"chain:{run_path}")
    if hit is None or hit[0] != signature:
        return None
    return hit[1]


def chain_report(run_id: str, root: str | Path | None = None, refresh: bool = False) -> dict[str, Any]:
    """`verify_chain` on demand (1.8 ms per event), cached by journal `(mtime, size)`."""
    run_path = _run_path(run_id, root)
    journal = run_path / "blackbox.jsonl"
    signature = (_stamp(journal), _stamp(run_path / "state.json"))
    key = f"chain:{run_path}"
    if not refresh:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and hit[0] == signature:
            return hit[1]
    errors = verify_chain(journal, run_path / "state.json")
    seqs = []
    for error in errors:
        match = LINE_RE.match(error)
        if match:
            seqs.append(int(match.group(1)))
    report = {
        "run_id": run_id,
        "status": "OK" if not errors else "TAMPERED",
        "errors": errors,
        "seq": seqs[0] if seqs else None,
        "seqs": sorted(set(seqs)),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _cache_lock:
        _cache[key] = (signature, report)
    return report


def _run_path(run_id: str, root: str | Path | None) -> Path:
    try:
        path = safe_run_dir(run_id, root)
    except RunError as exc:
        raise RunNotFound(str(exc)) from exc
    if not path.is_dir():
        raise RunNotFound(f"run {run_id!r} does not exist in {default_runs_root(root)}")
    return path


def run_path(run_id: str, root: str | Path | None = None) -> Path:
    """Validated run directory for callers outside this module (the exporter)."""
    return _run_path(run_id, root)


def revision(run_id: str, root: str | Path | None = None, filters: dict[str, str] | None = None) -> str | None:
    """Cheap `revision` for `If-None-Match`: a raw `state.json` read and three stats.

    Returns `None` for an unreadable state, so the expensive path runs and turns
    it into the 500 the operator needs to see.

    When ``filters`` are present, a short hash of the normalized filters is
    appended so changing a filter invalidates the client's ETag.
    """
    run_path = _run_path(run_id, root)
    try:
        state = json.loads((run_path / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    base = _revision(run_path, state)
    if filters:
        return base + "-" + _filters_hash(filters)
    return base


def _revision(run_path: Path, state: dict[str, Any]) -> str:
    parts = [
        str(state.get("event_count")),
        str(state.get("last_event_hash")),
        str(state.get("updated_at")),
    ]
    for name in ("state.json", "blackbox.jsonl", "runtime/usage.jsonl"):
        parts.append(f"{name}={_stamp(run_path / name)}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def run_payload(run_id: str, root: str | Path | None = None,
                filters: dict[str, str] | None = None,
                limit: int = JOURNAL_LIMIT_DEFAULT) -> dict[str, Any]:
    """P1-P7 for one run plus the `revision` the client polls against.

    When ``filters`` are given the journal block is built from the whole log,
    not from the 50-event tail.  ``filters`` and ``limit`` are echoed in
    ``payload["journal"]["filters"]`` / ``payload["journal"]["limit"]``.
    """
    run_path = _run_path(run_id, root)
    state = _state(run_path)
    pipeline = _pipeline()
    summary = _status(run_id, run_path)
    mode = state["mode"]
    current = summary["current_stage"]
    current_gate = None
    for stage in pipeline["stages"]:
        if stage["id"] == current:
            current_gate = stage["gate"]["id"]

    usage_stage = _usage(run_path, "stage")
    by_stage = {group["key"]: group for group in usage_stage["groups"]}
    stages = []
    for stage in sorted(pipeline["stages"], key=lambda item: item["order"]):
        stage_id = stage["id"]
        status = state["stage_status"].get(stage_id)
        gate_id = stage["gate"]["id"]
        gate = state["gate_status"].get(gate_id, {})
        bucket = by_stage.get(stage_id, {})
        stages.append(
            {
                "id": stage_id,
                "title": stage["title"],
                "order": stage["order"],
                "status": status,
                "applicable": status is not None
                and not (stage_id == "s_in" and mode != "brownfield"),
                "gate": gate_id,
                "gate_status": gate.get("status"),
                "gate_approver": gate.get("approver"),
                "gate_decided_by": gate.get("decided_by"),
                "gate_decided_at": gate.get("decided_at"),
                "gate_evidence": gate.get("evidence", []),
                "attempts": gate.get("attempts", 0),
                "max_attempts": stage.get("max_attempts"),
                "tokens": bucket.get("tokens"),
                "seconds": bucket.get("seconds"),
            }
        )

    tail = _tail(run_path)
    tasks = list(state["tasks"].values())
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task["status"]] = counts.get(task["status"], 0) + 1

    budget = (pipeline["modes"].get(mode) or {}).get("budget") or {}
    revision = _revision(run_path, state)
    if filters:
        revision = revision + "-" + _filters_hash(filters)

    # Journal block: fast tail when no filters, full scan when any filter is set.
    if filters:
        journal_data = _filtered_journal(run_path, filters, limit)
        journal = {
            "events": journal_data["events"],
            "total": state.get("event_count"),
            "matched": journal_data["matched"],
            "shown": len(journal_data["events"]),
            "limit": limit,
            "filters": dict(filters),
            "event_types": sorted(EVENT_TYPES),
        }
    else:
        shown_tail = tail[:limit]
        journal = {
            "events": shown_tail,
            "total": state.get("event_count"),
            "matched": state.get("event_count"),
            "shown": len(shown_tail),
            "limit": TAIL_LIMIT,
            "filters": {},
            "event_types": sorted(EVENT_TYPES),
        }

    payload = {
        "run_id": run_id,
        "revision": revision,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "header": {
            "project": project_title(run_path),
            "run_id": run_id,
            "mode": mode,
            "current_stage": current,
            "stage_title": summary["stage_title"],
            "stage_status": summary["stage_status"].get(current) if current else None,
            "gate": current_gate,
            "gate_status": summary["gate_status"].get(current_gate) if current_gate else None,
            "seconds_since_event": _seconds_since(tail[-1]["timestamp_utc"]) if tail else None,
            "updated_at": state.get("updated_at"),
            "event_count": state.get("event_count"),
            "budget": budget,
            "chain": _chain_cache(run_path),
        },
        "stages": stages,
        "now": {
            "expected": _expected_action(
                summary["stage_status"].get(current) if current else None,
                summary["gate_status"].get(current_gate) if current_gate else None,
            ),
            "current_stage": current,
            "current_stage_title": summary["stage_title"],
            "last_event": tail[-1] if tail else None,
            "task_counts": counts,
            "task_total": len(tasks),
        },
        "tasks": [
            {
                "task_id": task["task_id"],
                "stage": task["stage"],
                "owner": task["owner"],
                "reqs": task["reqs"],
                "status": task["status"],
                "attempts": task["attempts"],
                "lease": task.get("lease"),
                "deps": task["deps"],
                "reject_reasons": task.get("reject_reasons", []),
                "allowed_files": task["allowed_files"],
                "artifacts": task.get("artifacts", []),
            }
            for task in sorted(tasks, key=lambda item: item["task_id"])
        ],
        "usage": {
            "totals": usage_stage["totals"],
            "by_stage": usage_stage["groups"],
            "by_role": _usage(run_path, "role")["groups"],
            "by_task": _usage(run_path, "task")["groups"],
            "budget": budget,
        },
        "metrics": _metrics(run_path),
        "trace": _trace(run_path),
        "journal": journal,
    }
    return payload


def _run_row(entry: Path) -> dict[str, Any]:
    """One row of the runs index: `state.json` and `idea.md` only, never a bundle."""
    state_file = entry / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "run_id": entry.name,
            "project": project_title(entry),
            "error": str(exc),
            "mode": None,
            "stage": None,
            "stage_status": None,
            "tokens": None,
            "updated_at": None,
            "event_count": None,
        }
    stage = state.get("current_stage")
    # `totals` does not depend on the grouping; `role` skips the pipeline load
    # that `stage` pays for on every call (76 ms vs 2 ms per run).
    totals = _cached(
        f"totals:{entry}",
        _stamp(entry / "runtime" / "usage.jsonl"),
        lambda: build_usage_report(entry.name, group_by="role", root=entry.parent)[0]["totals"],
    )
    return {
        "run_id": entry.name,
        "project": project_title(entry),
        "error": None,
        "mode": state.get("mode"),
        "stage": stage,
        "stage_status": (state.get("stage_status") or {}).get(stage) if stage else None,
        "tokens": totals.get("tokens"),
        "updated_at": state.get("updated_at"),
        "event_count": state.get("event_count"),
    }


def list_runs(root: str | Path | None = None) -> dict[str, Any]:
    base = default_runs_root(root)
    rows = []
    stamps = []
    if base.is_dir():
        for entry in base.iterdir():
            if not entry.is_dir() or not (entry / "state.json").is_file():
                continue
            stamps.append(f"{entry.name}={_stamp(entry / 'state.json')}{_stamp(entry / 'runtime' / 'usage.jsonl')}")
            signature = (_stamp(entry / "state.json"), _stamp(entry / "runtime" / "usage.jsonl"))
            rows.append(_cached(f"row:{entry}", signature, lambda e=entry: _run_row(e)))
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return {
        "runs": rows,
        "revision": hashlib.sha1("|".join(sorted(stamps)).encode("utf-8")).hexdigest()[:20] if stamps else "empty",
    }
