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


def _number(value: Any) -> int | float | None:
    """Extract a numeric value, returning None for non-numeric or boolean."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


TAIL_LIMIT = 50
TITLE_LIMIT = 120
LINE_RE = re.compile(r"^line (\d+):")

JOURNAL_FILTER_FIELDS = ("verdict", "stage", "event_type")
JOURNAL_LIMIT_MIN = 10
JOURNAL_LIMIT_MAX = 200
JOURNAL_LIMIT_DEFAULT = 50

TIMESERIES_METRICS = ("tokens", "seconds", "usd", "api_calls")
TIMESERIES_BUCKETS = ("stage", "event", "minute")

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


def _timeseries(run_path: Path, metrics: list[str], bucket: str) -> dict[str, Any]:
    """Build time-series points from journal and usage ledger.

    Returns ``{"points": [...], "metrics": metrics, "bucket": bucket}`` where each
    point is ``{"t": timestamp_utc, "stage": stage, "metric": metric, "value": value}``.
    """
    journal = run_path / "blackbox.jsonl"
    usage_path = run_path / "runtime" / "usage.jsonl"
    key = f"timeseries:{run_path}:{bucket}:{','.join(metrics)}"
    signature = (_stamp(journal), _stamp(usage_path))

    def produce() -> dict[str, Any]:
        events = read_events(journal)
        if not events:
            return {"points": [], "metrics": metrics, "bucket": bucket}

        # Load usage records
        usage_records = []
        if usage_path.exists():
            for line in usage_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    usage_records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        points: list[dict[str, Any]] = []

        if bucket == "event":
            # One point per event, using event timestamp
            for event in events:
                ts = event.get("timestamp_utc")
                stage = event.get("stage")
                for metric in metrics:
                    value = None
                    if metric == "tokens":
                        # Tokens from usage at this event's stage (approximate)
                        # We'll sum tokens for this stage up to this event
                        value = _usage_up_to_stage(usage_records, stage, "tokens_total") or 0
                    elif metric == "seconds":
                        value = _usage_up_to_stage(usage_records, stage, "seconds") or 0.0
                    elif metric == "usd":
                        value = _usage_up_to_stage(usage_records, stage, "usd") or 0.0
                    elif metric == "api_calls":
                        value = _usage_up_to_stage(usage_records, stage, "api_calls") or 0
                    if value is not None:
                        points.append({"t": ts, "stage": stage, "metric": metric, "value": value})

        elif bucket == "stage":
            # One point per stage transition, using first event timestamp for stage
            stage_first_ts: dict[str, str] = {}
            stage_order: list[str] = []
            for event in events:
                stage = event.get("stage")
                if stage and stage not in stage_first_ts:
                    stage_first_ts[stage] = event.get("timestamp_utc")
                    stage_order.append(stage)

            for stage in stage_order:
                ts = stage_first_ts[stage]
                for metric in metrics:
                    value = None
                    if metric == "tokens":
                        value = _usage_for_stage(usage_records, stage, "tokens_total") or 0
                    elif metric == "seconds":
                        value = _usage_for_stage(usage_records, stage, "seconds") or 0.0
                    elif metric == "usd":
                        value = _usage_for_stage(usage_records, stage, "usd") or 0.0
                    elif metric == "api_calls":
                        value = _usage_for_stage(usage_records, stage, "api_calls") or 0
                    if value is not None:
                        points.append({"t": ts, "stage": stage, "metric": metric, "value": value})

        elif bucket == "minute":
            # Bucket events by minute, using usage timestamps
            minute_buckets: dict[str, dict[str, float]] = {}
            for record in usage_records:
                ts = record.get("timestamp_utc")
                if not ts:
                    continue
                # Round to minute
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    minute_key = dt.strftime("%Y-%m-%dT%H:%MZ")
                except ValueError:
                    continue
                bucket_data = minute_buckets.setdefault(minute_key, {})
                for metric in metrics:
                    if metric == "tokens":
                        val = _number(record.get("tokens_total"))
                        if val is not None:
                            bucket_data[metric] = float(bucket_data.get(metric, 0)) + float(val)
                    elif metric == "seconds":
                        val = _number(record.get("seconds"))
                        if val is not None:
                            bucket_data[metric] = float(bucket_data.get(metric, 0)) + float(val)
                    elif metric == "usd":
                        val = _number(record.get("usd"))
                        if val is not None:
                            bucket_data[metric] = float(bucket_data.get(metric, 0)) + float(val)
                    elif metric == "api_calls":
                        val = _number(record.get("api_calls"))
                        if val is not None:
                            bucket_data[metric] = float(bucket_data.get(metric, 0)) + float(val)

            for minute_key in sorted(minute_buckets.keys()):
                bucket_data = minute_buckets[minute_key]
                for metric in metrics:
                    value = bucket_data.get(metric)
                    if value is not None:
                        points.append({"t": minute_key, "stage": None, "metric": metric, "value": value})

        return {"points": points, "metrics": metrics, "bucket": bucket}

    return _cached(key, signature, produce)


def _usage_up_to_stage(usage_records: list[dict[str, Any]], stage: str | None, field: str) -> int | float | None:
    """Sum usage field for all records up to and including the given stage (cumulative)."""
    if not stage:
        return None
    pipeline = load_pipeline()
    stage_order = {s["id"]: s["order"] for s in pipeline["stages"]}
    target_order = stage_order.get(stage, 0)
    total = 0
    has_value = False
    for record in usage_records:
        rec_stage = record.get("stage")
        if rec_stage and stage_order.get(rec_stage, 0) <= target_order:
            val = _number(record.get(field))
            if val is not None:
                total += val
                has_value = True
    return total if has_value else None


def _usage_for_stage(usage_records: list[dict[str, Any]], stage: str | None, field: str) -> int | float | None:
    """Sum usage field for records exactly at the given stage."""
    if not stage:
        return None
    total = 0
    has_value = False
    for record in usage_records:
        if record.get("stage") == stage:
            val = _number(record.get(field))
            if val is not None:
                total += val
                has_value = True
    return total if has_value else None


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


def read_events_from(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Incrementally read journal events from byte ``offset``.

    Returns ``(events, new_offset)``.  A trailing line without ``\\n`` is the
    writer mid-append: it is left unconsumed and picked up on the next call.
    A file shorter than ``offset`` was truncated or rotated, so the read
    restarts at 0.  A complete line that does not parse raises
    ``FactoryError`` -- the journal is corrupt, not unfinished.
    """
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events, 0
    size = path.stat().st_size
    if offset > size:
        offset = 0
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        while True:
            position = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith("\n"):
                handle.seek(position)
                break
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FactoryError(f"{path}: invalid JSON at byte {position}: {exc}") from exc
            if isinstance(event, dict):
                events.append(event)
        return events, handle.tell()


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


def compare_runs(run_id_a: str, run_id_b: str, root: str | Path | None = None) -> dict[str, Any]:
    """Compare two runs and return deltas for metrics, stages, and traceability.

    Raises ``RunNotFound`` if either run does not exist, or ``ValueError`` if
    ``run_id_a == run_id_b``.
    """
    path_a = _run_path(run_id_a, root)
    path_b = _run_path(run_id_b, root)
    if run_id_a == run_id_b:
        raise ValueError("run ids must differ")

    payload_a = run_payload(run_id_a, root)
    payload_b = run_payload(run_id_b, root)

    def _delta(a_val: Any, b_val: Any) -> dict[str, Any] | None:
        if a_val is None and b_val is None:
            return None
        if a_val is None:
            return {"a": None, "b": b_val, "delta": None, "delta_pct": None}
        if b_val is None:
            return {"a": a_val, "b": None, "delta": None, "delta_pct": None}
        if isinstance(a_val, (int, float)) and isinstance(b_val, (int, float)):
            delta = b_val - a_val
            delta_pct = None
            if a_val != 0:
                delta_pct = round(delta / abs(a_val) * 100, 1)
            return {"a": a_val, "b": b_val, "delta": delta, "delta_pct": delta_pct}
        return {"a": a_val, "b": b_val, "delta": None, "delta_pct": None}

    # Metrics delta
    metrics_a = payload_a.get("metrics", {})
    metrics_b = payload_b.get("metrics", {})
    metric_keys = set(metrics_a.keys()) | set(metrics_b.keys())
    metrics_delta = {}
    for key in sorted(metric_keys):
        delta = _delta(metrics_a.get(key), metrics_b.get(key))
        if delta is not None:
            metrics_delta[key] = delta

    # Stages delta
    stages_a = {s["id"]: s for s in payload_a.get("stages", [])}
    stages_b = {s["id"]: s for s in payload_b.get("stages", [])}
    stage_ids = sorted(set(stages_a.keys()) | set(stages_b.keys()))
    stages_delta = []
    for stage_id in stage_ids:
        sa = stages_a.get(stage_id)
        sb = stages_b.get(stage_id)
        stage_delta = {"stage_id": stage_id}
        if sa and sb:
            for field in ("status", "gate_status", "tokens", "seconds", "attempts"):
                a_val = sa.get(field)
                b_val = sb.get(field)
                d = _delta(a_val, b_val)
                if d is not None:
                    stage_delta[f"a_{field}"] = a_val
                    stage_delta[f"b_{field}"] = b_val
                    stage_delta[f"delta_{field}"] = d.get("delta")
                    stage_delta[f"delta_{field}_pct"] = d.get("delta_pct")
        elif sa:
            stage_delta["a_status"] = sa.get("status")
            stage_delta["a_gate_status"] = sa.get("gate_status")
            stage_delta["a_tokens"] = sa.get("tokens")
            stage_delta["a_seconds"] = sa.get("seconds")
            stage_delta["a_attempts"] = sa.get("attempts")
        else:
            # sb is guaranteed to exist here (not in sa and not in both)
            stage_delta["b_status"] = sb.get("status") if sb else None
            stage_delta["b_gate_status"] = sb.get("gate_status") if sb else None
            stage_delta["b_tokens"] = sb.get("tokens") if sb else None
            stage_delta["b_seconds"] = sb.get("seconds") if sb else None
            stage_delta["b_attempts"] = sb.get("attempts") if sb else None
        stages_delta.append(stage_delta)

    # Traceability delta
    trace_a = payload_a.get("trace", {})
    trace_b = payload_b.get("trace", {})
    trace_delta = {
        "a": {
            "coverage": trace_a.get("coverage"),
            "covered": trace_a.get("covered"),
            "total": trace_a.get("total"),
            "uncovered": trace_a.get("uncovered", []),
        },
        "b": {
            "coverage": trace_b.get("coverage"),
            "covered": trace_b.get("covered"),
            "total": trace_b.get("total"),
            "uncovered": trace_b.get("uncovered", []),
        },
    }
    # Delta for numeric trace fields
    for field in ("coverage", "covered", "total"):
        av = trace_a.get(field)
        bv = trace_b.get(field)
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            delta = bv - av
            delta_pct = None
            if av != 0:
                delta_pct = round(delta / abs(av) * 100, 1)
            trace_delta[f"delta_{field}"] = delta
            trace_delta[f"delta_{field}_pct"] = delta_pct

    # Totals delta (usage)
    usage_a = payload_a.get("usage", {}).get("totals", {})
    usage_b = payload_b.get("usage", {}).get("totals", {})
    totals_delta = {}
    for field in ("tokens", "seconds", "usd", "api_calls", "steps"):
        av = usage_a.get(field)
        bv = usage_b.get(field)
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            delta = bv - av
            delta_pct = None
            if av != 0:
                delta_pct = round(delta / abs(av) * 100, 1)
            totals_delta[field] = {"a": av, "b": bv, "delta": delta, "delta_pct": delta_pct}

    return {
        "a": {"run_id": run_id_a, "project": payload_a["header"]["project"]},
        "b": {"run_id": run_id_b, "project": payload_b["header"]["project"]},
        "metrics": metrics_delta,
        "stages": stages_delta,
        "trace": trace_delta,
        "totals": totals_delta,
    }


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


def compare_revision(run_id_a: str, run_id_b: str, root: str | Path | None = None) -> str | None:
    """ETag for /api/compare: hash of the two run revisions."""
    rev_a = revision(run_id_a, root)
    rev_b = revision(run_id_b, root)
    if not rev_a or not rev_b:
        return None
    return hashlib.sha1(f"{rev_a}|{rev_b}".encode("utf-8")).hexdigest()[:20]


def timeseries_revision(run_id: str, root: str | Path | None = None, bucket: str = "stage", metrics: list[str] | None = None) -> str | None:
    """ETag for /api/runs/<id>/timeseries: hash of journal stamp + bucket + metrics."""
    try:
        run_path = _run_path(run_id, root)
    except RunNotFound:
        return None
    journal = run_path / "blackbox.jsonl"
    usage = run_path / "runtime" / "usage.jsonl"
    journal_stamp = _stamp(journal)
    usage_stamp = _stamp(usage)
    if not journal_stamp:
        return None
    metrics_key = ",".join(metrics or TIMESERIES_METRICS)
    parts = [
        f"journal={journal_stamp}",
        f"usage={usage_stamp}",
        f"bucket={bucket}",
        f"metrics={metrics_key}",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def timeseries_data(run_id: str, root: str | Path | None = None,
                    metrics: list[str] | None = None,
                    bucket: str = "stage") -> dict[str, Any]:
    """Return time-series points for the given run, metrics, and bucket."""
    if metrics is None:
        metrics = list(TIMESERIES_METRICS)
    run_path = _run_path(run_id, root)
    return _timeseries(run_path, metrics, bucket)


def compare_timeseries_revision(run_id_a: str, run_id_b: str, root: str | Path | None = None,
                                metrics: list[str] | None = None, bucket: str = "stage") -> str | None:
    """ETag for /api/compare/timeseries: two timeseries revisions + metrics."""
    rev_a = timeseries_revision(run_id_a, root, bucket)
    rev_b = timeseries_revision(run_id_b, root, bucket)
    if not rev_a or not rev_b:
        return None
    metrics_key = ",".join(metrics or TIMESERIES_METRICS)
    return hashlib.sha1(f"{rev_a}|{rev_b}|{metrics_key}".encode("utf-8")).hexdigest()[:20]


def compare_timeseries_data(run_id_a: str, run_id_b: str, root: str | Path | None = None,
                            metrics: list[str] | None = None,
                            bucket: str = "stage") -> dict[str, Any]:
    """Time-series points of two runs for the compare overlay.

    Raises ``RunNotFound`` if either run does not exist, or ``ValueError`` if
    ``run_id_a == run_id_b``.
    """
    if run_id_a == run_id_b:
        raise ValueError("run ids must differ")
    series_a = timeseries_data(run_id_a, root, metrics, bucket)
    series_b = timeseries_data(run_id_b, root, metrics, bucket)
    return {
        "a": {"run_id": run_id_a, "points": series_a["points"]},
        "b": {"run_id": run_id_b, "points": series_b["points"]},
        "metrics": series_a["metrics"],
        "bucket": series_a["bucket"],
    }


# -- artifact comparison ----------------------------------------------------

ARTIFACT_NAMES = ("spec", "acceptance", "plan")
ARTIFACT_FILES = {name: f"artifacts/{name}.md" for name in ARTIFACT_NAMES}


def _artifact_path(run_path: Path, name: str) -> Path:
    """Return the artifact file path for a run. Name must be in ARTIFACT_NAMES."""
    return run_path / ARTIFACT_FILES[name]


def _read_artifact(run_path: Path, name: str) -> list[str] | None:
    """Read artifact lines, or None if file does not exist."""
    path = _artifact_path(run_path, name)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text.splitlines()


def _artifact_stamp(run_path: Path, name: str) -> tuple[int, int] | None:
    """(mtime_ns, size) of an artifact file, or None if missing."""
    return _stamp(_artifact_path(run_path, name))


def artifact_revision(run_id: str, root: str | Path | None = None, name: str = "spec") -> str | None:
    """ETag for a single run's artifact: hash of the file stamp."""
    if name not in ARTIFACT_NAMES:
        return None
    try:
        run_path = _run_path(run_id, root)
    except RunNotFound:
        return None
    stamp = _artifact_stamp(run_path, name)
    if not stamp:
        return None
    return hashlib.sha1(f"{name}={stamp}".encode("utf-8")).hexdigest()[:20]


def compare_artifact_revision(run_id_a: str, run_id_b: str, root: str | Path | None = None, name: str = "spec") -> str | None:
    """ETag for /api/compare/artifact: hash of both artifact stamps + name."""
    if name not in ARTIFACT_NAMES:
        return None
    rev_a = artifact_revision(run_id_a, root, name)
    rev_b = artifact_revision(run_id_b, root, name)
    if not rev_a or not rev_b:
        return None
    return hashlib.sha1(f"{rev_a}|{rev_b}|{name}".encode("utf-8")).hexdigest()[:20]


def compare_artifact_data(run_id_a: str, run_id_b: str, root: str | Path | None = None, name: str = "spec") -> dict[str, Any]:
    """Line-by-line diff of an artifact between two runs using stdlib difflib.

    Returns ``{"name", "a": {"run_id", "present", "lines"}, "b": {...}, "rows": [{"tag", "left", "right"}]}
    where tag ∈ equal|delete|insert|replace. Missing artifact -> present: false, lines: null.
    Raises ``RunNotFound`` if either run does not exist, or ``ValueError`` if
    ``run_id_a == run_id_b`` or unknown artifact name.
    """
    import difflib
    if run_id_a == run_id_b:
        raise ValueError("run ids must differ")
    if name not in ARTIFACT_NAMES:
        raise ValueError(f"unknown artifact name: {name!r} (allowed: {', '.join(ARTIFACT_NAMES)})")
    path_a = _run_path(run_id_a, root)
    path_b = _run_path(run_id_b, root)
    lines_a = _read_artifact(path_a, name)
    lines_b = _read_artifact(path_b, name)
    present_a = lines_a is not None
    present_b = lines_b is not None
    a_lines = lines_a if lines_a is not None else []
    b_lines = lines_b if lines_b is not None else []
    opcodes = difflib.SequenceMatcher(None, a_lines, b_lines).get_opcodes()
    rows: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"tag": "equal", "left": a_lines[i1 + k], "right": b_lines[j1 + k]})
        elif tag == "delete":
            for k in range(i2 - i1):
                rows.append({"tag": "delete", "left": a_lines[i1 + k], "right": None})
        elif tag == "insert":
            for k in range(j2 - j1):
                rows.append({"tag": "insert", "left": None, "right": b_lines[j1 + k]})
        elif tag == "replace":
            # render as paired lines: old on left, new on right
            max_len = max(i2 - i1, j2 - j1)
            for k in range(max_len):
                left = a_lines[i1 + k] if k < (i2 - i1) else None
                right = b_lines[j1 + k] if k < (j2 - j1) else None
                rows.append({"tag": "replace", "left": left, "right": right})
    return {
        "name": name,
        "a": {"run_id": run_id_a, "present": present_a, "lines": lines_a},
        "b": {"run_id": run_id_b, "present": present_b, "lines": lines_b},
        "rows": rows,
    }


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
        shown_tail = tail[-limit:] if limit else tail[:TAIL_LIMIT]
        journal = {
            "events": shown_tail,
            "total": state.get("event_count"),
            "matched": state.get("event_count"),
            "shown": len(shown_tail),
            "limit": limit or TAIL_LIMIT,
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
