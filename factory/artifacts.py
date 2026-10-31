"""Artifact registry helpers and deterministic Markdown/JSON checks."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from .config import load_artifacts, load_json, project_root, validate_instance
from .errors import ConfigError, FactoryError, RunError

REQ_RE = re.compile(r"\bREQ-[A-Z0-9][A-Z0-9-]*\b")
PLACEHOLDER_RE = re.compile(r"\{\{[a-zA-Z0-9_.-]+\}\}")
PLACEHOLDER_GUARD = re.compile(r"REQ-(?:###|NNN|\{)")
MIN_SECTION_WORDS = 1


def extract_req_ids(text: str) -> set[str]:
    return {match for match in REQ_RE.findall(text) if not PLACEHOLDER_GUARD.match(match)}


def _within(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    root = parent.resolve()
    return resolved == root or root in resolved.parents


def _reject_symlink(path: Path) -> None:
    """A path is symlink-free when its resolved form equals its absolute form."""
    if path.is_symlink() or path.resolve() != Path(os.path.abspath(path)):
        raise RunError(f"symlink artifacts are forbidden: {path}")


def artifact_path(run_dir: Path, spec: dict[str, Any], *, task_id: str | None = None) -> Path:
    path = str(spec["path"])
    if spec.get("root"):
        resolved = (project_root() / path).resolve()
        if not _within(resolved, project_root()):
            raise RunError(f"root artifact escapes project root: {resolved}")
        return resolved
    if "{task_id}" in path:
        if not task_id:
            raise ConfigError(f"artifact {spec.get('id')} needs a task_id")
        path = path.replace("{task_id}", task_id)
    resolved = (run_dir / path).resolve()
    if not _within(resolved, run_dir):
        raise RunError(f"artifact escapes the run directory: {resolved}")
    return resolved


def resolve_recorded_path(
    run_dir: Path, recorded: str, root_paths: Iterable[str] = ()
) -> Path:
    """Resolve a path stored in the journal.

    A declared root artifact (`root: true`) always resolves against the project
    root, even if a stray copy exists inside the run: otherwise an agent could
    make the journal vouch for the wrong file.
    """
    raw = recorded[:-1] if recorded.endswith("/") else recorded
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    normalised = raw.replace("\\", "/")
    roots = {str(item).replace("\\", "/") for item in root_paths}
    if normalised in roots:
        return (project_root() / normalised).resolve()
    local = (run_dir / candidate).resolve()
    if local.exists():
        return local
    root = (project_root() / candidate).resolve()
    if _within(root, project_root()) and root.exists():
        return root
    return local


def pattern_paths(run_dir: Path, spec: dict[str, Any]) -> list[Path]:
    pattern = str(spec["path"])
    if "{num}" in pattern:
        base = run_dir / pattern.replace("{num}", "*")
        return sorted(base.parent.glob(base.name))
    return [artifact_path(run_dir, spec)]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_dir(path: Path) -> str:
    """Deterministic directory hash: sorted relative paths plus file hashes."""
    digest = hashlib.sha256()
    files = sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink() and "__pycache__" not in item.parts
    )
    if not files:
        raise ConfigError(f"directory artifact is empty: {path}")
    for item in files:
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(item).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def collect_artifact_entries(
    run_dir: Path,
    items: Iterable[str],
    *,
    root_paths: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Resolve submitted paths, hash files and directories, and return event entries.

    Root artifacts (declared with `root: true`) resolve against the project
    root; everything else must stay inside the run directory.  Symlinks are
    refused so a submission cannot escape its sandbox.
    """
    roots = {str(item).replace("\\", "/") for item in root_paths}
    entries: list[dict[str, Any]] = []
    for item in items:
        raw = str(item)
        raw_path = Path(raw)
        if not raw_path.is_absolute():
            raw_path = run_dir / raw
        _reject_symlink(raw_path)

        candidate = raw_path.resolve()
        is_root = False
        normalised = raw.replace("\\", "/")
        if not Path(raw).is_absolute() and normalised in roots:
            # The declaration wins over a run-local copy of a root artifact.
            candidate = (project_root() / normalised).resolve()
            is_root = True
            _reject_symlink(candidate)
        if Path(raw).is_absolute() and not _within(candidate, run_dir):
            for root in roots:
                if candidate == (project_root() / root).resolve():
                    is_root = True
                    break
        if not is_root and not _within(candidate, run_dir):
            raise RunError(f"refusing path outside {run_dir}: {candidate}")
        if is_root and not _within(candidate, project_root()):
            raise RunError(f"refusing path outside {project_root()}: {candidate}")
        if not candidate.exists():
            raise RunError(f"artifact does not exist: {candidate}")
        if candidate.is_dir():
            for child in candidate.rglob("*"):
                if child.is_symlink():
                    raise RunError(f"symlink artifacts are forbidden: {child}")
            relative = (
                candidate.relative_to(run_dir).as_posix()
                if _within(candidate, run_dir)
                else candidate.name
            )
            entries.append(
                {
                    "path": relative + "/",
                    "sha256": sha256_dir(candidate),
                    "bytes": sum(
                        child.stat().st_size
                        for child in candidate.rglob("*")
                        if child.is_file() and not child.is_symlink()
                    ),
                }
            )
            continue
        relative = (
            candidate.relative_to(run_dir).as_posix()
            if _within(candidate, run_dir)
            else candidate.relative_to(project_root()).as_posix()
        )
        entries.append(
            {"path": relative, "sha256": _sha256_file(candidate), "bytes": candidate.stat().st_size}
        )
    return entries


def check_sections(text: str, required: list[str]) -> list[str]:
    """A required section must be a whole heading line outside fenced code."""
    visible = _visible_text(text)
    missing = []
    for section in required:
        pattern = re.compile(rf"^{re.escape(section)}\s*$", re.MULTILINE)
        if not pattern.search(visible):
            missing.append(section)
    return missing


def _visible_text(text: str) -> str:
    lines: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            lines.append("")
            continue
        lines.append("" if fenced else line)
    return "\n".join(lines)


def _section_body(text: str, section: str) -> str:
    lines = text.splitlines()
    start = None
    fenced = False
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.strip() == section:
            start = index + 1
            continue
        if start is not None and not fenced and line.startswith("## "):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:]) if start is not None else ""


def validate_markdown(path: Path, required_sections: list[str]) -> list[str]:
    if not path.exists():
        return [f"missing artifact: {path}"]
    if not path.is_file():
        return [f"artifact is not a file: {path}"]
    text = path.read_text(encoding="utf-8")
    errors = [f"{path}: missing section {section!r}" for section in check_sections(text, required_sections)]
    if not text.strip():
        errors.append(f"{path}: artifact is empty")
    placeholders = sorted(set(PLACEHOLDER_RE.findall(text)))
    if placeholders:
        errors.append(f"{path}: unresolved template placeholders {placeholders}")
    for section in required_sections:
        chunk = _section_body(text, section)
        words = [word for word in chunk.replace("`", " ").split() if any(ch.isalnum() for ch in word)]
        if len(words) < MIN_SECTION_WORDS:
            errors.append(f"{path}: section {section!r} has no content")
    return errors


def validate_json_artifact(path: Path, schema_name: str) -> list[str]:
    if not path.exists():
        return [f"missing artifact: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]
    try:
        schema = load_json(Path(__file__).resolve().parent / "schemas" / schema_name)
        validate_instance(data, schema, str(path))
    except ConfigError as exc:
        return [str(exc)]
    return []


def _row_has_req(row: list[str], req: str) -> bool:
    token = re.compile(rf"(?<![A-Z0-9-]){re.escape(req)}(?![A-Z0-9-])")
    return any(token.search(cell) for cell in row)


def _table_rows(text: str, section: str) -> list[list[str]]:
    """Parse pipe-table rows of one section, skipping header and separator."""
    rows: list[list[str]] = []
    for line in _section_body(text, section).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip().strip("`").strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def validate_traceability(run_dir: Path) -> list[str]:
    errors: list[str] = []
    spec = run_dir / "artifacts" / "spec.md"
    human = run_dir / "artifacts" / "spec-human.md"
    trace = run_dir / "artifacts" / "traceability.json"
    if not spec.exists() or not human.exists():
        return ["spec.md and spec-human.md are required for traceability"]
    spec_reqs = extract_req_ids(spec.read_text(encoding="utf-8"))
    human_reqs = extract_req_ids(human.read_text(encoding="utf-8"))
    if not spec_reqs:
        errors.append("spec.md contains no REQ-* identifiers")
    if spec_reqs != human_reqs:
        errors.append(f"REQ parity failed: spec={sorted(spec_reqs)} human={sorted(human_reqs)}")
    if not trace.exists():
        errors.append("traceability.json is missing")
        return errors
    try:
        data = json.loads(trace.read_text(encoding="utf-8"))
        schema = load_json(Path(__file__).resolve().parent / "schemas" / "traceability.schema.json")
        validate_instance(data, schema, str(trace))
    except (json.JSONDecodeError, ConfigError) as exc:
        errors.append(str(exc))
        return errors
    keys = set(data.get("requirements", {}))
    if keys != spec_reqs:
        errors.append(f"traceability keys do not match spec REQ set: {sorted(keys ^ spec_reqs)}")
    errors.extend(_trace_link_errors(run_dir, data.get("requirements", {})))
    return errors


def _trace_link_errors(run_dir: Path, requirements: dict[str, Any]) -> list[str]:
    """Check that traceability links point at things that actually exist.

    Links are enforced only once the referenced sources exist: on s1_spec the
    plan and product tests do not exist yet, and that is legitimate.
    """
    errors: list[str] = []
    plan = run_dir / "artifacts" / "plan.md"
    plan_text = plan.read_text(encoding="utf-8") if plan.exists() else ""
    state_path = run_dir / "state.json"
    state_tasks: set[str] = set()
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state_tasks = set((state.get("tasks") or {}))
        except json.JSONDecodeError:
            state_tasks = set()
    acceptance = run_dir / "artifacts" / "acceptance.md"
    acceptance_text = acceptance.read_text(encoding="utf-8") if acceptance.exists() else ""
    tests_root = run_dir / "product" / "tests"
    tests_exist = tests_root.exists() and any(tests_root.rglob("*.py"))
    acceptance_rows = _table_rows(acceptance_text, "## 1. Матрица приёмки") if acceptance_text else []
    for req, item in requirements.items():
        for task in item.get("tasks", []):
            if (plan_text or state_tasks) and task not in state_tasks and task not in plan_text:
                errors.append(f"traceability {req}: task {task} does not exist")
        if tests_exist:
            for reference in item.get("tests", []):
                file_part = str(reference).split("::", 1)[0]
                if not file_part:
                    continue
                candidate = (run_dir / file_part).resolve()
                if not _within(candidate, run_dir):
                    errors.append(f"traceability {req}: test path escapes the run: {file_part}")
                elif not candidate.exists():
                    errors.append(f"traceability {req}: test file does not exist: {file_part}")
        if acceptance_rows:
            covered = any(
                _row_has_req(row, req)
                and re.search(r"\bPASS\b", row[-1] if row else "", re.IGNORECASE)
                for row in acceptance_rows
            )
            if not covered:
                errors.append(f"traceability {req}: acceptance has no PASS row")
    return errors


def validate_plan(run_dir: Path) -> list[str]:
    plan = run_dir / "artifacts" / "plan.md"
    spec = run_dir / "artifacts" / "spec.md"
    if not plan.exists():
        return ["plan.md is missing"]
    if not spec.exists():
        return ["spec.md is missing"]
    reqs = extract_req_ids(spec.read_text(encoding="utf-8"))
    covered = extract_req_ids(plan.read_text(encoding="utf-8"))
    errors = [f"plan.md does not cover {req}" for req in sorted(reqs - covered)]
    trace = run_dir / "artifacts" / "traceability.json"
    if trace.exists():
        # The traceability matrix is written before the plan exists, so its
        # task ids are a guess.  Once the plan names the real tasks, a link to
        # a task that the plan never defines is a defect the last gate would
        # otherwise discover only after the whole build was paid for.
        try:
            data = json.loads(trace.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"traceability.json is not valid JSON: {exc}")
        else:
            errors.extend(_trace_link_errors(run_dir, data.get("requirements", {})))
    return errors


def validate_acceptance(run_dir: Path) -> list[str]:
    acceptance = run_dir / "artifacts" / "acceptance.md"
    spec = run_dir / "artifacts" / "spec.md"
    if not acceptance.exists():
        return ["acceptance.md is missing"]
    reqs = extract_req_ids(spec.read_text(encoding="utf-8")) if spec.exists() else set()
    text = acceptance.read_text(encoding="utf-8")
    errors: list[str] = []
    if re.search(r"\[ТРЕБУЕТ ПРОВЕРКИ\]", text):
        errors.append("acceptance.md contains a [ТРЕБУЕТ ПРОВЕРКИ] marker")
    rows = _table_rows(text, "## 1. Матрица приёмки")
    for row in rows:
        status = row[-1] if row else ""
        if re.search(r"\b(FAIL|PENDING|BLOCKED)\b", status, re.IGNORECASE):
            errors.append(
                f"acceptance.md row {row[0]!r} has status {status!r}; only PASS is accepted"
            )
    for req in sorted(reqs):
        covered = any(
            _row_has_req(row, req)
            and re.search(r"\bPASS\b", row[-1] if row else "", re.IGNORECASE)
            for row in rows
        )
        if not covered:
            errors.append(f"acceptance.md lacks an explicit PASS row for {req}")
    for row in _table_rows(text, "## 2. Дефекты"):
        severity = next(
            (cell for cell in row if cell.lower() in {"critical", "high", "medium", "low"}), ""
        )
        status = row[-1] if row else ""
        if severity.lower() in {"critical", "high"} and status.lower() not in {
            "closed", "resolved", "fixed",
        }:
            errors.append(
                f"acceptance.md has an open {severity} defect {row[0]!r} (status={status!r})"
            )
    return errors


def validate_artifact(
    run_dir: Path, spec: dict[str, Any], tasks: dict[str, Any] | None = None
) -> list[str]:
    """Validate one declared artifact spec, independently of its stage.

    This is what lets a work order say *which* artifact is still missing on a
    multi-role stage, instead of only "the stage is not valid yet".
    """
    errors: list[str] = []
    tasks = tasks or {}
    if spec.get("directory"):
        path = artifact_path(run_dir, spec)
        relative = path.relative_to(run_dir).as_posix() if _within(path, run_dir) else path.name
        if not path.exists() or not path.is_dir():
            errors.append(f"missing directory: {relative}")
        elif not any(item.is_file() for item in path.rglob("*")):
            errors.append(f"directory is empty: {relative}")
        return errors
    if spec.get("per_task"):
        owners = [task for task in tasks.values() if task.get("stage") == spec.get("stage")]
        if not owners:
            owners = [task for task in tasks.values() if task.get("task_id") == spec.get("task_id")]
        if not owners:
            errors.append(f"no task to validate {spec.get('id', spec.get('path'))} against")
        for task in owners:
            path = artifact_path(run_dir, spec, task_id=task["task_id"])
            errors.extend(validate_markdown(path, list(spec.get("required_sections", []))))
        return errors
    if spec.get("per_pattern"):
        paths = pattern_paths(run_dir, spec)
        if not paths:
            errors.append(f"missing artifact pattern: {spec['path']}")
        for path in paths:
            errors.extend(validate_markdown(path, list(spec.get("required_sections", []))))
        return errors
    path = artifact_path(run_dir, spec)
    if spec.get("schema"):
        errors.extend(validate_json_artifact(path, Path(str(spec["schema"])).name))
    if spec.get("required_sections"):
        errors.extend(validate_markdown(path, list(spec["required_sections"])))
    elif not spec.get("schema") and not spec.get("directory") and not path.exists():
        errors.append(f"missing artifact: {path}")
    return errors


def validate_stage(run_dir: Path, stage: dict[str, Any], registry: dict[str, Any], tasks: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    tasks = tasks or {}
    for artifact_id in stage["artifacts"]:
        spec = dict(registry["artifacts"][artifact_id])
        spec["id"] = artifact_id
        if spec.get("per_task"):
            spec["stage"] = stage["id"]
        errors.extend(validate_artifact(run_dir, spec, tasks))
    if stage["id"] == "s1_spec":
        errors.extend(validate_traceability(run_dir))
    if stage["id"] == "s3_plan":
        errors.extend(validate_plan(run_dir))
    if stage["id"] == "s5_acceptance":
        errors.extend(validate_acceptance(run_dir))
    if stage["id"] == "s2_architecture":
        contract = artifact_path(run_dir, {"path": "artifacts/api-contract.md"})
        if contract.exists() and re.search(r"\b(TODO|FIXME)\b", contract.read_text(encoding="utf-8")):
            errors.append(f"{contract}: api contract still has TODO/FIXME markers")
    if stage["id"] == "s15_quarantine":
        from .quarantine import verify_lock

        lock_spec = dict(registry["artifacts"]["skills_lock"])
        lock_spec["id"] = "skills_lock"
        try:
            errors.extend(verify_lock(artifact_path(run_dir, lock_spec)))
        except FactoryError as exc:
            errors.append(str(exc))
    if stage["id"] == "s4_build":
        for task in tasks.values():
            if task.get("stage") == "s4_build" and task.get("status") != "accepted":
                errors.append(
                    f"task {task['task_id']} is not accepted (status={task.get('status')})"
                )
    return errors


def validate_templates() -> list[str]:
    errors: list[str] = []
    registry = load_artifacts()["artifacts"]
    for artifact_id, spec in registry.items():
        if not spec.get("template"):
            continue
        template = Path(__file__).resolve().parent / str(spec["template"])
        if not template.exists():
            errors.append(f"{artifact_id}: missing template {template}")
            continue
        if spec.get("required_sections") and template.suffix == ".tmpl":
            text = template.read_text(encoding="utf-8")
            missing = check_sections(text, list(spec["required_sections"]))
            if missing:
                errors.append(f"{artifact_id}: template misses sections {missing}")
    return errors
