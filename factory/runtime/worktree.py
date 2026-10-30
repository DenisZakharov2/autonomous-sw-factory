"""Parallel `s4_build` tasks in one git worktree per task.

The journal and `state.json` stay single-writer: workers only write files in
their own worktree, and the runtime merges and submits them sequentially after
all workers have finished.  A worktree is a copy of the run's product tree at
a base commit; the control plane (`state.json`, `blackbox.jsonl`, usage,
evidence) is never copied into it.

The merge is deliberately a file copy with a base-hash check, not a `git
merge`.  Tasks are required to have disjoint `Allowed Files`, so a content
conflict means someone wrote outside the declared scope; in that case every
file copied by the batch is restored and the task is not submitted.
"""
from __future__ import annotations

import copy
import hashlib
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import load_artifacts, load_pipeline
from ..engine import _task_work_order, submit_work, task_claim
from ..state import load_state
from .base import ExecutionResult, Executor, RunContext, RuntimeFailure, safe_write_text
from .prompt import build_prompt

# Trees that must never enter a seed repository: the control plane, previous
# worktrees, VCS metadata and generated mirrors.
SKIP_TOP_LEVEL = {
    "runtime",
    ".git",
    ".archive",
    "state.json",
    "blackbox.jsonl",
    "blackbox.lock",
    "idea.md",
    "AUDIT_TRAIL.md",
}
SKIP_ANYWHERE = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeFailure(
            f"git {' '.join(args)} failed in {repo}: {completed.stderr.strip()[-800:]}"
        )
    return completed.stdout


def _is_within(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    root = parent.resolve()
    return resolved == root or root in resolved.parents


def normalise_allowed(path: str) -> str:
    """A run-relative POSIX path, with no escape and no leading `./`."""
    raw = str(path).strip().strip("`").replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw or raw.startswith("/"):
        raise RuntimeFailure(f"Allowed Files entry must be run-relative: {path!r}")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise RuntimeFailure(f"Allowed Files entry escapes the run: {path!r}")
    return "/".join(parts)


def _entry_is_dir(entry: str) -> bool:
    return entry.endswith("/")


def paths_overlap(left: str, right: str) -> bool:
    """True when two Allowed Files entries can touch the same file."""
    a = normalise_allowed(left).rstrip("/")
    b = normalise_allowed(right).rstrip("/")
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def parallel_conflicts(tasks: list[dict[str, Any]]) -> list[str]:
    """Pairs of tasks whose Allowed Files intersect, with the shared path."""
    conflicts: list[str] = []
    for index, left in enumerate(tasks):
        for right in tasks[index + 1 :]:
            for a in left.get("allowed_files") or []:
                for b in right.get("allowed_files") or []:
                    if paths_overlap(str(a), str(b)):
                        conflicts.append(
                            f"{left['task_id']} and {right['task_id']} both declare "
                            f"{normalise_allowed(str(a))}"
                        )
                        break
                else:
                    continue
                break
    return conflicts


def _copy_run_tree(source: Path, destination: Path) -> None:
    """Copy the product tree without the control plane or symlinks."""
    destination.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(source):
        current = Path(root)
        relative = current.relative_to(source)
        if not relative.parts:
            dirs[:] = [name for name in dirs if name not in SKIP_TOP_LEVEL and name not in SKIP_ANYWHERE]
        else:
            dirs[:] = [name for name in dirs if name not in SKIP_ANYWHERE]
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            if relative.parts == () and name in SKIP_TOP_LEVEL:
                continue
            source_file = current / name
            if source_file.is_symlink() or not source_file.is_file():
                continue
            shutil.copy2(source_file, target_dir / name)


def _rebase_order(order: dict[str, Any], run_dir: Path, worktree: Path) -> dict[str, Any]:
    """Point the order's resolved paths at the worktree, keeping display paths."""
    scoped = copy.deepcopy(order)
    for spec in scoped.get("artifacts") or []:
        raw = str(spec.get("resolved_path") or spec.get("path") or "")
        if not raw or spec.get("root"):
            continue
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                relative = candidate.relative_to(run_dir)
            except ValueError:
                # A root artifact is handled by the main runtime, not a worker.
                continue
        else:
            relative = candidate
        spec["resolved_path"] = str(worktree / relative)
    return scoped


def _scope_order_to_task(order: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """Drop stage artifacts owned by other roles.

    The sandbox opens every directory in the order writable.  A task order
    carries the whole stage, so `product/tests/` (owned by the QA role) would
    be writable for a developer task even though it is outside `Allowed
    Files`.  Filtering the order is what keeps the sandbox to one task's scope.
    """
    owner = str(task["owner"])
    scoped = copy.deepcopy(order)
    scoped["artifacts"] = [
        spec
        for spec in order.get("artifacts") or []
        if spec.get("per_task") or str(spec.get("owner") or "") in {"", owner}
    ]
    scoped["pending_artifacts"] = [
        item
        for item in order.get("pending_artifacts") or []
        if str(item.get("owner") or "") in {"", owner}
    ]
    return scoped


@dataclass(slots=True)
class TaskOutcome:
    task_id: str
    ok: bool
    changed: list[str] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    prompt_chars: int = 0
    transcript: str | None = None
    worktree: str | None = None
    role: str | None = None


@dataclass(slots=True)
class ParallelBatch:
    tasks: list[str]
    outcomes: list[TaskOutcome]
    merged: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return any(item.ok for item in self.outcomes)


class WorktreeWorkspace:
    """A seed repository plus one detached worktree per task."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.resolve()
        self.base = self.run_dir / "runtime" / "worktrees"
        self.seed = self.base / "seed"
        self.trees = self.base / "trees"
        self.commit = ""
        self._created: list[Path] = []

    def prepare(self) -> None:
        if shutil.which("git") is None:
            raise RuntimeFailure("parallel worktrees need git, which is not installed")
        self.cleanup()
        self.base.mkdir(parents=True, exist_ok=True)
        self.seed.mkdir(parents=True, exist_ok=True)
        _copy_run_tree(self.run_dir, self.seed)
        _git(self.seed, "init", "-q")
        _git(self.seed, "config", "user.email", "factory@localhost")
        _git(self.seed, "config", "user.name", "factory runtime")
        _git(self.seed, "add", "-A")
        _git(self.seed, "commit", "-q", "--allow-empty", "-m", "factory worktree base")
        self.commit = _git(self.seed, "rev-parse", "HEAD").strip()

    def create(self, task_id: str) -> Path:
        path = self.trees / task_id
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.rmtree(path)
        _git(self.seed, "worktree", "add", "--detach", str(path), self.commit)
        self._created.append(path)
        return path

    def base_bytes(self, relative: str) -> bytes | None:
        completed = subprocess.run(
            ["git", "-C", str(self.seed), "show", f"{self.commit}:{relative}"],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout

    def changes(self, worktree: Path) -> list[str]:
        output = _git(
            worktree,
            "-c",
            "core.quotePath=false",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        changed: list[str] = []
        for line in output.splitlines():
            if not line:
                continue
            raw = line[3:]
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            raw = raw.strip().strip('"')
            if raw:
                changed.append(raw)
        return sorted(dict.fromkeys(changed))

    def merge(self, task: dict[str, Any], worktree: Path, changed: list[str]) -> list[tuple[Path, bytes | None]]:
        """Copy a task's changed files into the run after a base-hash check.

        Returns backups for `rollback`.  Raises before writing anything when a
        target changed since the seed commit: that is a conflict, and running
        with a half-merged batch would leave state that cannot be verified.
        """
        allowed = [normalise_allowed(item) for item in task.get("allowed_files") or []]
        deliverable = f"tasks/{task['task_id']}/deliverable.md"
        for relative in changed:
            normalised = normalise_allowed(relative)
            if normalised != deliverable and not any(
                normalised == entry or (_entry_is_dir(entry) and normalised.startswith(entry))
                for entry in allowed
            ):
                raise RuntimeFailure(
                    f"task {task['task_id']} wrote outside Allowed Files: {normalised}"
                )

        checks: list[tuple[Path, bytes | None, bytes | None]] = []
        for relative in changed:
            normalised = normalise_allowed(relative)
            target = (self.run_dir / normalised).resolve()
            if not _is_within(target, self.run_dir):
                raise RuntimeFailure(f"merge target escapes the run: {target}")
            base = self.base_bytes(normalised)
            current = target.read_bytes() if target.is_file() else None
            if base is None and current is not None:
                raise RuntimeFailure(
                    f"worktree merge conflict: {normalised} appeared in the run after the base commit"
                )
            if base is not None and current != base:
                raise RuntimeFailure(
                    f"worktree merge conflict: {normalised} changed in the run after the base commit"
                )
            checks.append((target, base, current))

        backups: list[tuple[Path, bytes | None]] = []
        for relative, (target, _base, current) in zip(changed, checks):
            source = worktree / normalise_allowed(relative)
            backups.append((target, current))
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            elif target.exists():
                target.unlink()
        return backups

    @staticmethod
    def rollback(backups: list[tuple[Path, bytes | None]]) -> None:
        for target, previous in reversed(backups):
            try:
                if previous is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(previous)
            except OSError:
                continue

    def cleanup(self) -> None:
        if self.seed.is_dir():
            for path in list(self._created):
                subprocess.run(
                    ["git", "-C", str(self.seed), "worktree", "remove", "--force", str(path)],
                    capture_output=True,
                    check=False,
                )
            subprocess.run(
                ["git", "-C", str(self.seed), "worktree", "prune"],
                capture_output=True,
                check=False,
            )
        for path in (self.trees, self.base):
            if path == self.base and self.seed.is_dir():
                # Keep the seed when a caller wants to inspect evidence; the
                # next prepare() removes it.
                shutil.rmtree(path, ignore_errors=True)
                break
            shutil.rmtree(path, ignore_errors=True)
        self._created.clear()


def _writable_paths(
    order: dict[str, Any], worktree: Path, task: dict[str, Any], control_dir: Path
) -> tuple[list[str], list[Path]]:
    from .loop import _cleanup_placeholders, _collect_writable_paths, _prepare_writable_paths

    entries = _collect_writable_paths(order, worktree, task)
    # The loop's helper adds `<workspace>/runtime/agent`; a worktree has no
    # control plane.  The Hermes usage file belongs to the run, not the tree.
    entries.pop((worktree / "runtime" / "agent").resolve(), None)
    writable, placeholders = _prepare_writable_paths(entries)
    agent = control_dir / "runtime" / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    writable.append(str(agent))
    return writable, placeholders


def run_parallel_tasks(
    run_id: str,
    run_dir: Path,
    stage: dict[str, Any],
    tasks: list[dict[str, Any]],
    executor: Executor,
    *,
    root: str | Path | None = None,
    max_workers: int | None = None,
    prompt_limit: int = 26000,
    progress: Callable[[str, str], None] | None = None,
) -> ParallelBatch:
    """Execute independent tasks concurrently; merge and submit sequentially.

    Only `produce` work happens in parallel.  `task_claim`, `submit_work`,
    journal events and `state.json` are written here, on the calling thread,
    after every worker has stopped.
    """
    if len(tasks) < 2:
        raise RuntimeFailure("parallel build needs at least two ready tasks")
    conflicts = parallel_conflicts(tasks)
    if conflicts:
        raise RuntimeFailure("parallel tasks rejected: " + "; ".join(conflicts))

    run_dir = run_dir.resolve()
    pipeline = load_pipeline()
    registry = load_artifacts()
    state = load_state(run_dir)
    workspace = WorktreeWorkspace(run_dir)
    workspace.prepare()
    by_id = {str(task["task_id"]): task for task in tasks}
    worktrees: dict[str, Path] = {}
    outcomes: list[TaskOutcome] = []

    try:
        for task in tasks:
            worktrees[str(task["task_id"])] = workspace.create(str(task["task_id"]))

        def worker(task: dict[str, Any], index: int) -> TaskOutcome:
            from .loop import _cleanup_placeholders

            task_id = str(task["task_id"])
            worktree = worktrees[task_id]
            order = _scope_order_to_task(
                _task_work_order(run_dir, run_id, stage, task, pipeline, registry), task
            )
            scoped = _rebase_order(order, run_dir, worktree)
            prompt = build_prompt(
                order,
                run_dir=run_dir,
                role=str(task["owner"]),
                workspace=worktree,
                max_chars=prompt_limit,
            )
            writable, placeholders = _writable_paths(scoped, worktree, task, run_dir)
            context = RunContext(
                run_id=run_id,
                run_dir=worktree,
                step=index + 1,
                order=scoped,
                mode=str(state["mode"]),
                prompt=prompt,
                allowed_paths=[str(item.get("path")) for item in order.get("pending_artifacts") or []],
                runtime_dir=run_dir / "runtime",
                control_dir=run_dir,
                sequence=int(state["event_count"]) + index,
                extra_read_write=tuple(writable),
                task=task,
            )
            safe_write_text(
                run_dir / "runtime" / f"parallel-{task_id}.prompt.md", prompt
            )
            started = time.monotonic()
            try:
                result = executor.execute(scoped, context)
                error = None if result.ok else (result.stderr or result.stdout or "executor failed")
            except Exception as exc:  # noqa: BLE001 - one worker's crash is not the batch's
                result = ExecutionResult(exit_code=1, stderr=repr(exc))
                error = f"{type(exc).__name__}: {exc}"
            elapsed = time.monotonic() - started
            _cleanup_placeholders(placeholders)
            changed: list[str] = []
            if error is None:
                changed = workspace.changes(worktree)
                if not (worktree / "tasks" / task_id / "deliverable.md").is_file():
                    error = f"task {task_id} did not write tasks/{task_id}/deliverable.md"
                if error is None and not changed:
                    error = f"task {task_id} changed no files"
            if progress is not None:
                progress(task_id, "ok" if error is None else "failed")
            return TaskOutcome(
                task_id=task_id,
                ok=error is None,
                changed=changed,
                error=error,
                usage=dict(result.usage or {}),
                seconds=round(elapsed, 3),
                prompt_chars=len(prompt),
                transcript=result.transcript_path,
                worktree=str(worktree),
                role=str(task.get("owner") or ""),
            )

        workers = max_workers or len(tasks)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, task, index) for index, task in enumerate(tasks)]
            outcomes = [future.result() for future in futures]
    except Exception:
        workspace.cleanup()
        raise

    merged: list[str] = []
    try:
        for outcome in outcomes:
            if not outcome.ok:
                continue
            task = by_id[outcome.task_id]
            worktree = worktrees[outcome.task_id]
            backups = workspace.merge(task, worktree, outcome.changed)
            try:
                current = load_state(run_dir)["tasks"][outcome.task_id]
                if current["status"] == "pending":
                    task_claim(run_id, outcome.task_id, str(task["owner"]), root=root)
                paths = list(dict.fromkeys(outcome.changed))
                submit_work(
                    run_id,
                    stage_id=str(stage["id"]),
                    role=str(task["owner"]),
                    artifacts=paths,
                    task_id=outcome.task_id,
                    root=root,
                    why=f"Runtime merged worktree for {outcome.task_id}",
                )
            except Exception:
                workspace.rollback(backups)
                raise
            merged.extend(outcome.changed)
    finally:
        workspace.cleanup()

    return ParallelBatch(tasks=[str(task["task_id"]) for task in tasks], outcomes=outcomes, merged=merged)
