"""Run one command inside a bubblewrap sandbox with a read-only root.

The original plan used `systemd-run --user` with `ProtectHome=read-only` and
`ProtectSystem=strict`.  Those properties are not available to user scopes:
`systemd-run --user --scope -p ProtectHome=read-only` answers
"Unknown assignment".  System scopes would run the agent as root, which is a
worse trade.  `bwrap` gives the same guarantees at user level, so it is the
default backend.

The trust boundary is the point of this module.  The whole filesystem, the
run directory included, is mounted read-only; only paths computed by the
runtime from the work order are re-bound writable.  `state.json`,
`blackbox.jsonl`, `blackbox.lock`, `idea.md`, the usage ledger and the
evidence directory therefore cannot be written by the executor even when it
runs `factory gate approve` itself: the command reaches a read-only inode and
fails.  Network stays available: a model call needs it, and this sandbox is
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
# there.
FORBIDDEN_WRITABLE = ("runtime", "runtime/evidence")
FORBIDDEN_FILES = ("state.json", "blackbox.jsonl", "blackbox.lock", "idea.md", "runtime/usage.jsonl")
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
        "--ro-bind",
        str(run),
        str(run),
        "--chdir",
        str(run),
    ]
    seen: set[str] = set()
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
        seen.add(str(path))
        args.extend(["--bind", str(path), str(path)])
    args.append("--")
    args.extend(command)
    return args
