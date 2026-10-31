"""Read-only usage report for one run, grouped by stage, task or role.

`runtime/usage.jsonl` is the only source.  The journal and `state.json` are
deliberately not read: a cost report must not change the run, and it must work
on a ledger whose control plane is gone.  A missing number stays `null`; `0`
means "measured zero", not "the adapter could not tell".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .config import load_pipeline
from .errors import RunError
from .paths import run_dir as safe_run_dir
from .runtime.usage import UsageLedger


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _billable(row: dict[str, Any]) -> int | float | None:
    tokens_in = _number(row.get("tokens_in"))
    tokens_out = _number(row.get("tokens_out"))
    if tokens_in is None or tokens_out is None:
        return None
    return tokens_in + tokens_out


# Field -> how it is read from one ledger record.  `steps` is the record count
# and is handled separately.
_FIELDS: dict[str, Callable[[dict[str, Any]], int | float | None]] = {
    "api_calls": lambda row: _number(row.get("api_calls")),
    "prompt_chars": lambda row: _number(row.get("prompt_chars")),
    "tokens_in": lambda row: _number(row.get("tokens_in")),
    "cache_read": lambda row: _number(row.get("cache_read")),
    "tokens_out": lambda row: _number(row.get("tokens_out")),
    "tokens": lambda row: _number(row.get("tokens_total")),
    "tokens_billable": _billable,
    "seconds": lambda row: _number(row.get("seconds")),
    "usd": lambda row: _number(row.get("usd")),
}

FIELD_ORDER = (
    "steps",
    "api_calls",
    "prompt_chars",
    "tokens_in",
    "cache_read",
    "tokens_out",
    "tokens",
    "tokens_billable",
    "seconds",
    "usd",
)


def _new_bucket() -> dict[str, Any]:
    bucket: dict[str, Any] = {"steps": 0}
    for name in FIELD_ORDER:
        if name != "steps":
            bucket[name] = 0
    return bucket


def _add_row(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["steps"] += 1
    for name, read in _FIELDS.items():
        value = read(row)
        if value is None:
            bucket[name] = None
        elif bucket[name] is not None:
            bucket[name] += value


def _finalise(bucket: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"steps": int(bucket["steps"])}
    for name in FIELD_ORDER:
        if name == "steps":
            continue
        value = bucket[name]
        if value is None:
            result[name] = None
        elif name == "seconds":
            result[name] = round(float(value), 3)
        elif name == "usd":
            result[name] = round(float(value), 6)
        else:
            result[name] = int(value)
    return result


def _group_key(row: dict[str, Any], group_by: str) -> str:
    if group_by == "task":
        task_id = row.get("task_id")
        return str(task_id) if task_id else "-"
    if group_by == "role":
        return str(row.get("role") or "?")
    return str(row.get("stage") or "?")


def _ordered_keys(
    groups: dict[str, dict[str, Any]], group_by: str, stage_ids: list[str]
) -> list[str]:
    if group_by == "stage":
        known = [stage for stage in stage_ids if stage in groups]
        return known + sorted(set(groups) - set(known))
    if group_by == "task":
        # Records without a task come first, then tasks in id order.
        return sorted(groups, key=lambda key: (key != "-", key))
    return sorted(groups)


def build_usage_report(
    run_id: str, *, group_by: str = "stage", root: str | Path | None = None
) -> tuple[dict[str, Any], str | None]:
    """Return `(report, warning)` for one run.

    A run that does not exist is an error; an existing run without a ledger is
    an honest zero report plus a warning.  The caller prints the warning to
    stderr so `--json` on stdout stays parseable.
    """
    if group_by not in {"stage", "task", "role"}:
        raise RunError(f"unknown usage grouping: {group_by!r}")

    path = safe_run_dir(run_id, root)
    if not path.is_dir():
        raise RunError(f"run {run_id!r} does not exist at {path}")

    ledger_path = path / "runtime" / "usage.jsonl"
    warning = None
    if ledger_path.is_file():
        rows = [row for row in UsageLedger(ledger_path).records() if isinstance(row, dict)]
    else:
        rows = []
        warning = f"warning: {ledger_path} is missing; reporting zero usage"

    totals_bucket = _new_bucket()
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        _add_row(totals_bucket, row)
        key = _group_key(row, group_by)
        _add_row(groups.setdefault(key, _new_bucket()), row)

    stage_ids = (
        [str(stage["id"]) for stage in load_pipeline()["stages"]]
        if group_by == "stage"
        else []
    )
    ordered = _ordered_keys(groups, group_by, stage_ids)
    report = {
        "run_id": run_id,
        "group_by": group_by,
        "totals": _finalise(totals_bucket),
        "groups": [{"key": key, **_finalise(groups[key])} for key in ordered],
    }
    return report, warning


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _usd_is_unmeasured(report: dict[str, Any]) -> bool:
    value = report["totals"].get("usd")
    return value is None or float(value) == 0.0


def format_usage(report: dict[str, Any]) -> str:
    headers = (
        ("KEY", "key", False),
        ("STEPS", "steps", True),
        ("API", "api_calls", True),
        ("PROMPT", "prompt_chars", True),
        ("IN", "tokens_in", True),
        ("CACHE", "cache_read", True),
        ("OUT", "tokens_out", True),
        ("TOKENS", "tokens", True),
        ("BILLABLE", "tokens_billable", True),
        ("SEC", "seconds", True),
        ("USD", "usd", True),
    )
    rows: list[dict[str, Any]] = list(report["groups"])
    rows.append({"key": "TOTAL", **report["totals"]})

    widths = []
    for title, field, _numeric in headers:
        width = max(len(title), *(len(_fmt(row.get(field))) for row in rows))
        widths.append(width)

    def render(row: dict[str, Any]) -> str:
        cells = []
        for (title, field, numeric), width in zip(headers, widths):
            value = _fmt(row.get(field))
            cells.append(value.rjust(width) if numeric else value.ljust(width))
        return "  ".join(cells).rstrip()

    lines = [
        f"run: {report['run_id']}  group_by: {report['group_by']}",
        render({field: title for title, field, _numeric in headers}),
    ]
    lines.extend(render(row) for row in rows)
    if not report["groups"]:
        lines.append("(no usage records)")
    if _usd_is_unmeasured(report):
        lines.append("usd не измерен: 0.0 или null — это не цена")
    return "\n".join(lines)
