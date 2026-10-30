"""Run one command inside a bubblewrap sandbox with a read-only root.

The original plan used `systemd-run --user` with `ProtectHome=read-only` and
`ProtectSystem=strict`.  Those properties are not available to user scopes:
`systemd-run --user --scope -p ProtectHome=read-only` answers
"Unknown assignment".  System scopes would run the agent as root, which is a
worse trade.  `bwrap` gives the same guarantees at user level, so it is the
default backend.

The whole filesystem is mounted read-only; the run directory and an explicit
list of extra paths (an agent's own state/config) are re-bound writable.
Network stays available: a model call needs it, and this sandbox is about
filesystem blast radius, not exfiltration.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .base import RuntimeFailure

BACKEND = "bwrap"


def sandbox_available(backend: str = BACKEND) -> bool:
    if backend != "bwrap":
        return False
    return shutil.which("bwrap") is not None


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

    run = str(run_dir.resolve())
    args = [
        "bwrap",
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--unshare-pid",
        "--new-session",
        "--bind",
        run,
        run,
        "--chdir",
        run,
    ]
    for item in read_write:
        path = Path(item).expanduser().resolve()
        if not path.exists():
            raise RuntimeFailure(f"sandbox read-write path does not exist: {path}")
        args.extend(["--bind", str(path), str(path)])
    args.append("--")
    args.extend(command)
    return args
