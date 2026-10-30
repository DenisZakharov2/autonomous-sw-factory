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
FORBIDDEN_WRITABLE = ("runtime", "runtime/evidence")
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
CONTROL_PLANE_DIRS = ("runtime", "checks")
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
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith("FACTORY_") or key == "PYTHONPATH":
            env.pop(key, None)
    return env


def _forbidden(run: Path, path: Path) -> bool:
    if path == run:
        return True
    package = PACKAGE_DIR
    if path == package or path in package.parents or package in path.parents:
        return True
    for name in FORBIDDEN_FILES:
        if path == (run / name).resolve():
            return True
    for relative in FORBIDDEN_WRITABLE:
        protected = (run / relative).resolve()
        if path == protected or path in protected.parents:
            return True
    return False


def sandbox_command(
    command: list[str],
    run_dir: Path,
    *,
    read_write: tuple[str, ...] = (),
    enabled: bool = True,
    backend: str = BACKEND,
) -> list[str]:
    if not enabled:
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
        "--ro-bind",
        "/",
        "/",
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
        args.extend(["--bind", str(path), str(path)])
        if path.is_dir():
            bound_dirs.append(path)
    args.append("--")
    args.extend(command)
    return args
