"""The runtime contract: an executor turns a work order into files on disk.

The engine decides *what* must exist; an executor decides *how* it appears.
Nothing in this module imports an agent CLI, so the same loop drives a fake
executor in tests and a real one on a workstation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..errors import FactoryError


class RuntimeFailure(FactoryError):
    """The runtime cannot continue without a human or a configuration fix."""

    exit_code = 3


def safe_write_text(path: Path, text: str) -> None:
    """Write a runtime-owned file without following a symlink.

    The executor owns the run directory between steps.  A transcript path or
    `prompt.md` could have been replaced by a symlink to a host file; a plain
    `write_text` would then write outside the sandbox as the invoking user.
    `O_NOFOLLOW` turns that into an error instead of a write.
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


@dataclass(slots=True)
class ExecutionResult:
    """What one executor call produced.

    `artifacts` is informational: the loop never submits what an executor
    claims, only declared artifacts that exist on disk.
    """

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    artifacts: list[str] = field(default_factory=list)
    summary: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    transcript_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(slots=True)
class RunContext:
    """Everything an executor needs that is not the work order itself."""

    run_id: str
    run_dir: Path
    step: int
    order: dict[str, Any]
    mode: str
    prompt: str
    allowed_paths: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    runtime_dir: Path | None = None
    sequence: int = 0
    extra_read_write: tuple[str, ...] = ()

    @property
    def role(self) -> str:
        next_role = self.order.get("next_role")
        if next_role:
            return str(next_role)
        roles = self.order.get("roles") or []
        return str(roles[0]) if roles else ""

    @property
    def stage(self) -> str:
        return str(self.order.get("stage") or "")

    def log_stem(self, suffix: str = "log") -> str:
        role = self.role or "role"
        stage = self.stage or "stage"
        marker = self.sequence or self.step
        return f"ev-{marker:04d}-{stage}-{role}.{suffix}"

    def _safe_base(self) -> Path:
        """Runtime directory, refusing a symlinked or escaping path."""
        base = Path(self.runtime_dir or (self.run_dir / "runtime"))
        run = self.run_dir.resolve()
        if base.is_symlink():
            raise RuntimeFailure(f"runtime directory is a symlink: {base}")
        resolved = base.resolve()
        if resolved != run and run not in resolved.parents:
            raise RuntimeFailure(f"runtime directory escapes the run: {resolved}")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def path_in_runtime(self, suffix: str = "log") -> Path:
        return self._safe_base() / self.log_stem(suffix)

    def path_in_executor(self, name: str = "usage.json") -> Path:
        """A path under `runtime/agent`, the only runtime subtree an adapter
        (and therefore the sandbox) may write to."""
        base = self._safe_base() / "agent"
        if base.is_symlink():
            raise RuntimeFailure(f"executor output directory is a symlink: {base}")
        base.mkdir(parents=True, exist_ok=True)
        return base / name


@runtime_checkable
class Executor(Protocol):
    """A host runtime adapter."""

    name: str

    def execute(self, order: dict[str, Any], context: RunContext) -> ExecutionResult:
        ...
