"""Run-id validation and path resolution.

Every path that contains a user-controlled run id goes through this module.
The rules are deliberately strict: lowercase ASCII letters, digits and
hyphens; 1-63 characters. This makes path traversal impossible and keeps run
directories portable across filesystems.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import RunError

RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TASK_ID_RE = re.compile(r"^TASK-[A-Z0-9][A-Z0-9-]{0,62}$")


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise RunError(
            "run_id must match ^[a-z0-9][a-z0-9-]{0,62}$ "
            "(lowercase letters, digits, hyphen; cannot contain '/', '..' or spaces)"
        )
    return run_id


def validate_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise RunError("task_id must match ^TASK-[A-Z0-9][A-Z0-9-]{0,62}$")
    return task_id


def runs_root(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    env = os.environ.get("FACTORY_RUNS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "runs").resolve()


def run_dir(run_id: str, root: str | Path | None = None) -> Path:
    validate_run_id(run_id)
    base = runs_root(root)
    candidate = (base / run_id).resolve()
    if candidate.parent != base:
        raise RunError(f"refusing path outside runs root: {candidate}")
    return candidate


def ensure_within(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    parent = parent.resolve()
    if resolved != parent and parent not in resolved.parents:
        raise RunError(f"refusing path outside {parent}: {resolved}")
    return resolved


def safe_write_text(path: Path, text: str) -> None:
    """Write a runtime-owned file without following a symlink.

    The executor owns the run directory between steps.  A transcript path or
    `prompt.md` could have been replaced by a symlink to a host file; a plain
    `write_text` would then write outside the sandbox as the invoking user.
    `O_NOFOLLOW` turns that into an error instead of a write.

    A symlink that is already there is removed first: the runtime owns this
    path, and the file behind it is not the runtime's to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
