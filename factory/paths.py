"""Run-id validation and path resolution.

Every path that contains a user-controlled run id goes through this module.
The rules are deliberately strict: lowercase ASCII letters, digits and
hyphens; 1-63 characters. This makes path traversal impossible and keeps run
directories portable across filesystems.
"""
from __future__ import annotations

import errno
import os
import re
from pathlib import Path

from .errors import JournalError, RunError

RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TASK_ID_RE = re.compile(r"^TASK-[A-Z0-9][A-Z0-9-]{0,62}$")


def _write_all(fd: int, payload: bytes) -> None:
    """Write every byte or fail.

    `write(2)` is allowed to write less than it was given. A short write
    would leave a half-written file, and on a power cut the temp file would
    be incomplete. The atomic rename protects the target, but the temp file
    must be fully written.
    """
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise JournalError("write made no progress")
        view = view[written:]


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


def write_bytes_nofollow(path: Path, data: bytes, *, append: bool = False) -> None:
    """Write bytes without following any symlink in the path.

    Opens the run root with O_DIRECTORY|O_NOFOLLOW once, then walks each
    component using os.open with dir_fd, verifying every directory is a real
    directory.

    For append: opens the leaf with O_NOFOLLOW|O_APPEND and writes.
    For overwrite: creates a temp file in the same directory with O_NOFOLLOW|
    O_CREAT|O_EXCL, writes data, fsyncs, then atomically renames over the
    target using os.rename with dir_fd.

    On any symlink component, raises JournalError without unlinking the target.

    This is the single secure write primitive for all host-side writes.
    """
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        # Windows: best-effort fallback; the real protection is on Linux.
        path.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with path.open("ab") as f:
                f.write(data)
        else:
            # Write to temp then replace
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            with tmp.open("wb") as f:
                f.write(data)
            os.replace(tmp, path)
        return

    # Resolve the run root: the directory containing the path must be within
    # the run directory. We open the run root with O_DIRECTORY|O_NOFOLLOW.
    run_root = _run_root_for_path(path)
    run_root_fd = os.open(run_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Walk each component relative to run_root
        rel_path = path.relative_to(run_root)
        components = rel_path.parts

        # Current directory fd, starting from run_root
        current_fd = run_root_fd

        for i, component in enumerate(components):
            is_last = (i == len(components) - 1)

            if is_last:
                if append:
                    # Append: open existing file with O_NOFOLLOW|O_APPEND
                    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_APPEND
                    try:
                        fd = os.open(component, flags, dir_fd=current_fd)
                    except OSError as exc:
                        if exc.errno == errno.ELOOP:
                            raise JournalError(f"refusing to append through symlink: {path}") from exc
                        if exc.errno == errno.ENOENT:
                            raise JournalError(f"file does not exist for append: {path}") from exc
                        raise JournalError(f"refusing to append {path}: {exc}") from exc
                    try:
                        _write_all(fd, data)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                else:
                    # Overwrite: first check if target is a symlink by trying to open it
                    # with O_NOFOLLOW. If it's a symlink, this fails with ELOOP.
                    try:
                        probe_fd = os.open(component, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current_fd)
                        os.close(probe_fd)
                    except OSError as exc:
                        if exc.errno == errno.ELOOP:
                            raise JournalError(f"refusing to overwrite symlink: {path}") from exc
                        # ENOENT is fine - file doesn't exist yet
                        if exc.errno != errno.ENOENT:
                            raise JournalError(f"refusing to write {path}: {exc}") from exc

                    # Now create temp file with O_NOFOLLOW|O_CREAT|O_EXCL in same dir
                    tmp_name = f".{path.name}.{os.getpid()}.tmp"
                    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL
                    try:
                        tmp_fd = os.open(tmp_name, flags, 0o644, dir_fd=current_fd)
                    except OSError as exc:
                        if exc.errno == errno.ELOOP:
                            raise JournalError(f"refusing to write through symlink: {path}") from exc
                        if exc.errno == errno.EEXIST:
                            raise JournalError(f"temp file already exists (race?): {path}") from exc
                        raise JournalError(f"refusing to write {path}: {exc}") from exc
                    try:
                        _write_all(tmp_fd, data)
                        os.fsync(tmp_fd)
                    finally:
                        os.close(tmp_fd)

                    # Atomic rename in the same directory
                    try:
                        os.rename(tmp_name, path.name, src_dir_fd=current_fd, dst_dir_fd=current_fd)
                    except OSError as exc:
                        raise JournalError(f"refusing to rename {path}: {exc}") from exc
            else:
                # Directory component: open with O_NOFOLLOW|O_DIRECTORY
                try:
                    next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current_fd)
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise JournalError(f"refusing to traverse symlink: {path}") from exc
                    if exc.errno == errno.ENOTDIR:
                        raise JournalError(f"path component is not a directory: {path}") from exc
                    raise JournalError(f"refusing to traverse {path}: {exc}") from exc
                # Close previous fd (except run_root_fd)
                if current_fd != run_root_fd:
                    os.close(current_fd)
                current_fd = next_fd
    finally:
        os.close(run_root_fd)


def _run_root_for_path(path: Path) -> Path:
    """Find the run root directory for a given path.

    Assumes the path is inside a run directory (runs/<run_id>/...).
    """
    resolved = path.resolve()
    for parent in resolved.parents:
        if parent.name == "runs":
            # Parent of 'runs' is the runs root; the run dir is the child
            continue
        if parent.parent.name == "runs":
            return parent
    # Fallback: assume the grandparent is runs root
    return resolved.parent.parent


def safe_write_text(path: Path, text: str) -> None:
    """Write a runtime-owned file without following a symlink.

    Uses write_bytes_nofollow for consistent symlink protection.
    """
    write_bytes_nofollow(path, text.encode("utf-8"), append=False)