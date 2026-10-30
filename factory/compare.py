"""Delta between two runs: stages, steps, cost, events and artifact hashes.

`factory verify` says whether a run is intact; `factory compare` says what
actually changed between two runs.  Without it an eval can be green while
nobody can tell which stage paid for the change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_pipeline
from .errors import RunError
from .journal import read_events, sha256_file
from .paths import run_dir as safe_run_dir
from .runtime.usage import UsageLedger
from .state import load_state

CONTROL_FILES = {"state.json", "blackbox.jsonl", "blackbox.lock", "checkpoint.json", "AUDIT_TRAIL.md"}
EXCLUDED_PREFIXES = ("runtime/",)


def _billable(row: dict[str, Any]) -> int | None:
    tokens_in, tokens_out = row.get("tokens_in"), row.get("tokens_out")
    if tokens_in is None or tokens_out is None:
        return None
    return int(tokens_in) + int(tokens_out)


def _add_optional(total: float | None, value: float | None) -> float | None:
    if value is None or total is None:
        return None
    return total + value


def _totals(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    seconds = sum(float(row.get("seconds") or 0.0) for row in rows)
    tokens: float | None = 0
    usd: float | None = 0.0
    api_calls: float | None = 0
    for row in rows:
        tokens = _add_optional(tokens, row.get("tokens_total"))
        usd = _add_optional(usd, row.get("usd"))
        api_calls = _add_optional(api_calls, row.get("api_calls"))
    billable: int | None = 0
    for row in rows:
        value = _billable(row)
        if value is None:
            billable = None
            break
        billable += value
    rework = [
        event
        for event in events
        if event.get("event_type") in {"REWORK", "VETO"}
        or (event.get("verdict") or {}).get("reason_code") == "GATE_REJECTED"
    ]
    return {
        "steps": len(rows),
        "seconds": round(seconds, 3),
        "tokens": None if tokens is None else int(tokens),
        "tokens_billable": billable,
        "usd": usd,
        "api_calls": None if api_calls is None else int(api_calls),
        "events": len(events),
        "rework_events": len(rework),
    }


def _stage_usage(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stages: dict[str, dict[str, Any]] = {}
    for row in rows:
        stage = str(row.get("stage") or "?")
        bucket = stages.setdefault(
            stage, {"steps": 0, "seconds": 0.0, "tokens_billable": 0, "usd": 0.0}
        )
        bucket["steps"] += 1
        bucket["seconds"] += float(row.get("seconds") or 0.0)
        value = _billable(row)
        bucket["tokens_billable"] = (
            None
            if value is None or bucket["tokens_billable"] is None
            else bucket["tokens_billable"] + value
        )
        bucket["usd"] = _add_optional(bucket["usd"], row.get("usd"))
    for bucket in stages.values():
        bucket["seconds"] = round(bucket["seconds"], 3)
    return stages


def _stage_events(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        stage = str(event.get("stage") or "?")
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def _stage_tasks(state: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for task in state["tasks"].values():
        stage = str(task.get("stage") or "?")
        bucket = counts.setdefault(stage, {"total": 0, "accepted": 0})
        bucket["total"] += 1
        if task.get("status") == "accepted":
            bucket["accepted"] += 1
    return counts


def artifact_hashes(run_dir: Path) -> dict[str, str]:
    """Hashes of every content file in a run, without the control plane.

    The journal, state and runtime ledger change on every run by design; they
    are not artifacts.  Everything else is compared, so "rebuilt identically"
    and "substantively changed" stay distinguishable.
    """
    hashes: dict[str, str] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run_dir).as_posix()
        if relative in CONTROL_FILES or relative.startswith(EXCLUDED_PREFIXES):
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        hashes[relative] = sha256_file(path)
    return hashes


def _run_summary(run_id: str, root: Path | None) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id, root)
    if not run_dir.is_dir():
        raise RunError(f"run {run_id!r} does not exist at {run_dir}")
    state = load_state(run_dir)
    events = read_events(run_dir / "blackbox.jsonl")
    rows = UsageLedger(run_dir / "runtime" / "usage.jsonl").records()
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "mode": state["mode"],
        "stage": state["current_stage"],
        "totals": _totals(rows, events),
        "stages": _stage_usage(rows),
        "events_by_stage": _stage_events(events),
        "tasks_by_stage": _stage_tasks(state),
        "artifacts": artifact_hashes(run_dir),
    }


def _stage_row(
    stage_id: str,
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    left_usage = left["stages"].get(stage_id, {})
    right_usage = right["stages"].get(stage_id, {})
    left_events = left["events_by_stage"].get(stage_id, 0)
    right_events = right["events_by_stage"].get(stage_id, 0)
    left_tasks = left["tasks_by_stage"].get(stage_id, {})
    right_tasks = right["tasks_by_stage"].get(stage_id, {})
    left_billable = left_usage.get("tokens_billable", 0)
    right_billable = right_usage.get("tokens_billable", 0)
    return {
        "stage": stage_id,
        "left": {
            "steps": left_usage.get("steps", 0),
            "events": left_events,
            "seconds": left_usage.get("seconds", 0.0),
            "tokens_billable": left_billable,
            "tasks": left_tasks.get("total", 0),
            "tasks_accepted": left_tasks.get("accepted", 0),
        },
        "right": {
            "steps": right_usage.get("steps", 0),
            "events": right_events,
            "seconds": right_usage.get("seconds", 0.0),
            "tokens_billable": right_billable,
            "tasks": right_tasks.get("total", 0),
            "tasks_accepted": right_tasks.get("accepted", 0),
        },
        "delta": {
            "steps": right_usage.get("steps", 0) - left_usage.get("steps", 0),
            "events": right_events - left_events,
            "seconds": round(
                right_usage.get("seconds", 0.0) - left_usage.get("seconds", 0.0), 3
            ),
            "tokens_billable": (
                right_billable - left_billable
                if isinstance(left_billable, int) and isinstance(right_billable, int)
                else None
            ),
        },
    }


def compare_runs(
    left_id: str, right_id: str, *, root: str | Path | None = None
) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve() if root else None
    left = _run_summary(left_id, root_path)
    right = _run_summary(right_id, root_path)

    stage_ids = [stage["id"] for stage in load_pipeline()["stages"]]
    unknown = sorted((set(left["stages"]) | set(right["stages"])) - set(stage_ids) - {"?"})
    stages = [_stage_row(stage_id, left, right) for stage_id in stage_ids + unknown]

    left_hashes = left["artifacts"]
    right_hashes = right["artifacts"]
    changed = sorted(
        name
        for name in set(left_hashes) & set(right_hashes)
        if left_hashes[name] != right_hashes[name]
    )
    only_left = sorted(set(left_hashes) - set(right_hashes))
    only_right = sorted(set(right_hashes) - set(left_hashes))
    same = len(set(left_hashes) & set(right_hashes)) - len(changed)

    def delta_total(key: str) -> float | int | None:
        left_value = left["totals"].get(key)
        right_value = right["totals"].get(key)
        if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
            return right_value - left_value
        return None

    return {
        "schema_version": "1.0.0",
        "left": {key: value for key, value in left.items() if key != "artifacts"},
        "right": {key: value for key, value in right.items() if key != "artifacts"},
        "totals": {
            key: {
                "left": left["totals"].get(key),
                "right": right["totals"].get(key),
                "delta": delta_total(key),
            }
            for key in (
                "steps",
                "events",
                "rework_events",
                "tokens",
                "tokens_billable",
                "usd",
                "seconds",
                "api_calls",
            )
        },
        "stages": stages,
        "artifacts": {
            "same": same,
            "changed": changed,
            "only_left": only_left,
            "only_right": only_right,
        },
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _pair(left: Any, right: Any) -> str:
    return f"{_fmt(left)}/{_fmt(right)}"


def format_compare(report: dict[str, Any]) -> str:
    lines = [
        f"left:  {report['left']['run_id']}  mode={report['left']['mode']}  stage={report['left']['stage']}",
        f"right: {report['right']['run_id']}  mode={report['right']['mode']}  stage={report['right']['stage']}",
        "",
        f"{'STAGE':18} {'STEPS':>11} {'EVENTS':>11} {'TOKENS':>17} {'SECONDS':>15} {'TASKS':>9}",
    ]
    for row in report["stages"]:
        left, right = row["left"], row["right"]
        if not any(
            (left["steps"], right["steps"], left["events"], right["events"], left["tasks"], right["tasks"])
        ):
            continue
        lines.append(
            f"{row['stage']:18} "
            f"{_pair(left['steps'], right['steps']):>11} "
            f"{_pair(left['events'], right['events']):>11} "
            f"{_pair(left['tokens_billable'], right['tokens_billable']):>17} "
            f"{_pair(left['seconds'], right['seconds']):>15} "
            f"{_pair(left['tasks_accepted'], right['tasks_accepted']):>9}"
        )
    totals = report["totals"]
    lines.append(
        f"{'TOTAL':18} "
        f"{_pair(totals['steps']['left'], totals['steps']['right']):>11} "
        f"{_pair(totals['events']['left'], totals['events']['right']):>11} "
        f"{_pair(totals['tokens_billable']['left'], totals['tokens_billable']['right']):>17} "
        f"{_pair(totals['seconds']['left'], totals['seconds']['right']):>15} "
        f"{_pair('-', '-'):>9}"
    )
    lines.append("")
    lines.append(
        "artifacts: "
        f"{report['artifacts']['same']} same, {len(report['artifacts']['changed'])} changed, "
        f"{len(report['artifacts']['only_left'])} only left, "
        f"{len(report['artifacts']['only_right'])} only right"
    )
    for name in report["artifacts"]["changed"][:10]:
        lines.append(f"  changed: {name}")
    for name in report["artifacts"]["only_left"][:5]:
        lines.append(f"  only left: {name}")
    for name in report["artifacts"]["only_right"][:5]:
        lines.append(f"  only right: {name}")
    return "\n".join(lines)
