"""The trust boundary: an executor cannot write the control plane.

These tests exercise the real `bwrap` backend, not just the argv shape: a
sandboxed shell is asked to append to `state.json`, to truncate the usage
ledger and to run `factory gate approve` against its own run.  All of those
must fail at the filesystem level, while writing a declared artifact and
atomically replacing a root artifact must still work.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from factory.cli import _build_executor, build_parser
from factory.engine import init_run, next_action
from factory.runtime.base import RunContext, RuntimeFailure, safe_write_text
from factory.runtime.loop import (
    _cleanup_placeholders,
    _collect_writable_paths,
    _prepare_writable_paths,
)
from factory.runtime.sandbox import sandbox_available, sandbox_command, sandbox_env

SANDBOX = pytest.mark.skipif(not sandbox_available(), reason="bubblewrap is not installed")


def _sandbox_works() -> bool:
    """GitHub runners may install bwrap and still forbid user namespaces."""
    if not sandbox_available():
        return False
    try:
        proc = subprocess.run(
            sandbox_command(["true"], Path.cwd(), enabled=True),
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


if sandbox_available() and not _sandbox_works():
    SANDBOX = pytest.mark.skipif(
        True, reason="bwrap is installed but cannot create a user namespace here"
    )


@pytest.fixture()
def run(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    init_run("trust", "nano", root=runs, idea="trust boundary probe")
    run_dir = runs / "trust"
    (run_dir / "runtime" / "evidence").mkdir(parents=True, exist_ok=True)
    (run_dir / "runtime" / "usage.jsonl").write_text('{"tokens": 0}\n', encoding="utf-8")
    return run_dir


def _writable(run_dir: Path) -> list[str]:
    order = next_action(run_dir.name, root=run_dir.parent)
    entries = _collect_writable_paths(order, run_dir, None)
    writable, _ = _prepare_writable_paths(entries)
    return writable


def _sandboxed(run_dir: Path, writable: list[str], argv: list[str]):
    cmd = sandbox_command(argv, run_dir, read_write=tuple(writable))
    return subprocess.run(cmd, capture_output=True, text=True, env=sandbox_env(), timeout=60)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@SANDBOX
def test_executor_cannot_write_the_control_plane(run):
    writable = _writable(run)
    before = {
        name: _sha(run / name)
        for name in ("state.json", "blackbox.jsonl", "blackbox.lock")
    }
    targets = (
        "state.json",
        "blackbox.jsonl",
        "blackbox.lock",
        "runtime/usage.jsonl",
        "runtime/evidence/proof.json",
        "idea.md",
    )
    for target in targets:
        proc = _sandboxed(run, writable, ["sh", "-c", f"printf evil >> {run / target}"])
        assert proc.returncode != 0, f"{target} was writable inside the sandbox"
    for name, digest in before.items():
        assert _sha(run / name) == digest
    assert (run / "runtime" / "evidence" / "proof.json").exists() is False


@SANDBOX
def test_declared_artifact_directory_is_writable(run):
    writable = _writable(run)
    proc = _sandboxed(
        run, writable, ["sh", "-c", f"printf ok > {run / 'artifacts' / 'note.md'}"]
    )
    assert proc.returncode == 0, proc.stderr
    assert (run / "artifacts" / "note.md").read_text() == "ok"


@SANDBOX
def test_run_root_is_writable_but_the_control_plane_is_not(run):
    """The root artifacts (skills-lock.json) need directory write to be replaced atomically."""
    writable = _writable(run)
    before = _sha(run / "state.json")
    script = (
        f"touch {run / 'stray.txt'} && "
        f"printf '{{\"schema_version\": \"1.0.0\", \"skills\": []}}' > {run / '.lock.tmp'} && "
        f"mv {run / '.lock.tmp'} {run / 'skills-lock.json'} && "
        f"printf evil >> {run / 'state.json'}; echo state_rc=$?; "
        f"rm -f {run / 'state.json'}; echo unlink_rc=$?"
    )
    proc = _sandboxed(run, writable, ["sh", "-c", script])
    assert (run / "skills-lock.json").read_text().startswith('{"schema_version"')
    assert _sha(run / "state.json") == before
    assert "state_rc=0" not in proc.stdout
    assert "unlink_rc=0" not in proc.stdout


@SANDBOX
def test_root_artifact_inside_the_run_is_replaceable_atomically(run, monkeypatch):
    """A file bound on itself cannot be renamed over; the run root is enough."""
    monkeypatch.setenv("FACTORY_ROOT", str(run))
    order = {
        "artifacts": [
            {
                "id": "skills_lock",
                "path": "skills-lock.json",
                "resolved_path": "skills-lock.json",
                "root": True,
                "owner": "00-orchestrator",
            }
        ]
    }
    entries = _collect_writable_paths(order, run, None)
    writable, placeholders = _prepare_writable_paths(entries)
    try:
        target = (run / "skills-lock.json").resolve()
        assert str(target) in writable
        cmd = sandbox_command(["true"], run, read_write=tuple(writable))
        file_binds = [cmd[index + 1] for index, item in enumerate(cmd) if item == "--bind"]
        assert str(target) not in file_binds
        script = (
            f"printf '{{\"schema_version\": \"1.0.0\", \"skills\": []}}' > {run}/.tmp-lock && "
            f"mv {run}/.tmp-lock {run}/skills-lock.json"
        )
        proc = subprocess.run(
            sandbox_command(["sh", "-c", script], run, read_write=tuple(writable)),
            capture_output=True,
            text=True,
            env=sandbox_env(),
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert '"skills": []' in (run / "skills-lock.json").read_text()
    finally:
        _cleanup_placeholders(placeholders)


@SANDBOX
def test_eval_checks_are_read_only_inside_the_sandbox(run):
    checks = run / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "test_product.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    before = _sha(checks / "test_product.py")
    writable = _writable(run)
    proc = _sandboxed(
        run, writable, ["sh", "-c", f"printf 'def test_ok(): pass' > {checks / 'test_product.py'}"]
    )
    assert proc.returncode != 0
    assert _sha(checks / "test_product.py") == before


@SANDBOX
def test_hardlink_cannot_reach_the_read_only_mount(run):
    writable = _writable(run)
    before = _sha(run / "state.json")
    script = (
        f"ln {run / 'state.json'} {run / 'artifacts' / 'state-link'} 2>/dev/null; "
        f"printf evil >> {run / 'artifacts' / 'state-link'}"
    )
    _sandboxed(run, writable, ["sh", "-c", script])
    assert _sha(run / "state.json") == before
    link = run / "artifacts" / "state-link"
    assert link.exists()
    assert link.stat().st_ino != (run / "state.json").stat().st_ino


@SANDBOX
def test_factory_cli_cannot_close_a_gate_inside_the_sandbox(run):
    binary = Path(sys.executable).with_name("factory")
    if not binary.exists():
        binary = shutil.which("factory")
    if not binary:
        pytest.skip("factory console script is not installed")
    writable = _writable(run)
    before = _sha(run / "state.json")
    proc = _sandboxed(
        run,
        writable,
        [
            str(binary),
            "gate",
            "approve",
            "--run-id",
            run.name,
            "--gate",
            "gate_s3",
            "--approver",
            "02-independent-verifier",
            "--runs-root",
            str(run.parent),
        ],
    )
    assert proc.returncode != 0
    assert _sha(run / "state.json") == before
    # The command must die on the read-only lock, not on an unrelated parse error.
    assert "blackbox.lock" in (proc.stderr or "")


def test_writable_paths_exclude_the_run_root_and_control_plane(run):
    order = next_action(run.name, root=run.parent)
    entries = _collect_writable_paths(order, run, None)
    exposed = {str(path) for path in entries}
    assert str(run.resolve()) not in exposed
    assert str((run / "runtime").resolve()) not in exposed
    assert str((run / "runtime" / "evidence").resolve()) not in exposed
    assert str((run / "artifacts").resolve()) in exposed
    assert str((run / "runtime" / "agent").resolve()) in exposed


def test_root_artifact_exposes_one_file_not_the_project(monkeypatch):
    project = Path.home() / ".cache" / "factory-trust-tests" / uuid4().hex
    project.mkdir(parents=True)
    run_dir = project / "runs" / "root-art"
    run_dir.mkdir(parents=True)
    try:
        monkeypatch.setenv("FACTORY_ROOT", str(project))
        order = {
            "artifacts": [
                {"id": "lock", "path": "skills-lock.json", "root": True, "owner": "20"}
            ]
        }
        entries = _collect_writable_paths(order, run_dir, None)
        assert (project / "skills-lock.json").resolve() in entries
        assert project.resolve() not in entries
        with pytest.raises(RuntimeFailure):
            sandbox_command(["true"], run_dir, read_write=(str(project),))
    finally:
        shutil.rmtree(project, ignore_errors=True)


def test_sandbox_refuses_the_factory_package(tmp_path):
    from factory.runtime.sandbox import PACKAGE_DIR

    with pytest.raises(RuntimeFailure):
        sandbox_command(["true"], tmp_path, read_write=(str(PACKAGE_DIR),))
    with pytest.raises(RuntimeFailure):
        sandbox_command(["true"], tmp_path, read_write=(str(PACKAGE_DIR.parent),))


def test_placeholder_is_removed_only_when_the_executor_did_not_write(run):
    _, created = _prepare_writable_paths({(run / "placeholder.md"): False})
    assert (run / "placeholder.md").exists()
    _cleanup_placeholders(created)
    assert not (run / "placeholder.md").exists()

    _, created = _prepare_writable_paths({(run / "written.md"): False})
    (run / "written.md").write_text("content")
    _cleanup_placeholders(created)
    assert (run / "written.md").read_text() == "content"


def test_real_executors_default_to_the_sandbox():
    parser = build_parser()
    args = parser.parse_args(["run", "--run-id", "x", "--executor", "hermes"])
    assert getattr(_build_executor(args), "sandbox") is True
    args = parser.parse_args(
        ["run", "--run-id", "x", "--executor", "hermes", "--no-sandbox"]
    )
    assert getattr(_build_executor(args), "sandbox") is False
    args = parser.parse_args(["run", "--run-id", "x", "--executor", "fake"])
    assert getattr(_build_executor(args), "sandbox", False) is False


def test_sandbox_env_drops_factory_control_variables():
    env = sandbox_env(
        {
            "FACTORY_ROOT": "/repo",
            "FACTORY_RUNS_ROOT": "/runs",
            "FACTORY_HERMES_MODEL": "m",
            "PYTHONPATH": "/evil",
            "HOME": "/home/user",
            "OPENAI_API_KEY": "key",
        }
    )
    assert "FACTORY_ROOT" not in env
    assert "FACTORY_RUNS_ROOT" not in env
    assert "FACTORY_HERMES_MODEL" not in env
    assert "PYTHONPATH" not in env
    assert env["HOME"] == "/home/user"
    assert env["OPENAI_API_KEY"] == "key"


def test_runtime_never_writes_through_a_symlink(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "runtime").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("safe")
    link = run_dir / "runtime" / "prompt.md"
    link.symlink_to(outside)
    safe_write_text(link, "prompt")
    assert outside.read_text() == "safe"
    assert not link.is_symlink()
    assert link.read_text() == "prompt"


def test_symlinked_runtime_directory_is_refused(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_dir / "runtime").symlink_to(outside)
    context = RunContext(
        run_id="run",
        run_dir=run_dir,
        step=1,
        order={},
        mode="nano",
        prompt="",
        runtime_dir=run_dir / "runtime",
    )
    with pytest.raises(RuntimeFailure):
        context.path_in_runtime("prompt.md")
