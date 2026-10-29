"""Eight health metrics computed from the journal and run state."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_json, validate_instance
from .errors import FactoryError
from .journal import read_events
from .state import load_state
from .trace import build_matrix


def _p(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((percentile / 100) * (len(values) - 1))))
    return round(values[index], 2)


def compute_metrics(run_dir: Path) -> dict[str, Any]:
    state = load_state(run_dir)
    events = read_events(run_dir / "blackbox.jsonl")
    tasks = list(state["tasks"].values())
    accepted = [task for task in tasks if task["status"] == "accepted"]
    first_pass = [task for task in accepted if task.get("attempts", 0) == 0]
    first_pass_rate = (len(first_pass) / len(accepted)) if accepted else None
    rework_events = [event for event in events if event.get("event_type") == "REWORK"]
    rework_rate = (len(rework_events) / len(tasks)) if tasks else None
    reasons = Counter(
        (event.get("verdict") or {}).get("reason_code", "unknown")
        for event in events
        if (event.get("verdict") or {}).get("status") == "FAIL"
    )
    top_reasons = [{"reason_code": code, "count": count} for code, count in reasons.most_common(5)]
    stage_times: dict[str, list[datetime]] = {}
    for event in events:
        raw = event.get("timestamp_utc")
        if not raw:
            continue
        try:
            stamp = datetime.strptime(str(raw), "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        stage_times.setdefault(str(event.get("stage")), []).append(stamp)
    durations = [
        (max(values) - min(values)).total_seconds() / 60.0
        for values in stage_times.values()
        if len(values) > 1
    ]
    cycle_p50 = _p(durations, 50)
    cycle_p90 = _p(durations, 90)
    try:
        matrix = build_matrix(run_dir)
        traceability_coverage = matrix["coverage"]
    except FactoryError:
        traceability_coverage = None
    total_stages = len(state["stage_status"])
    approved_gates = sum(1 for gate in state["gate_status"].values() if gate.get("status") == "approved")
    verification_coverage = (approved_gates / total_stages) if total_stages else None
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
