import json
import shutil
from pathlib import Path

import pytest

import factory.eval as eval_module
from factory.cli import build_parser
from factory.compare import compare_runs
from factory.config import ConfigError
from factory.engine import init_run
from factory.errors import FactoryError
from factory.eval import (
    compare_case_to_baseline,
    load_case,
    run_eval_set,
    validate_cases,
)
from factory.runtime.fake import FakeExecutor
from factory.verify import doctor_report

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "cases"
COMMITTED_BASELINE = ROOT / "eval" / "baseline.json"


def _copy_case(source: str, destination: Path) -> Path:
    target = destination / source
    shutil.copytree(CASES / source, target)
    return target


def _synthetic_result(case_id: str, *, steps: int = 14, ok: bool = True) -> dict:
    observed = {
        "status": "complete",
        "steps": steps,
        "loop_steps": steps * 2,
        "events": 33,
        "rework_events": 0,
        "tasks": 2,
        "gates_closed": [f"gate_s{n}" for n in range(10)],
        "pass_at_1": 1.0,
        "tokens": None,
        "tokens_billable": None,
        "usd": None,
        "seconds": 0.5,
        "prompt_chars": 1000,
    }
    return {
        "case": case_id,
        "title": case_id,
        "mode": "nano",
        "run_id": f"{case_id}-synthetic",
        "run_dir": "",
        "ok": ok,
        "status": "complete",
        "message": "",
        "errors": [] if ok else ["synthetic failure"],
        "observed": observed,
        "metrics": {"verification_coverage": 1.0, "traceability_coverage": 1.0},
    }


def test_eval_set_matches_the_committed_baseline(tmp_path, monkeypatch):
    """The committed baseline must describe the current code, not an old run."""
    monkeypatch.delenv("FACTORY_ROOT", raising=False)
    report = run_eval_set(
        CASES,
        FakeExecutor(),
        root=tmp_path / "runs",
        baseline=COMMITTED_BASELINE,
        stamp="test",
    )
    assert report["baseline"]["present"] is True
    assert report["ok"], [result["errors"] for result in report["cases"]]
    assert report["baseline"]["regressions"] == []
    nano = next(result for result in report["cases"] if result["case"] == "nano-todo")
    assert nano["status"] == "complete"
    assert nano["observed"]["product_tests"]["ok"] is True
    assert nano["observed"]["product_tests"]["tests"] == 3
    assert nano["observed"]["checks"]["ok"] is True
    assert nano["metrics"]["verification_coverage"] == 1.0
    standard = next(result for result in report["cases"] if result["case"] == "standard-mini")
    assert standard["observed"]["checks"]["ran"] is False  # checks are optional


def test_eval_writes_a_valid_baseline(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_ROOT", raising=False)
    baseline = tmp_path / "baseline.json"

    def fake_run_case(bundle, executor, runs_root, stamp, budget=None):
        return _synthetic_result(bundle["case"]["id"])

    monkeypatch.setattr(eval_module, "run_case", fake_run_case)
    report = run_eval_set(
        CASES, FakeExecutor(), root=tmp_path / "runs", update_baseline=baseline, stamp="write"
    )
    assert report["ok"] is True
    assert report["baseline_written"] == str(baseline.resolve())
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert set(data["cases"]) == {"nano-todo", "standard-mini"}
    assert data["cases"]["nano-todo"]["steps"] == 14


def test_eval_turns_a_baseline_delta_into_a_regression(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_ROOT", raising=False)
    cases = tmp_path / "cases"
    cases.mkdir()
    _copy_case("nano-todo", cases)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "generated_at": "2026-01-01T00:00:00Z",
                "cases": {
                    "nano-todo": {
                        "status": "complete",
                        "steps": 1,
                        "events": 33,
                        "rework_events": 0,
                        "tasks": 2,
                        "gates_closed": [],
                        "pass_at_1": 1.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_run_case(bundle, executor, runs_root, stamp, budget=None):
        return _synthetic_result(bundle["case"]["id"], steps=14)

    monkeypatch.setattr(eval_module, "run_case", fake_run_case)
    report = run_eval_set(
        cases, FakeExecutor(), root=tmp_path / "runs", baseline=baseline, stamp="regressed"
    )
    assert report["cases"][0]["ok"] is True
    assert report["ok"] is False
    assert any("steps grew" in message for message in report["baseline"]["regressions"])


def test_eval_refuses_to_bless_a_failing_case(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_ROOT", raising=False)
    cases = tmp_path / "cases"
    cases.mkdir()
    case_dir = _copy_case("nano-todo", cases)
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    case["max_steps"] = 1  # the run cannot leave the first stage
    (case_dir / "case.json").write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(FactoryError) as excinfo:
        run_eval_set(
            cases,
            FakeExecutor(),
            root=tmp_path / "runs",
            update_baseline=tmp_path / "baseline.json",
            stamp="bad",
        )
    assert "refusing to write a baseline" in str(excinfo.value)
    assert not (tmp_path / "baseline.json").exists()


def test_compare_case_rules_are_numeric_not_textual():
    baseline = {
        "status": "complete",
        "steps": 10,
        "events": 30,
        "tokens_billable": 1000,
        "usd": 2.0,
    }
    assert compare_case_to_baseline(dict(baseline), baseline) == []
    grown = {**baseline, "steps": 11}
    assert any("steps grew" in message for message in compare_case_to_baseline(grown, baseline))
    quieter = {**baseline, "events": 29}
    assert any("events fell" in message for message in compare_case_to_baseline(quieter, baseline))
    expensive = {**baseline, "tokens_billable": 1200, "usd": 2.5}
    messages = compare_case_to_baseline(expensive, baseline)
    assert any("tokens_billable grew" in message for message in messages)
    assert any("usd grew" in message for message in messages)
    # Unknown numbers never become zero: a fake run has no tokens at all.
    assert compare_case_to_baseline({**baseline, "tokens_billable": None}, baseline) == []
    # Ten percent is the default limit, not an exact match requirement.
    assert compare_case_to_baseline({**baseline, "tokens_billable": 1100}, baseline) == []
    changed = {**baseline, "status": "max_steps"}
    assert any("status changed" in message for message in compare_case_to_baseline(changed, baseline))


def test_compare_runs_reports_stage_and_artifact_deltas(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    init_run("left", "nano", root=runs)
    init_run("right", "nano", root=runs)
    (runs / "left" / "artifacts" / "spec.md").write_text("left spec\n", encoding="utf-8")
    (runs / "right" / "artifacts" / "spec.md").write_text("right spec\n", encoding="utf-8")
    (runs / "right" / "artifacts" / "extra.md").write_text("only right\n", encoding="utf-8")
    report = compare_runs("left", "right", root=runs)
    assert report["artifacts"]["changed"] == ["artifacts/spec.md"]
    assert report["artifacts"]["only_right"] == ["artifacts/extra.md"]
    assert report["artifacts"]["only_left"] == []
    assert report["totals"]["events"] == {"left": 1, "right": 1, "delta": 0}
    assert any(row["stage"] == "s00_intake" for row in report["stages"])


def test_validate_cases_rejects_a_broken_case(tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "case.json").write_text(json.dumps({"schema_version": "1.0.0"}), encoding="utf-8")
    errors = validate_cases(tmp_path)
    assert errors and "eval case broken" in errors[0]
    with pytest.raises(ConfigError):
        load_case(broken)


def test_expected_json_rejects_unknown_fields(tmp_path):
    case_dir = _copy_case("nano-todo", tmp_path)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["wording"] = "must not be compared"
    (case_dir / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_case(case_dir)


def test_doctor_reports_a_broken_eval_case(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    broken = tmp_path / "eval" / "cases" / "broken"
    broken.mkdir(parents=True)
    (broken / "case.json").write_text(json.dumps({"schema_version": "1.0.0"}), encoding="utf-8")
    errors = doctor_report()["errors"]
    assert any("eval case broken" in error for error in errors)


def test_cli_exposes_eval_and_compare():
    parser = build_parser()
    args = parser.parse_args(["eval", "--cases", "eval/cases", "--executor", "fake", "--json"])
    assert args.func.__name__ == "cmd_eval"
    assert args.cases == "eval/cases"
    args = parser.parse_args(["compare", "--left", "a", "--right", "b", "--json"])
    assert args.func.__name__ == "cmd_compare"
    assert (args.left, args.right) == ("a", "b")
