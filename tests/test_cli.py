import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cli(*args, runs_root: Path):
    env = dict(os.environ)
    env["FACTORY_RUNS_ROOT"] = str(runs_root)
    env["FACTORY_ROOT"] = str(runs_root.parent)
    return subprocess.run(
        [sys.executable, "-m", "factory.cli", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


def _write_intake(run_dir: Path):
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts" / "interview-brief.md").write_text(
        "# Brief\n## 1. Цель (JTBD)\nCLI todo\n## 2. Out-of-Scope\nweb\n"
        "## 3. Требования\n| `REQ-001` | add | Must |\n## 4. Профиль\nDEVELOPER\n",
        encoding="utf-8",
    )
    (run_dir / "artifacts" / "GLOSSARY.md").write_text(
        "# Glossary\n## Термины\n| Задача | item | Task |\n", encoding="utf-8"
    )


def test_doctor_json(tmp_path):
    result = cli("doctor", "--json", runs_root=tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["roles"] == 22
    assert data["errors"] == []


def test_init_status_next(tmp_path):
    result = cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path)
    assert result.returncode == 0, result.stderr
    run_dir = tmp_path / "demo"
    assert (run_dir / "state.json").exists()
    status = cli("status", "--run-id", "demo", "--json", runs_root=tmp_path)
    assert status.returncode == 0
    data = json.loads(status.stdout)
    assert data["current_stage"] == "s00_intake"
    order = cli("next", "--run-id", "demo", runs_root=tmp_path)
    assert order.returncode == 0
    order_data = json.loads(order.stdout)
    assert order_data["action"] == "produce"
    assert order_data["stage"] == "s00_intake"


def test_full_intake_gate(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    run_dir = tmp_path / "demo"
    _write_intake(run_dir)
    submit = cli(
        "submit", "--run-id", "demo", "--stage", "s00_intake", "--role", "01-smart-interviewer",
        "--artifact", "artifacts/interview-brief.md", "--artifact", "artifacts/GLOSSARY.md",
        runs_root=tmp_path,
    )
    assert submit.returncode == 0, submit.stderr
    verify = cli("verify", "--run-id", "demo", "--stage", "s00_intake", runs_root=tmp_path)
    assert verify.returncode == 0, verify.stdout
    bad = cli(
        "gate", "approve", "--run-id", "demo", "--gate", "gate_s00",
        "--approver", "02-independent-verifier", runs_root=tmp_path,
    )
    assert bad.returncode != 0
    gate = cli(
        "gate", "approve", "--run-id", "demo", "--gate", "gate_s00",
        "--approver", "human:test", "--evidence", "brief accepted", runs_root=tmp_path,
    )
    assert gate.returncode == 0, gate.stderr
    status = json.loads(cli("status", "--run-id", "demo", "--json", runs_root=tmp_path).stdout)
    assert status["current_stage"] == "s0_vision"


def test_verify_fails_without_artifacts(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    result = cli("verify", "--run-id", "demo", runs_root=tmp_path)
    assert result.returncode == 1
    assert "missing artifact" in result.stdout


def test_log_verify_and_show(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    assert cli("log", "verify", "--run-id", "demo", runs_root=tmp_path).returncode == 0
    show = cli("log", "show", "--run-id", "demo", runs_root=tmp_path)
    assert show.returncode == 0
    assert json.loads(show.stdout)[0]["event_type"] == "INIT"


def test_artifacts_and_models(tmp_path):
    artifacts = cli("artifacts", "--json", runs_root=tmp_path)
    assert artifacts.returncode == 0
    assert len(json.loads(artifacts.stdout)) >= 40
    models = cli("models", runs_root=tmp_path)
    assert models.returncode == 0
    assert "tier-3-frontier" in models.stdout


def test_metrics_returns_all_eight(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    result = cli("metrics", "--run-id", "demo", runs_root=tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for key in (
        "first_pass_rate", "rework_rate", "escaped_defect_rate", "cycle_time_p50_minutes",
        "cycle_time_p90_minutes", "cost_per_artifact_usd", "top_reject_reasons",
        "verification_coverage", "traceability_coverage",
    ):
        assert key in data


def test_budget_command_reports_engine_limits(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    result = cli("budget", "--run-id", "demo", runs_root=tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["default_max_attempts"] == 3
    assert data["mode"] == "nano"
    assert data["mode_budget"]["minutes"] == 120
    assert data["mode_budget"]["tokens"] == 2_500_000
    assert data["enforced_by_engine"] == ["attempts", "veto_retries"]
    assert data["runtime_responsibility"] == ["minutes", "tokens", "usd"]
    assert data["attempts_used"] == {"gates": {}, "tasks": {}}

    assert (
        cli("init", "--run-id", "demo-standard", "--mode", "standard", runs_root=tmp_path).returncode
        == 0
    )
    standard = json.loads(
        cli("budget", "--run-id", "demo-standard", runs_root=tmp_path).stdout
    )
    assert standard["mode"] == "standard"
    assert standard["mode_budget"]["minutes"] == 120
    assert standard["mode_budget"]["tokens"] == 2_900_000

    assert (
        cli("init", "--run-id", "demo-brownfield", "--mode", "brownfield", runs_root=tmp_path).returncode
        == 0
    )
    brownfield = json.loads(
        cli("budget", "--run-id", "demo-brownfield", runs_root=tmp_path).stdout
    )
    assert brownfield["mode"] == "brownfield"
    assert brownfield["mode_budget"]["minutes"] == 30
    assert brownfield["mode_budget"]["tokens"] == 4_500_000


def test_brownfield_starts_at_s_in(tmp_path):
    assert cli("init", "--run-id", "legacy", "--mode", "brownfield", runs_root=tmp_path).returncode == 0
    status = json.loads(cli("status", "--run-id", "legacy", "--json", runs_root=tmp_path).stdout)
    assert status["current_stage"] == "s_in"
    assert status["restore_drill_passed"] is False


def test_next_exits_one_when_the_stage_is_escalated(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    for attempt in range(3):
        rejected = cli(
            "gate", "reject", "--run-id", "demo", "--gate", "gate_s00",
            "--approver", "human:reviewer", "--evidence", f"reject {attempt}",
            runs_root=tmp_path,
        )
        assert rejected.returncode == 0, rejected.stderr
    result = cli("next", "--run-id", "demo", runs_root=tmp_path)
    assert result.returncode == 1
    order = json.loads(result.stdout)
    assert order["action"] == "blocked"
    assert order["stage"] == "s00_intake"


def test_run_with_fake_executor_stops_at_the_first_human_gate(tmp_path):
    runs = tmp_path / "runs"
    result = cli(
        "run", "--run-id", "live", "--executor", "fake", "--max-steps", "4", runs_root=runs
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "human_gate" in result.stdout
    assert "factory gate approve" in result.stdout
    assert (runs / "live" / "state.json").is_file()
    assert list((runs / "live" / "runtime").glob("ev-*-*.prompt.md"))


def test_run_refuses_to_create_a_missing_run_with_no_init(tmp_path):
    runs = tmp_path / "runs"
    result = cli("run", "--run-id", "ghost", "--no-init", runs_root=runs)
    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert not (runs / "ghost").exists()


def test_run_json_reports_the_stop_reason(tmp_path):
    runs = tmp_path / "runs"
    result = cli(
        "run", "--run-id", "live", "--executor", "fake", "--max-steps", "4",
        "--json", runs_root=runs,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "human_gate"
    assert payload["stage"] == "s00_intake"
    assert payload["usage"]["steps"] == 1
