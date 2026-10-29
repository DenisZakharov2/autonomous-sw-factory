"""Artifact registry helpers and deterministic Markdown/JSON checks."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import load_artifacts, load_json, project_root, validate_instance
from .errors import ConfigError

REQ_RE = re.compile(r"\bREQ-[A-Z0-9][A-Z0-9-]*\b")
PLACEHOLDER_RE = re.compile(r"REQ-(?:###|NNN|\{)")


def extract_req_ids(text: str) -> set[str]:
    return {match for match in REQ_RE.findall(text) if not PLACEHOLDER_RE.match(match)}


def artifact_path(run_dir: Path, spec: dict[str, Any], *, task_id: str | None = None) -> Path:
    path = str(spec["path"])
    if spec.get("root"):
        return (project_root() / path).resolve()
    if "{task_id}" in path:
        if not task_id:
            raise ConfigError(f"artifact {spec.get('id')} needs a task_id")
        path = path.replace("{task_id}", task_id)
    return (run_dir / path).resolve()


def pattern_paths(run_dir: Path, spec: dict[str, Any]) -> list[Path]:
    pattern = str(spec["path"])
    if "{num}" in pattern:
        base = run_dir / pattern.replace("{num}", "*")
        return sorted(base.parent.glob(base.name))
    return [artifact_path(run_dir, spec)]


def check_sections(text: str, required: list[str]) -> list[str]:
    return [section for section in required if section not in text]


def validate_markdown(path: Path, required_sections: list[str]) -> list[str]:
    if not path.exists():
        return [f"missing artifact: {path}"]
    if not path.is_file():
        return [f"artifact is not a file: {path}"]
    text = path.read_text(encoding="utf-8")
    errors = [f"{path}: missing section {section!r}" for section in check_sections(text, required_sections)]
    if not text.strip():
        errors.append(f"{path}: artifact is empty")
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
    return errors


def validate_plan(run_dir: Path) -> list[str]:
    plan = run_dir / "artifacts" / "plan.md"
    spec = run_dir / "artifacts" / "spec.md"
    if not plan.exists():
        return ["plan.md is missing"]
    if not spec.exists():
        return ["spec.md is missing"]
    reqs = extract_req_ids(spec.read_text(encoding="utf-8"))
    text = plan.read_text(encoding="utf-8")
    missing = sorted(req for req in reqs if req not in text)
    return [f"plan.md does not cover {req}" for req in missing]


def validate_acceptance(run_dir: Path) -> list[str]:
    acceptance = run_dir / "artifacts" / "acceptance.md"
    spec = run_dir / "artifacts" / "spec.md"
    if not acceptance.exists():
        return ["acceptance.md is missing"]
    reqs = extract_req_ids(spec.read_text(encoding="utf-8")) if spec.exists() else set()
    text = acceptance.read_text(encoding="utf-8")
    errors = [f"acceptance.md does not cover {req}" for req in sorted(reqs) if req not in text]
    if re.search(r"\|\s*`?FAIL`?\s*\|", text, re.IGNORECASE):
        errors.append("acceptance.md contains a FAIL row")
    return errors


def validate_stage(run_dir: Path, stage: dict[str, Any], registry: dict[str, Any], tasks: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    tasks = tasks or {}
    for artifact_id in stage["artifacts"]:
        spec = dict(registry["artifacts"][artifact_id])
        spec["id"] = artifact_id
        if spec.get("directory"):
            path = artifact_path(run_dir, spec)
            if not path.exists() or not path.is_dir():
                errors.append(f"missing directory: {path}")
            elif artifact_id in {"product_src", "product_tests"} and not any(path.rglob("*")):
                errors.append(f"directory is empty: {path}")
            continue
        if spec.get("per_task"):
            owners = [task for task in tasks.values() if task.get("stage") == stage["id"]]
            if not owners:
                errors.append(f"stage {stage['id']} has no tasks; cannot validate {artifact_id}")
            for task in owners:
                path = artifact_path(run_dir, spec, task_id=task["task_id"])
                errors.extend(validate_markdown(path, list(spec.get("required_sections", []))))
            continue
        if spec.get("per_pattern"):
            paths = pattern_paths(run_dir, spec)
            if not paths:
                errors.append(f"missing artifact pattern: {spec['path']}")
            for path in paths:
                errors.extend(validate_markdown(path, list(spec.get("required_sections", []))))
            continue
        path = artifact_path(run_dir, spec)
        if spec.get("schema"):
            errors.extend(validate_json_artifact(path, Path(str(spec["schema"])).name))
        if spec.get("required_sections"):
            errors.extend(validate_markdown(path, list(spec["required_sections"])))
        elif not spec.get("schema") and not spec.get("directory") and not path.exists():
            errors.append(f"missing artifact: {path}")
    if stage["id"] == "s1_spec":
        errors.extend(validate_traceability(run_dir))
    if stage["id"] == "s3_plan":
        errors.extend(validate_plan(run_dir))
    if stage["id"] == "s5_acceptance":
        errors.extend(validate_acceptance(run_dir))
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
