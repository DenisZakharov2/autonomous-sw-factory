import json
import os
import subprocess
import sys
from pathlib import Path

from factory.usage_report import FIELD_ORDER, build_usage_report, format_usage

ROOT = Path(__file__).resolve().parent.parent


def cli(*args, runs_root: Path):
    env = dict(os.environ)
    env["FACTORY_RUNS_ROOT"] = str(runs_root)
    env["FACTORY_ROOT"] = str(runs_root.parent)
    return subprocess.run(
        [sys.executable, "-m", "factory.cli", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def _write_run(root: Path, run_id: str, rows: list[dict], broken: bool = True) -> Path:
    runtime = root / run_id / "runtime"
    runtime.mkdir(parents=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if broken:
        lines.append('{"stage": "s5_acceptance", ')
    (runtime / "usage.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return runtime


def _synthetic_rows() -> list[dict]:
    return [
        {
            "stage": "s5_acceptance", "task_id": None, "role": "14-qa-engineer",
            "api_calls": 4, "prompt_chars": 3000, "tokens_in": 300,
            "cache_read": 800, "tokens_out": 70, "tokens_total": 1170,
            "seconds": 3.5, "usd": 0.0,
        },
        {
            "stage": "s5_acceptance", "task_id": None, "role": "14-qa-engineer",
            "api_calls": None, "prompt_chars": 4000, "tokens_in": None,
            "cache_read": None, "tokens_out": None, "tokens_total": None,
            "seconds": 4.5, "usd": None,
        },
        {
            "stage": "s4_build", "task_id": "TASK-002", "role": "12-fullstack-developer",
            "api_calls": 3, "prompt_chars": 2000, "tokens_in": 200,
            "cache_read": 700, "tokens_out": 60, "tokens_total": 960,
            "seconds": 2.75, "usd": 0.0,
        },
        {
            "stage": "s4_build", "task_id": "TASK-001", "role": "12-fullstack-developer",
            "api_calls": 2, "prompt_chars": 1000, "tokens_in": 100,
            "cache_read": 500, "tokens_out": 50, "tokens_total": 650,
            "seconds": 1.25, "usd": 0.0,
        },
    ]


def test_usage_json_sums_and_pipeline_order(tmp_path):
    _write_run(tmp_path, "demo", _synthetic_rows())
    result = cli("usage", "--run-id", "demo", "--json", runs_root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "NaN" not in result.stdout
    report = json.loads(result.stdout)

    assert set(report) == {"run_id", "group_by", "totals", "groups"}
    assert report["group_by"] == "stage"
    assert [group["key"] for group in report["groups"]] == ["s4_build", "s5_acceptance"]

    build = report["groups"][0]
    assert build["steps"] == 2
    assert build["api_calls"] == 5
    assert build["prompt_chars"] == 3000
    assert build["tokens_in"] == 300
    assert build["cache_read"] == 1200
    assert build["tokens_out"] == 110
    assert build["tokens"] == 1610
    assert build["tokens_billable"] == 410
    assert build["seconds"] == 4.0
    assert build["usd"] == 0.0

    accept = report["groups"][1]
    assert accept["steps"] == 2
    assert accept["api_calls"] is None
    assert accept["tokens"] is None
    assert accept["tokens_billable"] is None
    assert accept["seconds"] == 8.0
    assert accept["usd"] is None

    totals = report["totals"]
    assert set(totals) == set(FIELD_ORDER)
    assert totals["steps"] == 4
    assert totals["prompt_chars"] == 10000
    assert totals["api_calls"] is None
    assert totals["tokens_billable"] is None
    assert totals["seconds"] == 12.0
    assert totals["usd"] is None
    assert "usd не измерен" in cli("usage", "--run-id", "demo", runs_root=tmp_path).stdout


def test_usage_by_task_puts_rows_without_a_task_first(tmp_path):
    _write_run(tmp_path, "demo", _synthetic_rows())
    report = json.loads(
        cli("usage", "--run-id", "demo", "--by", "task", "--json", runs_root=tmp_path).stdout
    )
    assert report["group_by"] == "task"
    assert [group["key"] for group in report["groups"]] == ["-", "TASK-001", "TASK-002"]
    no_task, first, second = report["groups"]
    assert no_task["steps"] == 2 and no_task["tokens"] is None
    assert first["tokens_billable"] == 150
    assert second["tokens_billable"] == 260
    assert sum(group["tokens"] for group in (first, second)) == 1610


def test_usage_by_role_is_sorted_and_sums(tmp_path):
    _write_run(tmp_path, "demo", _synthetic_rows())
    report = json.loads(
        cli("usage", "--run-id", "demo", "--by", "role", "--json", runs_root=tmp_path).stdout
    )
    assert [group["key"] for group in report["groups"]] == [
        "12-fullstack-developer",
        "14-qa-engineer",
    ]
    assert report["groups"][0]["steps"] == 2
    assert report["groups"][0]["tokens"] == 1610
    assert report["groups"][1]["prompt_chars"] == 7000


def test_usage_missing_ledger_reports_zeros_and_a_warning(tmp_path):
    run_dir = tmp_path / "empty"
    run_dir.mkdir()
    result = cli("usage", "--run-id", "empty", "--json", runs_root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "is missing" in result.stderr
    report = json.loads(result.stdout)
    assert report["groups"] == []
    assert report["totals"]["steps"] == 0
    assert report["totals"]["tokens"] == 0
    assert report["totals"]["usd"] == 0.0


def test_usage_empty_ledger_reports_zeros(tmp_path):
    _write_run(tmp_path, "empty", [], broken=False)
    result = cli("usage", "--run-id", "empty", runs_root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "(no usage records)" in result.stdout
    assert "usd не измерен" in result.stdout
    assert "Traceback" not in result.stderr


def test_usage_unknown_run_is_a_cli_error(tmp_path):
    result = cli("usage", "--run-id", "ghost", runs_root=tmp_path)
    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert "Traceback" not in result.stderr


def test_usage_help_lists_the_command(tmp_path):
    result = cli("--help", runs_root=tmp_path)
    assert result.returncode == 0
    assert "usage" in result.stdout


def test_text_omits_the_usd_note_when_a_price_is_measured(tmp_path):
    rows = [dict(_synthetic_rows()[0], usd=0.25)]
    _write_run(tmp_path, "priced", rows, broken=False)
    report, warning = build_usage_report("priced", root=tmp_path)
    assert warning is None
    text = format_usage(report)
    assert "0.25" in text
    assert "usd не измерен" not in text
