"""CSV and .xlsx export of the dashboard's data.

Rows come from the same read functions the screen uses — `data.list_runs`,
`data.run_payload`, `journal.read_events` — so an export cannot disagree with
the page it was taken from, and no number is aggregated a second time.

The CSV dialect is chosen for Excel in a Russian locale: UTF-8 with a BOM, `;`
as the delimiter and CRLF.  A plain comma file without the BOM opens there as
mojibake, which is why RFC 4180 defaults are rejected on purpose.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..errors import FactoryError
from ..journal import read_events
from ..usage_report import FIELD_ORDER
from . import data

RUN_FIELDS = (
    "run_id",
    "project",
    "mode",
    "stage",
    "status",
    "tokens",
    "event_count",
    "updated_at",
)
STAGE_FIELDS = (
    "id",
    "title",
    "order",
    "status",
    "applicable",
    "gate",
    "gate_status",
    "gate_approver",
    "attempts",
    "max_attempts",
    "tokens",
    "seconds",
)
TASK_FIELDS = (
    "task_id",
    "stage",
    "owner",
    "status",
    "attempts",
    "reqs",
    "deps",
    "allowed_files",
    "reject_reasons",
)
JOURNAL_FIELDS = (
    "seq",
    "event_id",
    "timestamp_utc",
    "stage",
    "task_id",
    "role",
    "event_type",
    "verdict",
    "reason_code",
    "for_what",
    "action",
    "hash",
)
USAGE_FIELDS = ("key",) + FIELD_ORDER
TRACE_FIELDS = (
    "req",
    "spec",
    "tasks",
    "tests",
    "acceptance",
    "tasks_ok",
    "tests_ok",
    "acceptance_ok",
    "covered",
    "test_problems",
)
DATASETS = ("journal", "tasks", "usage", "trace")
GROUPINGS = ("stage", "role", "task")

Table = tuple[tuple[str, ...], list[dict[str, Any]]]


class ExportUnavailable(FactoryError):
    """The optional `.xlsx` dependency is not installed."""


def _cell(value: Any) -> Any:
    """`null` is an empty cell; a list is one cell joined with `; `."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return value


def _rows(fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> list[list[Any]]:
    return [[_cell(row.get(field)) for field in fields] for row in rows]


def csv_bytes(fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(fields)
    writer.writerows(_rows(fields, rows))
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def csv_filename(dataset: str, run_id: str | None = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{run_id}-{dataset}-{stamp}.csv" if run_id else f"{dataset}-{stamp}.csv"


def xlsx_filename(run_id: str) -> str:
    return f"{run_id}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"


def runs_table(root: str | Path | None = None) -> Table:
    """One row per run directory with a `state.json`, broken ones included."""
    rows = []
    for run in data.list_runs(root)["runs"]:
        rows.append(
            {
                "run_id": run["run_id"],
                "project": run["project"],
                "mode": run["mode"],
                "stage": run["stage"],
                "status": run["stage_status"],
                "tokens": run["tokens"],
                "event_count": run["event_count"],
                "updated_at": run["updated_at"],
            }
        )
    return RUN_FIELDS, rows


def run_row(payload: dict[str, Any]) -> dict[str, Any]:
    """The `runs.csv` row for one run, from the payload the page already has."""
    header = payload["header"]
    return {
        "run_id": payload["run_id"],
        "project": header["project"],
        "mode": header["mode"],
        "stage": header["current_stage"],
        "status": header["stage_status"],
        "tokens": (payload["usage"]["totals"] or {}).get("tokens"),
        "event_count": header["event_count"],
        "updated_at": header["updated_at"],
    }


def stages_table(payload: dict[str, Any]) -> Table:
    return STAGE_FIELDS, list(payload["stages"])


def tasks_table(payload: dict[str, Any]) -> Table:
    return TASK_FIELDS, list(payload["tasks"])


def usage_table(payload: dict[str, Any], by: str = "stage") -> Table:
    return USAGE_FIELDS, list(payload["usage"][f"by_{by}"])


def trace_table(payload: dict[str, Any]) -> Table:
    """Header only when the run has no `spec.md` yet: an empty matrix is not an error."""
    return TRACE_FIELDS, list(payload["trace"].get("rows") or [])


def journal_table(run_path: Path, limit: int | None = None) -> Table:
    """The whole journal, not the 50-event tail the screen shows; `limit` is opt-in."""
    rows = []
    for event in read_events(run_path / "blackbox.jsonl"):
        verdict = event.get("verdict") or {}
        rows.append(
            {
                "seq": event.get("seq"),
                "event_id": event.get("event_id"),
                "timestamp_utc": event.get("timestamp_utc"),
                "stage": event.get("stage"),
                "task_id": event.get("task_id"),
                "role": event.get("role"),
                "event_type": event.get("event_type"),
                "verdict": verdict.get("status"),
                "reason_code": verdict.get("reason_code"),
                "for_what": event.get("for_what"),
                "action": event.get("action"),
                "hash": event.get("hash"),
            }
        )
    return JOURNAL_FIELDS, rows[-limit:] if limit else rows


def _openpyxl() -> tuple[Any, Any]:
    # Lazy on purpose: the CI `build` job installs the sdist with bare pytest,
    # so a module-level import would break every platform there.
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError as exc:
        raise ExportUnavailable(
            "xlsx export needs the optional dependency: pip install autonomous-sw-factory[xlsx]"
        ) from exc
    return openpyxl, Font


def xlsx_bytes(sheets: dict[str, Table]) -> bytes:
    openpyxl, font = _openpyxl()
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, (fields, rows) in sheets.items():
        sheet = book.create_sheet(title)
        sheet.append(list(fields))
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = font(bold=True)
        for row in _rows(fields, rows):
            sheet.append([None if value == "" else value for value in row])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
