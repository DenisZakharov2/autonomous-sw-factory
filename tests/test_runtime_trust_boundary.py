"""The trust boundary: an executor cannot write the control plane.

These tests exercise the real `bwrap` backend, not just the argv shape: a
sandboxed shell is asked to append to `state.json`, to truncate the usage
ledger and to run `factory gate approve` against its own run.  All of those
must fail at the filesystem level, while writing a declared artifact and
atomically replacing a root artifact must still work.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from factory.cli import _build_executor, build_parser
from factory.config import project_root as factory_project_root
from factory.engine import init_run, next_action
from factory.errors import JournalError
from factory.runtime.base import RunContext, RuntimeFailure, safe_write_text
from factory.runtime.loop import (
    _cleanup_placeholders,
    _collect_writable_paths,
    _prepare_writable_paths,
    _writable_entries,
)
from factory.runtime.isolation import executor_user_uid
from factory.runtime.sandbox import sandbox_available, sandbox_command, sandbox_env
from factory.runtime.staging import (
    WritableEntry,
    build_plan,
    cleanup,
    prepare,
    promote,
    staging_root,
)

SANDBOX = pytest.mark.skipif(not sandbox_available(), reason="bubblewrap is not installed")
POSIX_ONLY = pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="O_NOFOLLOW is POSIX-only; the Windows writer is a best-effort fallback",
)


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


@pytest.fixture()
def home_dir():
    """A fake HOME outside `/tmp`: the sandbox mounts an empty tmpfs on `/tmp`."""
    home = Path.home() / ".cache" / "factory-trust-tests" / uuid4().hex
    home.mkdir(parents=True)
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _stage_plan(run_dir: Path):
    order = next_action(run_dir.name, root=run_dir.parent)
    plan = build_plan(
        _writable_entries(order, run_dir, None),
        run_dir=run_dir,
        project=factory_project_root(),
        staging=staging_root(run_dir),
        allow_project_root=True,
    )
    prepared = prepare(plan, run_dir=run_dir)
    mounts = [str((run_dir / "runtime" / "agent").resolve())]
    staged = tuple((str(dest), str(src)) for dest, src in prepared.binds)
    return plan, prepared, mounts, staged


def _sandboxed(
    run_dir: Path,
    writable: list[str],
    argv: list[str],
    staged: tuple[tuple[str, str], ...] = (),
):
    cmd = sandbox_command(argv, run_dir, read_write=tuple(writable), staged=staged)
    return subprocess.run(cmd, capture_output=True, text=True, env=sandbox_env(), timeout=60)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@SANDBOX
def test_executor_cannot_write_the_control_plane(run):
    plan, prepared, writable, staged = _stage_plan(run)
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
        proc = _sandboxed(
            run, writable, ["sh", "-c", f"printf evil >> {run / target}"], staged
        )
        assert proc.returncode != 0, f"{target} was writable inside the sandbox"
    for name, digest in before.items():
        assert _sha(run / name) == digest
    assert (run / "runtime" / "evidence" / "proof.json").exists() is False
    cleanup(prepared, run_dir=run)


@SANDBOX
def test_declared_artifact_directory_is_writable(run):
    plan, prepared, writable, staged = _stage_plan(run)
    artifact = run / "artifacts" / "interview-brief.md"
    proc = _sandboxed(
        run,
        writable,
        ["sh", "-c", f"printf ok > {artifact}"],
        staged,
    )
    assert proc.returncode == 0, proc.stderr
    # The write landed in staging; the run root was not writable at all.
    assert not artifact.exists()
    assert (run / "runtime" / "agent" / "out" / "artifacts" / "interview-brief.md").read_text() == "ok"
    promote(plan, run_dir=run, project=factory_project_root())
    cleanup(prepared, run_dir=run)
    assert artifact.read_text() == "ok"


@SANDBOX
def test_executor_cannot_write_the_verifiers_report(run):
    """A witness the producer wrote is not a witness."""
    (run / "artifacts" / "verification" / "s4_build").mkdir(parents=True, exist_ok=True)
    (run / "tasks" / "TASK-001" / "verification").mkdir(parents=True, exist_ok=True)
    plan, prepared, writable, staged = _stage_plan(run)
    targets = (
        "artifacts/verification/s4_build/verification.md",
        "tasks/TASK-001/verification/verification.md",
    )
    for target in targets:
        proc = _sandboxed(
            run,
            writable,
            ["sh", "-c", f"printf 'PASS: forged\\n' > {run / target}"],
            staged,
        )
        assert proc.returncode != 0, f"{target} was writable inside the sandbox"
        assert not (run / target).exists()
    promote(plan, run_dir=run, project=factory_project_root())
    for target in targets:
        assert not (run / target).exists()
    cleanup(prepared, run_dir=run)


@SANDBOX
def test_the_verifier_can_write_its_report(run):
    """The protection must not reach the role that has to write the report."""
    report_dir = run / "artifacts" / "verification" / "s4_build"
    report_dir.mkdir(parents=True, exist_ok=True)
    plan = build_plan(
        [WritableEntry(report_dir, "tree")],
        run_dir=run,
        project=factory_project_root(),
        staging=staging_root(run),
        allow_project_root=True,
    )
    prepared = prepare(plan, run_dir=run)
    staged = tuple((str(dest), str(src)) for dest, src in prepared.binds)
    proc = _sandboxed(
        run,
        [str((run / "runtime" / "agent").resolve())],
        ["sh", "-c", f"printf 'PASS: written\\n' > {report_dir / 'verification.md'}"],
        staged,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (report_dir / "verification.md").exists()
    promote(plan, run_dir=run, project=factory_project_root())
    cleanup(prepared, run_dir=run)
    assert (report_dir / "verification.md").read_text() == "PASS: written\n"


def test_the_executor_does_not_inherit_the_operators_credentials(monkeypatch):
    """A token that leaks into the executor leaks out."""
    from factory.runtime.sandbox import sandbox_env

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_operator_token")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "operator_secret")
    monkeypatch.setenv("NPM_TOKEN", "npm_operator_token")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/1000/ssh-agent.socket")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-the-model-call")

    env = sandbox_env()

    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "NPM_TOKEN" not in env
    assert "SSH_AUTH_SOCK" not in env
    # The model call belongs to the credential proxy, not to a key handed to
    # untrusted code.  A provider key in this environment is a hole.
    assert "OPENAI_API_KEY" not in env


@SANDBOX
def test_the_executor_environment_inside_the_sandbox_has_no_credentials(run, monkeypatch):
    """The unit test above checks the filter; this one checks the process it feeds."""
    for name, value in (
        ("OPENAI_API_KEY", "***"),
        ("ANTHROPIC_API_KEY", "***"),
        ("AWS_SECRET_ACCESS_KEY", "***"),
        ("GITHUB_TOKEN", "***"),
        ("NETRC", "/home/user/.netrc"),
        ("SSH_AUTH_SOCK", "/run/user/1000/ssh-agent.socket"),
        ("KUBECONFIG", "/home/user/.kube/config"),
        ("PR_C1_PLAIN", "kept"),
    ):
        monkeypatch.setenv(name, value)

    plan, prepared, writable, staged = _stage_plan(run)
    proc = _sandboxed(run, writable, ["sh", "-c", "env"], staged)
    assert proc.returncode == 0, proc.stderr
    assert "PR_C1_PLAIN=kept" in proc.stdout
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "NETRC",
        "SSH_AUTH_SOCK",
        "KUBECONFIG",
    ):
        assert f"{name}=" not in proc.stdout
    cleanup(prepared, run_dir=run)


def test_sandbox_env_drops_every_credential_suffix():
    from factory.runtime.sandbox import sandbox_env

    env = sandbox_env(
        {
            "OPENAI_API_KEY": "***",
            "ANTHROPIC_API_KEY": "***",
            "AWS_ACCESS_KEY_ID": "***",
            "AWS_SECRET_ACCESS_KEY": "***",
            "SERVICE_ACCOUNT_API_KEY": "***",
            "SIGNING_ACCESS_KEY": "***",
            "MY_SECRET_KEY": "***",
            "APP_SECRET": "***",
            "NETRC": "/home/user/.netrc",
            "KUBECONFIG": "/home/user/.kube/config",
            "GOOGLE_APPLICATION_CREDENTIALS": "/home/user/gcp.json",
            "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.socket",
            "GPG_AGENT_INFO": "/run/user/1000/gpg",
            "FACTORY_APPROVAL_KEY": "approval",
            "PYTHONPATH": "/evil",
            "PATH": "/usr/bin",
        }
    )

    assert env == {"PATH": "/usr/bin"}


@SANDBOX
def test_the_operator_secret_directories_are_empty_inside_the_sandbox(run, home_dir, monkeypatch):
    """Read-only is not private: in the default mode the executor is the operator."""
    home = home_dir
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("operator secret\n", encoding="utf-8")
    (home / ".claude").mkdir()
    (home / ".claude" / "credentials.json").write_text("operator secret\n", encoding="utf-8")
    (home / ".netrc").write_text("machine api password operator secret\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    plan, prepared, writable, staged = _stage_plan(run)
    proc = _sandboxed(
        run,
        writable,
        [
            "sh",
            "-c",
            f"cat {home / '.ssh' / 'id_rsa'} 2>&1 || true; "
            f"cat {home / '.claude' / 'credentials.json'} 2>&1 || true; "
            f"cat {home / '.netrc'} 2>&1 || true",
        ],
        staged,
    )
    assert "operator secret" not in proc.stdout, proc.stdout
    cleanup(prepared, run_dir=run)


def test_home_mask_args_cover_every_secret_path(tmp_path):
    """A secret directory gets a tmpfs, a credential file gets /dev/null."""
    from factory.runtime.sandbox import (
        HERMES_HOME_PASSTHROUGH,
        SECRET_HOME_DIRS,
        home_mask_args,
    )

    files = {".netrc", ".npmrc", ".pypirc", ".git-credentials"}
    for relative in SECRET_HOME_DIRS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in files:
            target.write_text("operator secret\n", encoding="utf-8")
        else:
            target.mkdir(exist_ok=True)

    args = home_mask_args(tmp_path)

    expected: list[str] = []
    for relative in SECRET_HOME_DIRS:
        target = tmp_path / relative
        if relative in files:
            expected.extend(["--ro-bind", "/dev/null", str(target)])
        else:
            expected.extend(["--tmpfs", str(target)])
    for name in HERMES_HOME_PASSTHROUGH:
        target = tmp_path / ".hermes" / name
        expected.extend(["--bind", str(target), str(target)])

    assert args == expected
    for name in HERMES_HOME_PASSTHROUGH:
        assert (tmp_path / ".hermes" / name).is_dir()


def test_home_mask_args_pass_the_hermes_install_back_read_only(tmp_path):
    from factory.runtime.sandbox import HERMES_HOME_READ_ONLY, home_mask_args

    (tmp_path / ".hermes" / "cache").mkdir(parents=True)
    for name in HERMES_HOME_READ_ONLY:
        (tmp_path / ".hermes" / name).mkdir()

    args = home_mask_args(tmp_path)
    for name in HERMES_HOME_READ_ONLY:
        target = str(tmp_path / ".hermes" / name)
        assert args[args.index(target) - 1] == "--ro-bind"


@SANDBOX
def test_the_hermes_home_is_a_tmpfs_except_the_adapter_directories(run, home_dir, monkeypatch):
    """`~/.hermes` carries the operator's keys; only cache/logs/installs come through."""
    hermes = home_dir / ".hermes"
    for name in ("cache", "logs", "installs"):
        (hermes / name).mkdir(parents=True)
    (hermes / "config.yaml").write_text("api_key: operator secret\n", encoding="utf-8")
    (hermes / "auth.json").write_text('{"token": "operator secret"}\n', encoding="utf-8")
    (hermes / ".env").write_text("OPENAI_API_KEY=operator secret\n", encoding="utf-8")
    (hermes / "sessions").mkdir()
    (hermes / "sessions" / "chat.md").write_text("operator secret\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home_dir))

    plan, prepared, writable, staged = _stage_plan(run)
    listing = _sandboxed(run, writable, ["sh", "-c", f"ls -A {hermes}"], staged)
    assert set(listing.stdout.split()) == {"cache", "logs", "installs"}, listing.stdout

    secrets = _sandboxed(
        run,
        writable,
        ["sh", "-c", f"cat {hermes}/config.yaml {hermes}/.env {hermes}/sessions/chat.md"],
        staged,
    )
    assert "operator secret" not in secrets.stdout, secrets.stdout

    write = _sandboxed(
        run, writable, ["sh", "-c", f"printf adapter > {hermes}/cache/probe.txt"], staged
    )
    assert write.returncode == 0, write.stderr
    assert (hermes / "cache" / "probe.txt").read_text() == "adapter"
    cleanup(prepared, run_dir=run)


@SANDBOX
def test_the_hermes_install_is_visible_read_only_inside_the_sandbox(run, home_dir, monkeypatch):
    """Masking `~/.hermes` must not mask the CLI the executor is asked to run."""
    hermes = home_dir / ".hermes"
    (hermes / "cache").mkdir(parents=True)
    (hermes / "hermes-agent").mkdir()
    (hermes / "hermes-agent" / "version.txt").write_text("hermes code\n", encoding="utf-8")
    (hermes / "tools").mkdir()
    (hermes / "config.yaml").write_text("api_key: operator secret\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home_dir))

    plan, prepared, writable, staged = _stage_plan(run)
    probe = hermes / "hermes-agent" / "version.txt"
    read = _sandboxed(run, writable, ["sh", "-c", f"cat {probe}"], staged)
    assert read.returncode == 0, read.stderr
    assert read.stdout.strip() == "hermes code"

    write = _sandboxed(
        run, writable, ["sh", "-c", f"printf evil > {hermes / 'hermes-agent' / 'x'}"], staged
    )
    assert write.returncode != 0
    assert not (hermes / "hermes-agent" / "x").exists()

    secret = _sandboxed(run, writable, ["sh", "-c", f"cat {hermes / 'config.yaml'} 2>&1 || true"], staged)
    assert "operator secret" not in secret.stdout
    cleanup(prepared, run_dir=run)


@POSIX_ONLY
def test_evidence_does_not_follow_a_planted_symlink(run):
    """The evidence directory is in the executor's reach; the write must not be."""
    from factory.runtime.evidence import EvidenceStore

    outside = run.parent / "outside.txt"
    outside.write_text("original\n", encoding="utf-8")
    evidence_dir = run / "runtime" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(run)
    planted = evidence_dir / f"step-{store._sequence():04d}-s4_build-01.json"
    try:
        planted.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    with pytest.raises(JournalError):
        store.write_step(1, {"stage": "s4_build"}, {"status": "ok"})

    assert outside.read_text() == "original\n"
    assert planted.is_symlink()

ACL = pytest.mark.skipif(
    os.name != "posix"
    or shutil.which("getfacl") is None
    or shutil.which("setfacl") is None,
    reason="POSIX ACLs are not available on this platform",
)


def _acl(path: Path) -> str:
    completed = subprocess.run(
        ["getfacl", "-p", "-c", "--", str(path)], capture_output=True, text=True, check=False
    )
    return completed.stdout


@ACL
def test_the_executor_grant_leaves_the_run_root_alone(tmp_path):
    """The run root holds the control plane: write access there is access to it."""
    from factory.runtime.isolation import grant_run_tree

    run = tmp_path / "run-01"
    for part in ("artifacts", "tasks", "product/src", "runtime/agent"):
        (run / part).mkdir(parents=True)
    (run / "state.json").write_text("{}", encoding="utf-8")

    grant_run_tree("nobody", run)

    root = _acl(run)
    assert "user:nobody" not in root, root
    assert "default:user:nobody" not in root, root
    work = _acl(run / "artifacts")
    assert "user:nobody" in work, work


@ACL
def test_a_new_control_plane_file_does_not_inherit_the_executor(tmp_path):
    """The engine rewrites `state.json` atomically; the new inode must be clean."""
    import os

    from factory.runtime.isolation import grant_run_tree

    run = tmp_path / "run-01"
    for part in ("artifacts", "tasks", "product/src", "runtime/agent"):
        (run / part).mkdir(parents=True)
    state = run / "state.json"
    state.write_text("{}", encoding="utf-8")

    grant_run_tree("nobody", run)

    tmp = state.with_name(f".{state.name}.{os.getpid()}.tmp")
    tmp.write_text("{}", encoding="utf-8")
    os.replace(tmp, state)

    assert "user:nobody" not in _acl(state), _acl(state)


@SANDBOX
def test_the_run_root_itself_is_not_writable(run):
    """The root is read-only; only a declared path is replaced by its staging copy."""
    plan, prepared, writable, staged = _stage_plan(run)
    before = _sha(run / "state.json")
    script = (
        f"touch {run / 'stray.txt'}; echo touch_rc=$?; "
        f"printf x > {run / '.lock.tmp'}; echo tmp_rc=$?; "
        f"mv {run / '.lock.tmp'} {run / 'skills-lock.json'}; echo mv_rc=$?; "
        f"printf evil >> {run / 'state.json'}; echo state_rc=$?; "
        f"rm -f {run / 'state.json'}; echo unlink_rc=$?"
    )
    proc = _sandboxed(run, writable, ["sh", "-c", script], staged)
    assert not (run / "stray.txt").exists()
    assert not (run / "skills-lock.json").exists()
    assert _sha(run / "state.json") == before
    for marker in ("touch_rc=0", "tmp_rc=0", "mv_rc=0", "state_rc=0", "unlink_rc=0"):
        assert marker not in proc.stdout, (marker, proc.stdout, proc.stderr)
    cleanup(prepared, run_dir=run)


@SANDBOX
def test_root_artifact_is_written_through_staging(run, monkeypatch):
    """A project-root artifact cannot be mounted: it is staged and promoted."""
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
    stage = staging_root(run)
    plan = build_plan(
        _writable_entries(order, run, None),
        run_dir=run,
        project=factory_project_root(),
        staging=stage,
        allow_project_root=True,
    )
    assert plan[0].mount is None
    prepared = prepare(plan, run_dir=run)
    staged = tuple((str(dest), str(src)) for dest, src in prepared.binds)
    assert not staged
    writable = [str((run / "runtime" / "agent").resolve())]
    target = run / "skills-lock.json"
    direct = _sandboxed(
        run,
        writable,
        ["sh", "-c", f"printf direct > {target}"],
        staged,
    )
    assert direct.returncode != 0
    assert not target.exists()

    payload = '{"schema_version": "1.0.0", "skills": []}'
    proc = _sandboxed(
        run,
        writable,
        ["sh", "-c", f"printf '%s' '{payload}' > {stage / '_root' / 'skills-lock.json'}"],
        staged,
    )
    assert proc.returncode == 0, proc.stderr
    assert not target.exists()
    promote(plan, run_dir=run, project=factory_project_root())
    cleanup(prepared, run_dir=run)
    assert target.read_text() == payload


@SANDBOX
def test_eval_checks_are_read_only_inside_the_sandbox(run):
    checks = run / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "test_product.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    before = _sha(checks / "test_product.py")
    plan, prepared, writable, staged = _stage_plan(run)
    proc = _sandboxed(
        run,
        writable,
        ["sh", "-c", f"printf 'def test_ok(): pass' > {checks / 'test_product.py'}"],
        staged,
    )
    assert proc.returncode != 0
    assert _sha(checks / "test_product.py") == before
    cleanup(prepared, run_dir=run)


@SANDBOX
def test_hardlink_cannot_reach_the_read_only_mount(run):
    plan, prepared, writable, staged = _stage_plan(run)
    before = _sha(run / "state.json")
    script = (
        f"ln {run / 'state.json'} {run / 'artifacts' / 'state-link'} 2>/dev/null; "
        f"printf evil >> {run / 'artifacts' / 'state-link'}"
    )
    _sandboxed(run, writable, ["sh", "-c", script], staged)
    # A hard link across the read-only mount would corrupt `state.json`
    # through the writable staging path; the digest is the property.
    assert _sha(run / "state.json") == before


@SANDBOX
def test_factory_cli_cannot_close_a_gate_inside_the_sandbox(run):
    binary = Path(sys.executable).with_name("factory")
    if not binary.exists():
        binary = shutil.which("factory")
    if not binary:
        pytest.skip("factory console script is not installed")
    if str(binary).startswith(("/tmp/", "/private/tmp/")):
        # The sandbox mounts an empty `--tmpfs /tmp`: a venv installed there
        # (the sdist smoke job does exactly that) cannot be executed inside.
        pytest.skip("factory console script lives under /tmp, which bwrap masks")
    plan, prepared, writable, staged = _stage_plan(run)
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
        staged,
    )
    assert proc.returncode != 0
    assert _sha(run / "state.json") == before
    # The command must die on the read-only lock, not on an unrelated parse error.
    assert "blackbox.lock" in (proc.stderr or "")
    cleanup(prepared, run_dir=run)


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
        staged_root = staging_root(run_dir) / "_root"
        assert staged_root in entries
        assert (project / "skills-lock.json").resolve() not in entries
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


@pytest.mark.skipif(not sandbox_available(), reason="bubblewrap is not installed")
def test_sandbox_refuses_a_writable_secret_home_path(tmp_path, monkeypatch):
    """A broad `--sandbox-rw` must not silently undo the home mask."""
    home = tmp_path / "home"
    (home / ".hermes" / "cache").mkdir(parents=True)
    (home / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(home))
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    for path in (home, home / ".ssh", home / ".hermes"):
        with pytest.raises(RuntimeFailure, match="masked secret path"):
            sandbox_command(["true"], run_dir, read_write=(str(path),))

    cmd = sandbox_command(
        ["true"], run_dir, read_write=(str(home / ".hermes" / "cache"),)
    )
    assert str(home / ".hermes" / "cache") in cmd


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
    assert "OPENAI_API_KEY" not in env


@POSIX_ONLY
def test_runtime_never_writes_through_a_symlink(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "runtime").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("safe")
    link = run_dir / "runtime" / "prompt.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    with pytest.raises(JournalError):
        safe_write_text(link, "prompt")
    assert outside.read_text() == "safe"
    assert link.is_symlink()


def test_symlinked_runtime_directory_is_refused(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (run_dir / "runtime").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
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


def _helper_ready() -> bool:
    """Root-helper установлен и отвечает без пароля."""
    from factory.runtime import isolation

    return isolation.helper_installed() and isolation.polkit_allows_helper()


def _run_as_executor(user: str, argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Запустить команду под uid исполнителя через root-helper."""
    from factory.runtime import isolation

    isolation.install_env_file(user, "trust-boundary", {"HOME": str(Path.home())}, Path.cwd())
    try:
        command = isolation.executor_command(
            argv, user=user, run_id="trust-boundary", seq=1
        )
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    finally:
        isolation.remove_env_file("trust-boundary")
        isolation.remove_pid_file("trust-boundary", 1)


EXECUTOR_USER = "factory-executor"


def test_executor_runs_as_different_uid(tmp_path):
    """Исполнитель запускается не под uid владельца репозитория."""
    if not _helper_ready():
        pytest.skip("root-helper is not installed; run `factory setup-executor`")
    if executor_user_uid(EXECUTOR_USER) is None:
        pytest.skip(f"user {EXECUTOR_USER} does not exist; run `factory setup-executor`")
    proc = _run_as_executor(EXECUTOR_USER, ["/usr/bin/id", "-u"])
    assert proc.returncode == 0, proc.stderr
    executor_uid = int(proc.stdout.strip())
    assert executor_uid != os.getuid()
    assert executor_uid == executor_user_uid(EXECUTOR_USER)


def test_executor_cannot_read_user_secret_files(tmp_path):
    """Отдельный uid не читает файлы пользователя с правами 0600."""
    if not _helper_ready():
        pytest.skip("root-helper is not installed; run `factory setup-executor`")
    if executor_user_uid(EXECUTOR_USER) is None:
        pytest.skip(f"user {EXECUTOR_USER} does not exist; run `factory setup-executor`")
    secret = Path.home() / ".factory-trust-boundary-secret"
    secret.write_text("secret-content", encoding="utf-8")
    secret.chmod(0o600)
    try:
        proc = _run_as_executor(EXECUTOR_USER, ["/usr/bin/cat", str(secret)])
        assert proc.returncode != 0, f"executor uid {executor_user_uid(EXECUTOR_USER)} read the user's 0600 file"
        assert "Permission denied" in proc.stderr
    finally:
        secret.unlink(missing_ok=True)


def test_executor_can_run_system_command(tmp_path):
    """Отдельный uid вообще живёт: простая системная команда выполняется."""
    if not _helper_ready():
        pytest.skip("root-helper is not installed; run `factory setup-executor`")
    if executor_user_uid(EXECUTOR_USER) is None:
        pytest.skip(f"user {EXECUTOR_USER} does not exist; run `factory setup-executor`")
    proc = _run_as_executor(EXECUTOR_USER, ["/usr/bin/cat", "/etc/hostname"])
    assert proc.returncode == 0, proc.stderr


def test_executor_is_not_the_operator(tmp_path):
    """Исполнитель не выдаёт себя за оператора: USER и LOGNAME — его."""
    if not _helper_ready():
        pytest.skip("root-helper is not installed; run `factory setup-executor`")
    if executor_user_uid(EXECUTOR_USER) is None:
        pytest.skip(f"user {EXECUTOR_USER} does not exist; run `factory setup-executor`")
    proc = _run_as_executor(EXECUTOR_USER, ["/usr/bin/env"])
    assert proc.returncode == 0, proc.stderr
    env = dict(
        line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
    )
    assert env.get("USER") == EXECUTOR_USER
    assert env.get("LOGNAME") == EXECUTOR_USER
    assert env.get("USER") != os.environ.get("USER")
