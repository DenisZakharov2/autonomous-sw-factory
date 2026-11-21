"""Host replay of a verifier report: unit and sandbox tests."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from factory import config as config_module
from factory.config import ConfigError, load_budget
from factory.host_verify import (
    _matches,
    _normalise,
    _output_mismatch,
    _product_has_files,
    replay_report,
)
from factory.runtime.sandbox import sandbox_available, sandbox_command

BASE = "default_max_attempts: 3\nmax_veto_retries: 3\n"


def _budget(tmp_path: Path, monkeypatch, extra: str):
    monkeypatch.setattr(config_module, "PACKAGE_ROOT", tmp_path)
    (tmp_path / "budget.yaml").write_text(BASE + extra, encoding="utf-8")
    return load_budget()


def _report(path: Path, blocks, verdict: str = "PASS") -> Path:
    lines = [
        "# Отчёт",
        "",
        "## 1. Проверено",
        "",
        "- Предмет проверки: `stage s4_build`",
        "",
        "## 2. Проверки",
        "",
    ]
    for index, (command, code, output) in enumerate(blocks, 1):
        lines += [
            f"### Проверка {index}: проверка {index}",
            "",
            f"- Команда: `{command}`",
            f"- Код возврата: `{code}`",
            "- Вывод:",
            "",
            "```text",
            output,
            "```",
            "",
        ]
    lines += ["## 3. Вердикт", "", f"- Вердикт: {verdict}", "- Причина: `проверено`", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _works() -> bool:
    if not sandbox_available():
        return False
    try:
        completed = subprocess.run(
            sandbox_command(["true"], Path.cwd(), enabled=True, network=False),
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _product(root: Path, passing: bool = True) -> None:
    tests = root / "product" / "tests"
    tests.mkdir(parents=True)
    body = "def test_ok():\n    assert True\n"
    if not passing:
        body = "def test_bad():\n    assert False\n"
    (tests / "test_sample.py").write_text(body, encoding="utf-8")


def test_a_shell_in_the_allowlist_is_a_config_error(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="may not run"):
        _budget(tmp_path, monkeypatch, "verify_commands:\n  - ['bash', '-c', 'true']\n")


def test_a_malformed_allowlist_is_a_config_error(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="argv lists"):
        _budget(tmp_path, monkeypatch, "verify_commands: 'pytest'\n")


def test_the_timeout_has_ceilings(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="verify_timeout_s"):
        _budget(tmp_path, monkeypatch, "verify_timeout_s: 0\n")
    with pytest.raises(ConfigError, match="verify_timeout_s"):
        _budget(tmp_path, monkeypatch, "verify_timeout_s: 99999\n")


def test_the_command_cap_has_ceilings(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="verify_max_commands"):
        _budget(tmp_path, monkeypatch, "verify_max_commands: 0\n")
    with pytest.raises(ConfigError, match="verify_max_commands"):
        _budget(tmp_path, monkeypatch, "verify_max_commands: 1000\n")


def test_an_unknown_output_mode_is_a_config_error(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="verify_output"):
        _budget(tmp_path, monkeypatch, 'verify_output: "sometimes"\n')


def test_a_valid_block_is_read_with_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "factory.host_verify.load_budget",
        lambda: {"verify_commands": [["python3", "-m", "pytest"]]},
    )
    from factory.host_verify import config

    data = config()
    assert data["commands"] == [["python3", "-m", "pytest"]]
    assert data["timeout_s"] == 600
    assert data["max_commands"] == 10
    assert data["require_for_pass"] is True
    assert data["output"] == "strict"


def test_the_allowlist_matches_an_argv_head():
    prefix = ["python3", "-m", "pytest"]
    assert _matches(["python3", "-m", "pytest", "-q", "product"], prefix)
    assert not _matches(["python3", "-m", "pytesthtml"], prefix)
    assert not _matches(["python3", "-m", "unittest"], prefix)
    assert not _matches(["python3"], prefix)


def test_timings_are_normalised_on_both_sides():
    assert _normalise("Ran 3 tests in 0.003s") == _normalise("Ran 3 tests in 1.2s")
    assert _normalise("1 passed in 0.003s") == _normalise("1 passed in 12.5s")


def test_the_report_may_omit_lines_but_not_invent_them():
    actual = "collecting\n1 passed in 0.02s\n"
    assert _output_mismatch("collecting\n1 passed in 0.01s", actual) is None
    assert _output_mismatch("1 passed in 0.01s", actual) is None
    assert _output_mismatch("...", actual) is None
    assert _output_mismatch("all 42 requirements covered", actual) is not None


def test_the_report_output_keeps_its_order():
    actual = "first\nsecond\nlast\n"
    assert _output_mismatch("first\nlast", actual) is None
    assert _output_mismatch("last\nfirst", actual) is not None


def test_product_roots_come_from_the_mode_registry(tmp_path):
    run = tmp_path / "run-01"
    green = run / "product" / "src"
    green.mkdir(parents=True)
    (green / "todo.py").write_text("NAME = 'todo'\n", encoding="utf-8")
    assert _product_has_files(run)
    brownfield = {"artifacts": {"product_src": {"path": "app/src-tauri/src/"}}}
    assert not _product_has_files(run, brownfield)
    seeded = run / "app" / "src-tauri" / "src"
    seeded.mkdir(parents=True)
    (seeded / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    assert _product_has_files(run, brownfield)


def test_product_detection_does_not_follow_directory_symlinks(tmp_path):
    run = tmp_path / "run-01"
    (run / "product").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n", encoding="utf-8")
    try:
        (run / "product" / "linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    assert not _product_has_files(run)


@pytest.mark.skipif(not sandbox_available(), reason="bubblewrap is not installed")
def test_a_venv_under_tmp_is_rebound_after_the_tmpfs(tmp_path, monkeypatch):
    """CI installs the factory into /tmp/sdist-venv; the replay must see it."""
    from factory.host_verify import _sandboxed
    import sys

    prefix = Path("/tmp") / f"sdist-venv-{os.getpid()}"
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "executable", str(prefix / "bin" / "python"))
    command = _sandboxed(["python3", "-m", "pytest"], tmp_path)
    tmpfs = next(
        index
        for index, token in enumerate(command)
        if token == "/tmp" and command[index - 1] == "--tmpfs"
    )
    rebound = command.index(str(prefix))
    assert tmpfs < rebound
    assert command[rebound - 1] == "--ro-bind"
    assert command[rebound + 1] == str(prefix)


@pytest.mark.skipif(not sandbox_available(), reason="bubblewrap is not installed")
class TestReplay:
    def test_a_truthful_pass_is_replayed(self, tmp_path):
        if not _works():
            pytest.skip("bubblewrap cannot create a namespace here")
        root = tmp_path / "run-01"
        _product(root)
        report = _report(root / "verification.md", [
            ("python3 -m pytest -q product", 0, "1 passed in 0.02s"),
            ("python3 -m pytest -q product/tests", 0, "1 passed in 0.02s"),
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
        ])
        result = replay_report(report, root)
        assert result.ok, result.errors
        assert result.executed == 2
        assert any("host replay" in item for item in result.evidence)

    def test_a_claimed_pass_that_fails_is_refused(self, tmp_path):
        if not _works():
            pytest.skip("bubblewrap cannot create a namespace here")
        root = tmp_path / "run-01"
        _product(root, passing=False)
        report = _report(root / "verification.md", [
            ("python3 -m pytest -q product", 0, "1 passed in 0.02s"),
            ("python3 -m unittest discover -s product", 0, "OK"),
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
        ])
        result = replay_report(report, root)
        assert not result.ok
        assert any(
            "exit 1" in error and "claims exit 0" in error for error in result.errors
        ), result.errors

    def test_an_invented_output_line_is_refused(self, tmp_path):
        if not _works():
            pytest.skip("bubblewrap cannot create a namespace here")
        root = tmp_path / "run-01"
        _product(root)
        report = _report(root / "verification.md", [
            ("python3 -m pytest -q product", 0, "1 passed in 0.02s"),
            ("python3 -m pytest -q product/tests", 0, "all 42 requirements covered"),
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
        ])
        result = replay_report(report, root)
        assert not result.ok
        assert any("did not produce that line" in error for error in result.errors), result.errors

    def test_a_check_that_is_not_allowlisted_is_not_run(self, tmp_path):
        if not _works():
            pytest.skip("bubblewrap cannot create a namespace here")
        root = tmp_path / "run-01"
        _product(root)
        marker = tmp_path / "pwned"
        report = _report(root / "verification.md", [
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
            (f"bash -c 'touch {marker}'", 0, "touched"),
            ("python3 -m pytest -q product", 0, "1 passed in 0.02s"),
        ])
        result = replay_report(report, root)
        assert result.ok, result.errors
        assert result.executed == 1
        assert result.skipped == 2
        assert not marker.exists()

    def test_no_replayable_check_refuses_a_product_pass(self, tmp_path):
        if not _works():
            pytest.skip("bubblewrap cannot create a namespace here")
        root = tmp_path / "run-01"
        _product(root)
        marker = tmp_path / "pwned"
        report = _report(root / "verification.md", [
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
            (f"bash -c 'touch {marker}'", 0, "touched"),
            ("test -r product/src", 0, "readable"),
        ])
        result = replay_report(report, root)
        assert not result.ok
        assert any("no replayable check" in error for error in result.errors), result.errors
        assert not marker.exists()

    def test_a_failing_check_cannot_evidence_a_pass(self, tmp_path):
        if not _works():
            pytest.skip("bubblewrap cannot create a namespace here")
        root = tmp_path / "run-01"
        _product(root, passing=False)
        report = _report(root / "verification.md", [
            ("python3 -m pytest -q product", 1, "1 failed in 0.02s"),
            ("python3 -m pytest -q product/tests", 1, "1 failed in 0.02s"),
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
        ])
        result = replay_report(report, root)
        assert not result.ok
        assert any("failing check cannot be the evidence" in error for error in result.errors), result.errors

    def test_a_task_report_needs_no_replayable_check(self, tmp_path):
        root = tmp_path / "run-01"
        _product(root)
        report = _report(root / "verification.md", [
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
            ("test -r product/src", 0, "readable"),
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
        ])
        result = replay_report(report, root, require_executable=False)
        assert result.ok, result.errors

    def test_an_empty_product_needs_no_replayable_check(self, tmp_path):
        root = tmp_path / "run-01"
        root.mkdir()
        report = _report(root / "verification.md", [
            ("factory verify --run-id run-01 --stage s15_quarantine", 0, "VERIFY OK"),
            ("test -r artifacts/security-review-skills.md", 0, "readable"),
            ("factory verify --run-id run-01 --stage s15_quarantine", 0, "VERIFY OK"),
        ])
        result = replay_report(report, root, require_executable=True)
        assert result.ok, result.errors

    def test_a_timeout_refuses_the_pass(self, tmp_path, monkeypatch):
        if not _works():
            pytest.skip("bubblewrap cannot create a namespace here")
        root = tmp_path / "run-01"
        tests = root / "product" / "tests"
        tests.mkdir(parents=True)
        (tests / "test_slow.py").write_text(
            "import time\n\ndef test_slow():\n    time.sleep(5)\n", encoding="utf-8"
        )
        report = _report(root / "verification.md", [
            ("python3 -m pytest -q product", 0, "1 passed in 0.02s"),
            ("python3 -m pytest -q product/tests", 0, "1 passed in 0.02s"),
            ("factory verify --run-id run-01 --stage s4_build", 0, "VERIFY OK"),
        ])
        monkeypatch.setattr(
            "factory.host_verify.config",
            lambda: {
                "commands": [["python3", "-m", "pytest"]],
                "timeout_s": 1,
                "max_commands": 10,
                "require_for_pass": True,
                "output": "strict",
            },
        )
        result = replay_report(report, root)
        assert not result.ok
        assert any("did not finish within" in error for error in result.errors), result.errors

