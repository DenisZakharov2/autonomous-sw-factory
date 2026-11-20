"""Staging between an executor and the run it is allowed to change.

The sandbox no longer mounts the run root writable.  A declared artifact is
replaced by a private copy under `runtime/agent/out`: the executor reads and
writes the familiar path inside the namespace, while the bytes land here.
When the process exits successfully the host checks the copy and only then
moves the declared files into the run.

Only declared destinations are promoted.  A directory artifact is a tree, an
ordinary artifact is one file, and a `per_pattern` artifact is a set of files
matched by its pattern.  `runtime/`, `checks/` and every `verification/` report
are never promoted as part of a tree.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..paths import write_bytes_nofollow
from .base import RuntimeFailure

STAGING_REL = Path("runtime") / "agent" / "out"
ROOT_DIRNAME = "_root"
MAX_FILE_BYTES = 256 * 1024 * 1024
IGNORED_DIR_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
)
IGNORED_FILE_SUFFIXES = (".pyc", ".pyo")


@dataclass(frozen=True, slots=True)
class WritableEntry:
    """One destination the executor may change, in the real run tree."""

    target: Path
    kind: str = "file"
    pattern: str = ""
    root: bool = False


@dataclass(frozen=True, slots=True)
class StagedPath:
    """A writable entry plus the staging copy that stands in for it."""

    target: Path
    staged: Path
    kind: str
    pattern: str = ""
    mount: Path | None = None
    root: bool = False


@dataclass(slots=True)
class PreparedStaging:
    """The bind destinations that were prepared for one sandbox call."""

    binds: list[tuple[Path, Path]] = field(default_factory=list)
    created: list[Path] = field(default_factory=list)
    plan: list[StagedPath] = field(default_factory=list)

    @property
    def mounts(self) -> list[Path]:
        return [destination for destination, _source in self.binds]


def staging_root(run_dir: Path) -> Path:
    # Deliberately not `resolve()`: `out` is written by the host and must not
    # be allowed to become a symlink that redirects the promotion.
    return Path(run_dir).resolve() / STAGING_REL


def _relative(path: Path, parent: Path) -> str:
    try:
        return path.relative_to(parent).as_posix()
    except ValueError as exc:
        raise RuntimeFailure(f"staged path escapes {parent}: {path}") from exc


def reject_symlink_chain(root: Path, candidate: Path) -> None:
    """Refuse a symlink anywhere between root and candidate."""
    try:
        parts = candidate.relative_to(root).parts
    except ValueError as exc:
        raise RuntimeFailure(f"path escapes {root}: {candidate}") from exc
    probe = root
    for part in parts:
        probe = probe / part
        try:
            mode = probe.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode):
            raise RuntimeFailure(f"refusing a symlink in the staging path: {probe}")


def build_plan(
    entries: Iterable[WritableEntry],
    *,
    run_dir: Path,
    project: Path,
    staging: Path,
    allow_project_root: bool = False,
) -> list[StagedPath]:
    """Map every writable destination to its staging copy."""
    run = Path(run_dir).resolve()
    project = Path(project).resolve()
    staging = Path(staging)
    if staging != run and run not in staging.parents:
        raise RuntimeFailure(f"staging directory escapes the run: {staging}")
    plan: list[StagedPath] = []
    seen: set[Path] = set()

    for entry in entries:
        raw = Path(entry.target)
        if entry.root:
            if not allow_project_root:
                raise RuntimeFailure(
                    f"a project-root artifact cannot be staged in a worktree: {raw}"
                )
            reject_symlink_chain(project, raw)
        else:
            reject_symlink_chain(run, raw)
        target = raw.resolve()
        if target in seen:
            continue
        if entry.root:
            if target == project or project not in target.parents:
                raise RuntimeFailure(f"root artifact escapes the project: {target}")
            rel = target.relative_to(project)
            if not rel.parts:
                raise RuntimeFailure("refusing to stage the project root itself")
            plan.append(
                StagedPath(
                    target=target,
                    staged=staging / ROOT_DIRNAME / rel,
                    kind=entry.kind,
                    pattern=entry.pattern,
                    mount=None,
                    root=True,
                )
            )
            seen.add(target)
            continue

        if target == run or run not in target.parents:
            raise RuntimeFailure(f"writable path escapes the run: {target}")
        if target == staging or staging in target.parents:
            raise RuntimeFailure(f"refusing to stage the staging directory: {target}")

        if entry.kind in {"tree", "pattern"}:
            mount = target
            staged = staging / _relative(target, run)
        else:
            mount = target.parent
            if mount == run:
                raise RuntimeFailure(
                    f"refusing to expose the run root writable for {target.name}"
                )
            staged = staging / _relative(mount, run) / target.name

        plan.append(
            StagedPath(
                target=target,
                staged=staged,
                kind=entry.kind,
                pattern=entry.pattern,
                mount=mount,
                root=False,
            )
        )
        seen.add(target)
    return plan


def _unique_mounts(plan: Sequence[StagedPath]) -> list[tuple[Path, Path]]:
    """Return (destination, source) binds, outer directories first."""
    mapping: dict[Path, Path] = {}
    for entry in plan:
        if entry.mount is None:
            continue
        source = entry.staged.parent if entry.kind == "file" else entry.staged
        current = mapping.get(entry.mount)
        if current is not None and current != source:
            raise RuntimeFailure(f"conflicting staging sources for {entry.mount}")
        mapping[entry.mount] = source

    kept: list[Path] = []
    for destination in sorted(mapping, key=lambda item: len(item.parts)):
        if any(parent == destination or parent in destination.parents for parent in kept):
            continue
        kept.append(destination)
    return [(destination, mapping[destination]) for destination in kept]


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy a tree without following or keeping a symlink."""
    root = Path(source)
    if root.is_symlink():
        raise RuntimeFailure(f"refusing to stage a symlinked directory: {root}")
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        rel = current_path.relative_to(root)
        if current_path.is_symlink():
            raise RuntimeFailure(f"refusing to stage a symlinked directory: {current_path}")
        dirs[:] = [name for name in dirs if name not in IGNORED_DIR_NAMES]
        target_dir = Path(destination) / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            if Path(name).suffix in IGNORED_FILE_SUFFIXES:
                continue
            source_file = current_path / name
            info = source_file.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeFailure(f"refusing to stage a symlinked file: {source_file}")
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeFailure(f"refusing to stage a special file: {source_file}")
            shutil.copyfile(source_file, target_dir / name, follow_symlinks=False)


def prepare(plan: Sequence[StagedPath], *, run_dir: Path) -> PreparedStaging:
    """Copy the writable trees into staging and create their bind points."""
    run = Path(run_dir).resolve()
    prepared = PreparedStaging(plan=list(plan))
    staging = staging_root(run)
    if staging.is_symlink():
        raise RuntimeFailure(f"refusing a symlinked staging directory: {staging}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for entry in plan:
        entry.staged.parent.mkdir(parents=True, exist_ok=True)

    for destination, source in _unique_mounts(plan):
        if run != destination and run not in destination.parents:
            raise RuntimeFailure(f"staging destination escapes the run: {destination}")
        reject_symlink_chain(run, destination)
        if destination.is_symlink():
            raise RuntimeFailure(f"refusing to stage a symlink: {destination}")
        if destination.exists() and not destination.is_dir():
            raise RuntimeFailure(f"expected a directory to stage: {destination}")
        if not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
            prepared.created.append(destination)
        source.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            _copy_tree(destination, source)
        prepared.binds.append((destination, source))

    for entry in plan:
        if entry.root:
            entry.staged.parent.mkdir(parents=True, exist_ok=True)
    return prepared


def _assert_regular(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise RuntimeFailure(f"refusing to promote a symlink: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeFailure(f"refusing to promote a special file: {path}")
    if info.st_nlink != 1:
        # A hard link is a hole: `link(state.json, staged/ok.md)` would turn
        # the witness into artifact bytes.  Nothing legitimate needs one.
        raise RuntimeFailure(f"refusing to promote a hard-linked file: {path}")
    if info.st_size > MAX_FILE_BYTES:
        raise RuntimeFailure(
            f"refusing to promote a file larger than {MAX_FILE_BYTES} bytes: {path}"
        )


def _protected_parts(base: Sequence[str], parts: Sequence[str]) -> bool:
    """Whether `parts`, under `base`, is a tree a promotion must never carry."""
    if not parts:
        return True
    if base[:1] and base[0] in {"runtime", "checks", ".git"}:
        return True
    if base[:1] == ("artifacts",) and base[1:2] != ("verification",):
        return parts[0] == "verification"
    if base[:1] == ("tasks",) and len(base) >= 2 and base[2:3] != ("verification",):
        return parts[0] == "verification"
    return False


def _tree_files(root: Path, target_rel: tuple[str, ...]) -> list[tuple[Path, bytes]]:
    """Validated (relative, bytes) for every file under root."""
    if root.is_symlink():
        raise RuntimeFailure(f"refusing to promote a symlinked tree: {root}")
    if not root.is_dir():
        raise RuntimeFailure(f"expected a directory in staging: {root}")
    collected: list[tuple[Path, bytes]] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        if current_path.is_symlink():
            raise RuntimeFailure(f"refusing to promote a symlinked directory: {current_path}")
        keep_dirs: list[str] = []
        for name in dirs:
            child = current_path / name
            if name in IGNORED_DIR_NAMES:
                continue
            if child.is_symlink():
                raise RuntimeFailure(f"refusing to promote a symlinked directory: {child}")
            keep_dirs.append(name)
        dirs[:] = keep_dirs
        for name in files:
            if Path(name).suffix in IGNORED_FILE_SUFFIXES:
                continue
            source = current_path / name
            _assert_regular(source)
            rel = rel_dir / name
            if _protected_parts(target_rel, rel.parts):
                continue
            collected.append((rel, source.read_bytes()))
    return collected


def _ensure_parents(anchor: Path, parent: Path) -> None:
    """Create `parent` below `anchor`, refusing a symlink as it goes."""
    rel = parent.relative_to(anchor)
    probe = anchor
    for part in rel.parts:
        probe = probe / part
        if probe.is_symlink():
            raise RuntimeFailure(f"refusing to write through a symlink: {probe}")
        if probe.exists():
            if not probe.is_dir():
                raise RuntimeFailure(f"not a directory: {probe}")
            continue
        probe.mkdir()


def _write(target: Path, payload: bytes, anchor: Path) -> None:
    _ensure_parents(anchor, target.parent)
    write_bytes_nofollow(target, payload)


def promote(plan: Sequence[StagedPath], *, run_dir: Path, project: Path) -> list[Path]:
    """Check every staged destination, then copy it into the real tree."""
    run = Path(run_dir).resolve()
    project = Path(project).resolve()
    writes: list[tuple[Path, bytes, Path]] = []
    for entry in plan:
        staged = Path(entry.staged)
        target = Path(entry.target)
        anchor = project if entry.root else run
        reject_symlink_chain(run, staged)
        if entry.mount is not None and not staged.exists():
            continue
        if entry.kind == "file":
            if not staged.exists():
                continue
            _assert_regular(staged)
            writes.append((target, staged.read_bytes(), anchor))
        elif entry.kind == "pattern":
            if not staged.is_dir():
                continue
            for source in sorted(staged.iterdir()):
                if not source.is_file() or source.is_symlink():
                    continue
                if not fnmatch.fnmatch(source.name, entry.pattern or "*"):
                    continue
                _assert_regular(source)
                writes.append((target / source.name, source.read_bytes(), anchor))
        else:
            if not staged.exists():
                continue
            if anchor == run:
                target_rel = tuple(target.relative_to(run).parts)
            else:
                target_rel = ()
            for relative, payload in _tree_files(staged, target_rel):
                writes.append((target / relative, payload, anchor))

    promoted: list[Path] = []
    for target, payload, anchor in writes:
        if target.is_symlink():
            raise RuntimeFailure(f"refusing to overwrite a symlink: {target}")
        if target.is_file() and target.read_bytes() == payload:
            continue
        _write(target, payload, anchor)
        promoted.append(target)
    return promoted


def cleanup(prepared: PreparedStaging, *, run_dir: Path) -> None:
    """Remove staging and every empty artifact directory the step created."""
    staging = staging_root(run_dir)
    if staging.is_symlink():
        raise RuntimeFailure(f"refusing to remove a symlinked staging directory: {staging}")
    if staging.exists():
        shutil.rmtree(staging)
    for directory in sorted(prepared.created, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue
