"""Deterministic verification: roles, configuration, schemas, state, journal and artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .artifacts import validate_stage, validate_templates
from .config import PACKAGE_ROOT, load_artifacts, load_json, load_models, load_pipeline
from .errors import ConfigError, FactoryError, RunError
from .journal import verify_chain
from .pipeline import role_ids, validate_pipeline
from .state import current_stage, find_stage, load_state, stage_list

ROLE_SECTIONS = [
    "## 1. Назначение",
    "## 2. Входы",
    "## 3. Процедура",
    "## 4. Выходы",
    "## 5. Критерии готовности",
    "## 6. Право вето",
    "## 7. Запреты",
    "## 8. Эскалация",
]
FRONTMATTER_KEYS = {"role_id", "title", "default_model_tier", "reasoning_effort", "active_stages", "write_scopes", "veto_type", "veto_scope", "blackbox_logging"}
SCOPES = {"code", "own_artifacts", "factory_state"}
EFFORTS = {"low", "medium", "high", "max"}
VETO_TYPES = {"blocking", "advisory"}
MIN_SECTION_WORDS = 3


def parse_role(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ConfigError(f"{path}: missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ConfigError(f"{path}: frontmatter must be delimited by ---")
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid frontmatter YAML: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise ConfigError(f"{path}: frontmatter must be a mapping")
    return frontmatter, parts[2]


def validate_role_card(path: Path, pipeline: dict[str, Any] | None = None, models: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    pipeline = pipeline or load_pipeline()
    models = models or load_models()
    stages = {stage["id"] for stage in pipeline["stages"]}
    tiers = set(models["tiers"])
    try:
        fm, body = parse_role(path)
    except ConfigError as exc:
        return [str(exc)]
    missing = FRONTMATTER_KEYS - set(fm)
    if missing:
        errors.append(f"{path.name}: missing frontmatter keys {sorted(missing)}")
    if fm.get("role_id") != path.stem:
        errors.append(f"{path.name}: role_id {fm.get('role_id')!r} does not match filename")
    if fm.get("default_model_tier") not in tiers:
        errors.append(f"{path.name}: unknown tier {fm.get('default_model_tier')!r}")
    if fm.get("reasoning_effort") not in EFFORTS:
        errors.append(f"{path.name}: invalid reasoning_effort {fm.get('reasoning_effort')!r}")
    scopes = fm.get("write_scopes")
    if not isinstance(scopes, list) or not scopes:
        errors.append(f"{path.name}: write_scopes must be a non-empty list")
    else:
        unknown = sorted(set(scopes) - SCOPES)
        if unknown:
            errors.append(f"{path.name}: unknown write_scopes {unknown}")
    if fm.get("veto_type") not in VETO_TYPES:
        errors.append(f"{path.name}: veto_type must be blocking or advisory")
    if fm.get("blackbox_logging") != "required":
        errors.append(f"{path.name}: blackbox_logging must be 'required'")
    active = fm.get("active_stages")
    if not isinstance(active, list) or not active:
        errors.append(f"{path.name}: active_stages must be a non-empty list")
    else:
        unknown_stages = sorted(set(active) - stages)
        if unknown_stages:
            errors.append(f"{path.name}: unknown active_stages {unknown_stages}")
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    actual = headings[: len(ROLE_SECTIONS)]
    if len(actual) != len(ROLE_SECTIONS) or any(
        not heading.startswith(expected) for heading, expected in zip(actual, ROLE_SECTIONS)
    ):
        errors.append(f"{path.name}: the eight required sections are missing or out of order")
    for section in ROLE_SECTIONS:
        start = body.find(section)
        if start < 0:
            continue
        end = body.find("\n## ", start + len(section))
        chunk = body[start : end if end >= 0 else len(body)]
        words = len([w for w in chunk.replace("`", " ").split() if any(ch.isalpha() for ch in w)])
        if words < MIN_SECTION_WORDS:
            errors.append(f"{path.name}: section {section!r} is too short ({words} words)")
    if "can_edit_project_files" in body:
        errors.append(f"{path.name}: deprecated can_edit_project_files is forbidden")
    return errors


def validate_roles() -> list[str]:
    pipeline = load_pipeline()
    models = load_models()
    errors: list[str] = []
    paths = sorted((PACKAGE_ROOT / "roles").glob("*.md"))
    if len(paths) != 22:
        errors.append(f"expected 22 role cards, found {len(paths)}")
    hashes: dict[str, str] = {}
    for path in paths:
        errors.extend(validate_role_card(path, pipeline, models))
        try:
            _, body = parse_role(path)
        except ConfigError:
            continue
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest in hashes:
            errors.append(f"{path.name}: body duplicates {hashes[digest]}")
        hashes[digest] = path.name
    return errors


def validate_schemas() -> list[str]:
    errors: list[str] = []
    for path in sorted((PACKAGE_ROOT / "schemas").glob("*.json")):
        try:
            schema = load_json(path)
            jsonschema.Draft202012Validator.check_schema(schema)
        except (ConfigError, jsonschema.SchemaError) as exc:
            errors.append(f"{path.name}: invalid JSON Schema: {exc}")
    return errors


def check_state_consistency(run_dir: Path, state: dict[str, Any], pipeline: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if state["run_id"] != run_dir.name:
        errors.append(f"state.run_id {state['run_id']!r} does not match directory {run_dir.name!r}")
    expected_stages = {stage["id"] for stage in pipeline["stages"]}
    if set(state["stage_status"]) != expected_stages:
        errors.append("state.stage_status keys do not match pipeline stages")
    expected_gates = {stage["gate"]["id"] for stage in pipeline["stages"]}
    if set(state["gate_status"]) != expected_gates:
        errors.append("state.gate_status keys do not match pipeline gates")
    stage = current_stage(state, pipeline)
    expected_current = stage["id"] if stage else None
    if state["current_stage"] != expected_current:
        errors.append(f"state.current_stage {state['current_stage']!r} should be {expected_current!r}")
    return errors


def verify_run(run_dir: Path, stage_id: str | None = None, require_gate: bool = False) -> list[str]:
    errors: list[str] = []
    pipeline = load_pipeline()
    registry = load_artifacts()
    errors.extend(validate_pipeline(pipeline, registry))
    errors.extend(validate_roles())
    errors.extend(validate_schemas())
    errors.extend(validate_templates())
    try:
        state = load_state(run_dir)
    except FactoryError as exc:
        return errors + [str(exc)]
    errors.extend(check_state_consistency(run_dir, state, pipeline))
    errors.extend(verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json"))
    if stage_id:
        try:
            stage = find_stage(pipeline, stage_id)
        except RunError as exc:
            return errors + [str(exc)]
        errors.extend(validate_stage(run_dir, stage, registry, state["tasks"]))
        if require_gate:
            gate = state["gate_status"].get(stage["gate"]["id"], {})
            if gate.get("status") != "approved":
                errors.append(f"stage {stage_id}: gate {stage['gate']['id']} is {gate.get('status', 'missing')}")
            if stage_id == "s1_spec" and not state.get("human_spec_approved"):
                errors.append("stage s1_spec: human_spec_approved is false")
            if stage_id == "s_in" and not state.get("restore_drill_passed"):
                errors.append("stage s_in: restore_drill_passed is false")
    else:
        stage = current_stage(state, pipeline)
        if stage:
            errors.extend(validate_stage(run_dir, stage, registry, state["tasks"]))
        else:
            for accepted in stage_list(pipeline):
                if accepted["id"] == "s_in" and state["mode"] != "brownfield":
                    continue
                if state["stage_status"].get(accepted["id"]) == "accepted":
                    errors.extend(validate_stage(run_dir, accepted, registry, state["tasks"]))
    return errors


def doctor_report() -> dict[str, Any]:
    pipeline = load_pipeline()
    registry = load_artifacts()
    roles = role_ids()
    errors = []
    errors.extend(validate_pipeline(pipeline, registry))
    errors.extend(validate_roles())
    errors.extend(validate_schemas())
    errors.extend(validate_templates())
    return {
        "roles": len(roles),
        "templates": len(list((PACKAGE_ROOT / "templates").glob("*.tmpl"))),
        "schemas": len(list((PACKAGE_ROOT / "schemas").glob("*.json"))),
        "stages": len(pipeline["stages"]),
        "modes": sorted(pipeline["modes"]),
        "artifacts": len(registry["artifacts"]),
        "errors": errors,
    }
