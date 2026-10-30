"""Eight health metrics computed from the journal and run state."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_json, load_pipeline, validate_instance
from .errors import FactoryError
from .journal import read_events
from .state import load_state
from .trace import build_matrix

STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _p(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((percentile / 100) * (len(values) - 1))))
    return round(values[index], 2)


def _parse_stamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), STAMP)
    except ValueError:
        return None


def _stage_durations(events: list[dict[str, Any]]) -> list[float]:
    """Time spent on each stage: previous gate approval (or INIT) to this one.

    The engine records no stage-entry event, so the only reliable bracket is
    the interval between consecutive gate decisions; measuring submit→approve
    would silently drop all the actual work.
    """
    stamps: list[tuple[datetime, str]] = []
    for event in events:
        verdict = (event.get("verdict") or {}).get("status")
        if event.get("event_type") == "INIT":
            stamp = _parse_stamp(event.get("timestamp_utc"))
            if stamp:
                stamps.append((stamp, "init"))
        elif event.get("event_type") == "GATE_DECISION" and verdict in {"PASS", "ACCEPTED_RISK"}:
            stamp = _parse_stamp(event.get("timestamp_utc"))
            if stamp:
                stamps.append((stamp, str(event.get("stage"))))
    stamps.sort()
    durations: list[float] = []
    for (previous, _), (current, stage) in zip(stamps, stamps[1:]):
        if stage == "init":
            continue
        durations.append((current - previous).total_seconds() / 60.0)
    return durations


def compute_metrics(run_dir: Path) -> dict[str, Any]:
    state = load_state(run_dir)
    pipeline = load_pipeline()
    events = read_events(run_dir / "blackbox.jsonl")
    tasks = list(state["tasks"].values())
    accepted = [task for task in tasks if task["status"] == "accepted"]
    first_pass = [task for task in accepted if task.get("attempts", 0) == 0]
    first_pass_rate = (len(first_pass) / len(accepted)) if accepted else None

    rework_events = [
        event
        for event in events
        if event.get("event_type") in {"REWORK", "VETO"}
        or (event.get("verdict") or {}).get("reason_code") == "GATE_REJECTED"
    ]
    denial_units = len(tasks) + len(pipeline["stages"])
    rework_rate = (len(rework_events) / denial_units) if denial_units else None

    reasons = Counter(
        (event.get("verdict") or {}).get("reason_code", "unknown")
        for event in events
        if (event.get("verdict") or {}).get("status") == "FAIL"
    )
    top_reasons = [{"reason_code": code, "count": count} for code, count in reasons.most_common(5)]

    durations = _stage_durations(events)
    cycle_p50 = _p(durations, 50)
    cycle_p90 = _p(durations, 90)
    try:
        matrix = build_matrix(run_dir)
        traceability_coverage = matrix["coverage"]
    except FactoryError:
        traceability_coverage = None

    applicable = [
        stage
        for stage in pipeline["stages"]
        if not (stage["id"] == "s_in" and state["mode"] != "brownfield")
    ]
    approved = [
        stage
        for stage in applicable
        if state["gate_status"].get(stage["gate"]["id"], {}).get("status") == "approved"
    ]
    verification_coverage = (len(approved) / len(applicable)) if applicable else None

    metrics = {
        "schema_version": "1.0.0",
        "run_id": run_dir.name,
        "first_pass_rate": first_pass_rate,
        "rework_rate": rework_rate,
        "escaped_defect_rate": None,
        "cycle_time_p50_minutes": cycle_p50,
        "cycle_time_p90_minutes": cycle_p90,
        "cost_per_artifact_usd": None,
        "top_reject_reasons": top_reasons,
        "verification_coverage": verification_coverage,
        "traceability_coverage": traceability_coverage,
    }
    schema = load_json(Path(__file__).resolve().parent / "schemas" / "metrics.schema.json")
    validate_instance(metrics, schema, "metrics.json")
    return metrics


def write_metrics(run_dir: Path) -> dict[str, Any]:
    metrics = compute_metrics(run_dir)
    path = run_dir / "artifacts" / "metrics.json"
    path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return metrics
