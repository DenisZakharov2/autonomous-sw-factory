import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _make_token(runs_root: Path, run_id: str, gate: str, approver: str, action: str) -> tuple[str, dict[str, str]]:
    """Generate an approval token via CLI."""
    env: dict[str, str] = {"FACTORY_APPROVAL_KEY": "a" * 32}
    result = cli(
        "approval-token",
        "--run-id", run_id,
        "--gate", gate,
        "--approver", approver,
        "--action", action,
        "--ttl", "600",
        runs_root=runs_root,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip(), env


def cli(*args, runs_root: Path, env: dict | None = None):
    process_env = dict(os.environ)
    process_env["FACTORY_RUNS_ROOT"] = str(runs_root)
    process_env["FACTORY_ROOT"] = str(runs_root.parent)
    process_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "factory.cli", *args],
        cwd=ROOT, env=process_env, capture_output=True, text=True, encoding="utf-8", check=False,
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
    # Doctor may fail if root-helper is not installed; accept that
    data = json.loads(result.stdout)
    assert data["roles"] == 22
    masks = data["executor_masks"]
    assert ".hermes" in masks["secret_home_paths"]
    assert ".netrc" in masks["secret_home_paths"]
    assert "_API_KEY" in masks["env_denied_suffixes"]
    assert masks["hermes_passthrough"] == ["cache", "logs", "installs"]


def test_doctor_prints_the_executor_masks(tmp_path):
    result = cli("doctor", runs_root=tmp_path)
    # Doctor may fail if root-helper is not installed; accept that
    assert "secret home paths:" in result.stdout
    assert ".hermes" in result.stdout
    assert "*_API_KEY" in result.stdout
    assert "~/.hermes pass-through: cache, logs, installs" in result.stdout


def _doctor_report(errors=None):
    return {
        "errors": list(errors or []),
        "warnings": [],
        "roles": 22,
        "templates": 39,
        "schemas": 13,
        "stages": 13,
        "artifacts": 44,
        "modes": ["nano"],
    }


def test_doctor_does_not_compare_a_foreign_project_with_the_package(tmp_path, monkeypatch, capsys):
    """`FACTORY_ROOT` may point at another project; its VERSION is not our contract.

    The check used to read `PACKAGE_ROOT.parent` unconditionally, so an
    installed package asked about a foreign project compared the wrong files
    and could report a mismatch that says nothing about either of them.
    """
    from factory import cli as cli_module

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "VERSION").write_text("9.9.9", encoding="utf-8")
    (foreign / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    (foreign / "LICENSE").write_text("All rights reserved.\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "project_root", lambda: foreign)
    monkeypatch.setattr(cli_module, "doctor_report", lambda: _doctor_report())

    assert cli_module.cmd_doctor(argparse.Namespace(json=True)) == 0
    assert json.loads(capsys.readouterr().out)["errors"] == []


def test_doctor_checks_the_package_repository_against_its_version(tmp_path, monkeypatch, capsys):
    from factory import cli as cli_module

    package = tmp_path / "repo" / "factory"
    package.mkdir(parents=True)
    repo = package.parent
    (repo / "VERSION").write_text("0.0.1", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (repo / "LICENSE").write_text("All rights reserved.\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "PACKAGE_ROOT", package)
    monkeypatch.setattr(cli_module, "project_root", lambda: repo)
    monkeypatch.setattr(cli_module, "doctor_report", lambda: _doctor_report())

    assert cli_module.cmd_doctor(argparse.Namespace(json=True)) == 1
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert "VERSION does not match factory.__version__" in errors
    assert any("LICENSE" in error for error in errors)


def test_approval_token_mints_a_bound_token(tmp_path):
    env_key = "a" * 32
    result = cli(
        "approval-token",
        "--run-id", "demo-01",
        "--gate", "gate_s00",
        "--approver", "human:denis",
        "--action", "approve",
        "--ttl", "600",
        runs_root=tmp_path,
        env={"FACTORY_APPROVAL_KEY": env_key},
    )
    assert result.returncode == 0, result.stderr
    # New format: exp.jti.digest (3 parts)
    parts = result.stdout.strip().split(".")
    assert len(parts) == 3
    expiry, jti, digest = parts
    assert int(expiry) > 0
    assert len(jti) > 0
    assert len(digest) == 64


def test_approval_token_checks_action_against_subject(tmp_path):
    result = cli(
        "approval-token",
        "--run-id", "demo-01",
        "--stage", "s00_intake",
        "--approver", "human:denis",
        "--action", "approve",
        runs_root=tmp_path,
        env={"FACTORY_APPROVAL_KEY": "a" * 32},
    )
    assert result.returncode != 0
    assert "--gate is required" in result.stderr


def test_approval_token_refuses_a_short_key(tmp_path):
    result = cli(
        "approval-token",
        "--run-id", "demo-01",
        "--gate", "gate_s00",
        "--approver", "human:denis",
        "--action", "approve",
        runs_root=tmp_path,
        env={"FACTORY_APPROVAL_KEY": "secret"},
    )
    assert result.returncode != 0
    assert "at least 32" in result.stderr


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
    token, env = _make_token(tmp_path, "demo", "gate_s00", "human:test", "approve")
    gate = cli(
        "gate", "approve", "--run-id", "demo", "--gate", "gate_s00",
        "--approver", "human:test", "--evidence", "brief accepted",
        "--approval-token", token,
        runs_root=tmp_path,
        env=env,
    )
    assert gate.returncode == 0, gate.stderr
    status = json.loads(cli("status", "--run-id", "demo", "--json", runs_root=tmp_path).stdout)
    assert status["current_stage"] == "s0_vision"


def test_artifact_re_record_command(tmp_path):
    """The CLI path an auditor uses: change, re-record, verify shows the count."""
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    run_dir = tmp_path / "demo"
    _write_intake(run_dir)
    assert cli(
        "submit", "--run-id", "demo", "--stage", "s00_intake", "--role", "01-smart-interviewer",
        "--artifact", "artifacts/interview-brief.md", "--artifact", "artifacts/GLOSSARY.md",
        runs_root=tmp_path,
    ).returncode == 0
    token, env = _make_token(tmp_path, "demo", "gate_s00", "human:test", "approve")
    assert cli(
        "gate", "approve", "--run-id", "demo", "--gate", "gate_s00",
        "--approver", "human:test", "--evidence", "brief accepted",
        "--approval-token", token,
        runs_root=tmp_path,
        env=env,
    ).returncode == 0
    glossary = run_dir / "artifacts" / "GLOSSARY.md"
    glossary.write_text(
        glossary.read_text(encoding="utf-8") + "| Бэклог | backlog | List |\n", encoding="utf-8"
    )
    broken = cli("verify", "--run-id", "demo", "--stage", "s00_intake", runs_root=tmp_path)
    assert broken.returncode == 1
    assert "tampered" in broken.stdout
    rerecord = cli(
        "artifact", "re-record", "--run-id", "demo", "--path", "artifacts/GLOSSARY.md",
        "--role", "01-smart-interviewer", "--reason", "добавлен термин после ревью",
        "--evidence", "artifacts/interview-brief.md#3", runs_root=tmp_path,
    )
    assert rerecord.returncode == 0, rerecord.stderr
    assert "RE-RECORDED" in rerecord.stdout
    assert "artifacts/GLOSSARY.md" in rerecord.stdout
    verify = cli("verify", "--run-id", "demo", "--stage", "s00_intake", runs_root=tmp_path)
    assert verify.returncode == 0, verify.stdout
    assert "re-records: 1" in verify.stdout
    assert "s00_intake: 1" in verify.stdout
    payload = json.loads(
        cli(
            "verify", "--run-id", "demo", "--stage", "s00_intake", "--json", runs_root=tmp_path
        ).stdout
    )
    assert payload["ok"] is True
    assert payload["re_records"][0]["paths"] == ["artifacts/GLOSSARY.md"]


def test_artifact_re_record_requires_reason_and_evidence(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    result = cli(
        "artifact", "re-record", "--run-id", "demo", "--path", "artifacts/GLOSSARY.md",
        "--role", "01-smart-interviewer", "--reason", "x", runs_root=tmp_path,
    )
    assert result.returncode == 1
    assert "--evidence" in result.stderr or "--evidence" in result.stdout


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


def test_log_audit_reports_and_rebuilds_the_mirror(tmp_path):
    """The mirror is a view of the journal; it can be rebuilt from it."""
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    audit = cli("log", "audit", "--run-id", "demo", runs_root=tmp_path)
    assert audit.returncode == 0
    assert "in sync" in audit.stdout

    trail = tmp_path / "demo" / "AUDIT_TRAIL.md"
    trail.write_text("stale\n", encoding="utf-8")
    stale = cli("log", "audit", "--run-id", "demo", runs_root=tmp_path)
    assert stale.returncode == 1
    assert "out of sync" in stale.stdout

    rebuilt = cli("log", "audit", "--run-id", "demo", "--rebuild", runs_root=tmp_path)
    assert rebuilt.returncode == 0
    assert cli("log", "audit", "--run-id", "demo", runs_root=tmp_path).returncode == 0


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
    # Measured on the real verifier run: worst completed 6 217 251 + 25 %,
    # rounded up (was 5 200 000 when the verifier step did not exist).
    assert data["mode_budget"]["tokens"] == 7_800_000
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
    # Worst green standard run after the verifier step: 7 581 128 + 25 %.
    assert standard["mode_budget"]["tokens"] == 9_500_000

    assert (
        cli("init", "--run-id", "demo-brownfield", "--mode", "brownfield", runs_root=tmp_path).returncode
        == 0
    )
    brownfield = json.loads(
        cli("budget", "--run-id", "demo-brownfield", runs_root=tmp_path).stdout
    )
    assert brownfield["mode"] == "brownfield"
    # 44.2 measured minutes and 7 269 116 tokens, both + 25 % and rounded up.
    assert brownfield["mode_budget"]["minutes"] == 60
    assert brownfield["mode_budget"]["tokens"] == 9_100_000


def test_brownfield_starts_at_s_in(tmp_path):
    assert cli("init", "--run-id", "legacy", "--mode", "brownfield", runs_root=tmp_path).returncode == 0
    status = json.loads(cli("status", "--run-id", "legacy", "--json", runs_root=tmp_path).stdout)
    assert status["current_stage"] == "s_in"
    assert status["restore_drill_passed"] is False


def test_next_exits_one_when_the_stage_is_escalated(tmp_path):
    assert cli("init", "--run-id", "demo", "--mode", "nano", runs_root=tmp_path).returncode == 0
    for attempt in range(3):
        token, env = _make_token(tmp_path, "demo", "gate_s00", "human:reviewer", "reject")
        rejected = cli(
            "gate", "reject", "--run-id", "demo", "--gate", "gate_s00",
            "--approver", "human:reviewer", "--evidence", f"reject {attempt}",
            "--approval-token", token,
            runs_root=tmp_path,
            env=env,
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


def test_no_sandbox_with_an_executor_user_needs_an_explicit_flag(tmp_path):
    """Without the sandbox the separate user is not a boundary for the control plane."""
    runs = tmp_path / "runs"
    result = cli(
        "run", "--run-id", "live", "--executor", "hermes",
        "--no-sandbox", "--executor-user", "factory-executor",
        runs_root=runs,
    )
    assert result.returncode == 1
    assert "--allow-unsafe-no-sandbox" in result.stderr

    allowed = cli(
        "run", "--run-id", "live", "--executor", "hermes",
        "--no-sandbox", "--executor-user", "factory-executor",
        "--allow-unsafe-no-sandbox",
        runs_root=runs,
    )
    assert "allow-unsafe-no-sandbox" not in allowed.stderr


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
