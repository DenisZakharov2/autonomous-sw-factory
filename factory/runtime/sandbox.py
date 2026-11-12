"""Run one command inside a bubblewrap sandbox with a read-only root.

The original plan used `systemd-run --user` with `ProtectHome=read-only` and
`ProtectSystem=strict`.  Those properties are not available to user scopes:
`systemd-run --user --scope -p ProtectHome=read-only` answers
"Unknown assignment".  System scopes would run the agent as root, which is a
worse trade.  `bwrap` gives the same guarantees at user level, so it is the
default backend.

The trust boundary is the point of this module.  The whole filesystem is
mounted read-only, and the run directory is opened writable only so that an
atomic writer can do what every editor does: create a temporary file next to
the target and rename it over.  A single-file bind is not enough for that --
`rename` needs write permission on the *directory*, which is why the root
artifact `skills-lock.json` could not be produced by a real executor while
the run root was read-only.  Everything that decides state is then re-mounted
read-only on top, because later mounts win: `state.json`, `blackbox.jsonl`,
`blackbox.lock`, `idea.md`, `AUDIT_TRAIL.md`, `checkpoint.json`, the usage
ledger, `runtime/` and the eval `checks/` directory.  Only `runtime/agent/`
is opened again for the CLI's usage file.  Writing a protected file fails with
`EROFS`; replacing it with a rename fails with `EBUSY` because it is a mount
point.  Network stays available: a model call needs it, and this sandbox is
about the control plane, not exfiltration.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .base import RuntimeFailure

BACKEND = "bwrap"

# Paths inside a run that must never be mounted writable.  `runtime/agent` is
# the one exception under `runtime/`: the Hermes adapter writes its usage file
# there, and its bind is applied after the read-only one, so it wins.
FORBIDDEN_WRITABLE = ("runtime/evidence", "checks")
FORBIDDEN_FILES = ("state.json", "blackbox.jsonl", "blackbox.lock", "idea.md", "runtime/usage.jsonl")
# Re-mounted read-only on top of the writable run root.  `checks/` is here
# because the eval harness copies its product checks into the run: an executor
# that could rewrite them would be grading its own product.
CONTROL_PLANE_FILES = (
    "state.json",
    "blackbox.jsonl",
    "blackbox.lock",
    "idea.md",
    "AUDIT_TRAIL.md",
    "checkpoint.json",
    "runtime/usage.jsonl",
)
CONTROL_PLANE_DIRS = ("runtime/evidence", "checks")
# The directories that hold the verifier's reports.  A producer may write next
# to them, never into them: the report is the witness, and a witness the
# producer wrote is not a witness.
VERIFICATION_DIRS = ("artifacts/verification",)
# What the executor may not keep from the operator's environment.  The
# provider's own key (`*_API_KEY`) stays: without it the agent cannot call
# the model, and a model call is the whole point of the executor.
ENV_DENIED = frozenset(
    {
        "NETRC",
        "SSH_AUTH_SOCK",
        "GPG_AGENT_INFO",
        "KUBECONFIG",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)
ENV_DENIED_PREFIXES = (
    "SSH_",
    "GPG_",
    "GNUPG_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GCP_",
    "DOCKER_",
    "NPM_",
    "PYPI_",
    "STRIPE_",
    "SENTRY_",
    "TWILIO_",
    "CLOUDFLARE_",
)
ENV_DENIED_SUFFIXES = ("_TOKEN", "_PASSWORD", "_SECRET", "_CREDENTIALS")
# The operator's secret directories.  In the default mode the whole
# filesystem is mounted read-only, and read-only is not private: the
# executor runs as the operator and can read everything the operator can.
SECRET_HOME_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".docker",
    ".config/gh",
)
# The `factory/` package itself; binding its repository root would let an
# executor rewrite the code that judges it.
PACKAGE_DIR = Path(__file__).resolve().parents[1]


def sandbox_available(backend: str = BACKEND) -> bool:
    if backend != "bwrap":
        return False
    return shutil.which("bwrap") is not None


def sandbox_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a sandboxed executor.

    The agent inherits provider credentials, because the CLI reads them
    itself, but it has no business knowing where the factory root or runs root
    is: those variables only make an accidental `factory ...` call easier.
    `PYTHONPATH` is dropped for the same reason (module shadowing from the run
    directory).

    Everything that belongs to the *operator* rather than to the model call is
    dropped too: the executor is untrusted code, and a token that leaks into
    it leaks out.  The provider's own key stays — without it the agent cannot
    call the model — and so does the plain shell environment it needs to run.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith("FACTORY_") or key == "PYTHONPATH":
            env.pop(key, None)
            continue
        if key in ENV_DENIED or key.startswith(ENV_DENIED_PREFIXES):
            env.pop(key, None)
            continue
        if key.endswith(ENV_DENIED_SUFFIXES):
            env.pop(key, None)
    return env


def _forbidden(run: Path, path: Path) -> bool:
    resolved_run = run.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_run or resolved_path in resolved_run.parents:
        return True
    package = PACKAGE_DIR.resolve()
    if resolved_path == package or resolved_path in package.parents or package in resolved_path.parents:
        return True
    for name in (*FORBIDDEN_FILES, *CONTROL_PLANE_FILES):
        target = (resolved_run / name).resolve()
        if resolved_path == target or target in resolved_path.parents:
            return True
    for relative in (*FORBIDDEN_WRITABLE, *CONTROL_PLANE_DIRS):
        protected = (resolved_run / relative).resolve()
        if resolved_path == protected or protected in resolved_path.parents or resolved_path in protected.parents:
            return True
    return False


def verification_dirs(run: Path) -> list[Path]:
    """The report directories of this run: the stage reports and the task ones.

    They are protected by mounting them read-only on top of the writable run
    root — but only for an executor that was not given a path inside them.
    The verifier is given exactly that path, and must be able to write.
    """
    found: list[Path] = []
    for relative in VERIFICATION_DIRS:
        candidate = run / relative
        if candidate.is_dir():
            found.append(candidate.resolve())
    tasks = run / "tasks"
    if tasks.is_dir():
        for task in sorted(tasks.iterdir()):
            candidate = task / "verification"
            if candidate.is_dir():
                found.append(candidate.resolve())
    return found


def sandbox_command(
    command: list[str],
    run_dir: Path,
    *,
    read_write: tuple[str, ...] = (),
    enabled: bool = True,
    backend: str = BACKEND,
    executor_user: str | None = None,
    run_id: str = "run",
    seq: int | str = 0,
) -> list[str]:
    """Команда в песочнице; при `executor_user` — ещё и под чужим uid.

    В обычном режиме корень `/` монтируется read-only.  В режиме отдельного
    пользователя профиль сужен до явных привязок (системные каталоги ro,
    каталог прогона rw): `/home` внутри пространства не существует, поэтому
    мирочитаемые файлы домашней директории недоступны даже при traverse-гранте
    на пути к прогону (см. `runtime/isolation.py`).
    """
    if not enabled:
        if executor_user:
            from . import isolation

            return isolation.executor_command(
                list(command), user=executor_user, run_id=run_id, seq=seq
            )
        return list(command)
    if backend != "bwrap":
        raise RuntimeFailure(
            f"unknown sandbox backend {backend!r}; only 'bwrap' is supported "
            "(systemd user scopes cannot apply ProtectHome/ProtectSystem)"
        )
    if not sandbox_available(backend):
        raise RuntimeFailure("sandbox requested but bubblewrap (bwrap) is not installed")

    run = run_dir.resolve()
    args = [
        "bwrap",
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--new-session",
        "--cap-drop",
        "ALL",
    ]
    if executor_user:
        for system_dir in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt"):
            if os.path.isdir(system_dir):
                args.extend(["--ro-bind", system_dir, system_dir])
    else:
        args.extend(["--ro-bind", "/", "/"])
        # Read-only is not private: the executor runs as the operator.  The
        # operator's secret directories are mounted over as empty, and the
        # session sockets (keyring, D-Bus, ssh-agent) are masked — a socket
        # can be connected to through a read-only mount.
        home = Path.home()
        for relative in SECRET_HOME_DIRS:
            candidate = home / relative
            if candidate.is_dir():
                args.extend(["--tmpfs", str(candidate)])
        runtime_dir = Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get(
            "XDG_RUNTIME_DIR"
        ) else None
        if runtime_dir is not None and runtime_dir.is_dir():
            for name in ("bus", "ssh-agent.socket"):
                socket = runtime_dir / name
                if socket.is_socket():
                    args.extend(["--ro-bind", "/dev/null", str(socket)])
            keyring = runtime_dir / "keyring"
            if keyring.is_dir():
                args.extend(["--tmpfs", str(keyring)])
    args.extend(
        [
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            # The run root is writable so an editor can create `<target>.tmp` and
            # rename it into place; the control plane is re-bound read-only below.
            "--bind",
            str(run),
            str(run),
        ]
    )
    for name in CONTROL_PLANE_FILES:
        protected_file = run / name
        if protected_file.is_file():
            args.extend(["--ro-bind", str(protected_file), str(protected_file)])
    for name in CONTROL_PLANE_DIRS:
        protected_dir = run / name
        if protected_dir.is_dir():
            args.extend(["--ro-bind", str(protected_dir), str(protected_dir)])
    args.extend(["--chdir", str(run)])
    seen: set[str] = set()
    bound: list[Path] = []
    bound_dirs: list[Path] = [run]
    for item in read_write:
        path = Path(item).expanduser()
        if path.is_symlink():
            raise RuntimeFailure(f"refusing to expose a symlink to the executor: {path}")
        if not path.exists():
            raise RuntimeFailure(f"sandbox read-write path does not exist: {path}")
        path = path.resolve()
        if _forbidden(run, path):
            raise RuntimeFailure(f"refusing to mount the factory control plane writable: {path}")
        if str(path) in seen:
            continue
        if path.is_file() and any(parent in bound_dirs for parent in path.parents):
            # A file bound on itself becomes a mount point, and an atomic
            # writer that creates `<target>.tmp` and renames it over the target
            # gets EBUSY from rename(2).  The parent directory is already
            # writable, so the file needs no separate bind.
            continue
        seen.add(str(path))
        bound.append(path)
        args.extend(["--bind", str(path), str(path)])
        if path.is_dir():
            bound_dirs.append(path)
    # The verifier's reports are mounted read-only on top of the writable run
    # root: later mounts win.  An executor that was given a path inside one of
    # them is the verifier itself and keeps its write access.
    for protected in verification_dirs(run):
        if any(protected == path or protected in path.parents for path in bound):
            continue
        args.extend(["--ro-bind", str(protected), str(protected)])
    args.append("--")
    args.extend(command)
    if executor_user:
        from . import isolation

        return isolation.executor_command(args, user=executor_user, run_id=run_id, seq=seq)
    return args
