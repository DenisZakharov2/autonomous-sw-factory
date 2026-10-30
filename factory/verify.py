"""Deterministic verification: roles, configuration, schemas, state, journal and artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .artifacts import resolve_recorded_path, sha256_dir, validate_stage, validate_templates
from .config import (
    PACKAGE_ROOT,
    SCHEMA_DIR,
    load_artifacts,
    load_json,
    load_models,
    load_pipeline,
)
from .errors import ConfigError, FactoryError, RunError
from .journal import read_events, sha256_file, verify_chain
from .pipeline import effective_roles, role_ids, validate_pipeline
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
    cards: dict[str, set[str]] = {}
    for path in paths:
        errors.extend(validate_role_card(path, pipeline, models))
        try:
            frontmatter, body = parse_role(path)
        except ConfigError:
            continue
        cards[str(frontmatter.get("role_id"))] = set(frontmatter.get("active_stages", []))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest in hashes:
            errors.append(f"{path.name}: body duplicates {hashes[digest]}")
        hashes[digest] = path.name
    # Bidirectional sync: pipeline and role cards must agree, per mode.
    used: dict[str, set[str]] = {stage["id"]: set() for stage in pipeline["stages"]}
    for mode in pipeline["modes"]:
        for stage in pipeline["stages"]:
            for role in effective_roles(pipeline, mode, stage):
                used[stage["id"]].add(role)
                active = cards.get(role)
                if active is not None and stage["id"] not in active:
                    errors.append(
                        f"{role}: assigned to {stage['id']} in mode {mode} "
                        "but missing from active_stages"
                    )
    for role, active in cards.items():
        if role == "00-orchestrator":
            continue
        for stage_id in sorted(active):
            if role not in used.get(stage_id, set()):
                errors.append(
                    f"{role}: declares {stage_id} in active_stages but no mode uses the role there"
                )
    return errors


PARITY_KEYS = ("type", "additionalProperties", "required", "properties")


def schema_parity_errors(
    state_schema: dict[str, Any], name: str, other: dict[str, Any], key: str
) -> list[str]:
    """A standalone schema must describe exactly the record the state stores."""
    expected = (state_schema.get("$defs") or {}).get(key)
    if expected is None:
        return [f"state.schema.json is missing $defs.{key}"]
    if {field: expected.get(field) for field in PARITY_KEYS} != {
        field: other.get(field) for field in PARITY_KEYS
    }:
        return [f"{name} has drifted from state.schema.json $defs.{key}"]
    return []


def validate_schemas() -> list[str]:
    errors: list[str] = []
    for path in sorted((PACKAGE_ROOT / "schemas").glob("*.json")):
        try:
            schema = load_json(path)
            jsonschema.Draft202012Validator.check_schema(schema)
        except (ConfigError, jsonschema.SchemaError) as exc:
            errors.append(f"{path.name}: invalid JSON Schema: {exc}")
    try:
        state_schema = load_json(SCHEMA_DIR / "state.schema.json")
        for name, key in (("task.schema.json", "task"), ("gate.schema.json", "gate")):
            errors.extend(
                schema_parity_errors(state_schema, name, load_json(SCHEMA_DIR / name), key)
            )
    except ConfigError as exc:
        errors.append(str(exc))
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
    for item in pipeline["stages"]:
        status = state["stage_status"].get(item["id"])
        gate = state["gate_status"].get(item["gate"]["id"], {})
        if gate.get("status") == "approved" and status != "accepted":
            errors.append(f"stage {item['id']}: gate approved but stage is {status!r}")
        if status == "accepted" and gate.get("status") != "approved":
            errors.append(
                f"stage {item['id']}: accepted while gate is {gate.get('status', 'missing')!r}"
            )
    return errors


def _check_recorded_artifacts(
    run_dir: Path, entries: list[dict[str, Any]], label: str
) -> list[str]:
    errors: list[str] = []
    root_paths = [
        str(spec["path"]) for spec in load_artifacts()["artifacts"].values() if spec.get("root")
    ]
    for entry in entries:
        recorded = str(entry.get("path", ""))
        expected = str(entry.get("sha256", ""))
        target = resolve_recorded_path(run_dir, recorded, root_paths=root_paths)
        if not target.exists():
            errors.append(f"{label}: artifact is missing: {recorded}")
            continue
        try:
            actual = sha256_dir(target) if recorded.endswith("/") else sha256_file(target)
        except (OSError, ConfigError) as exc:
            errors.append(f"{label}: cannot hash {recorded}: {exc}")
            continue
        if actual != expected:
            errors.append(f"{label}: artifact tampered after submission/approval: {recorded}")
    return errors


def artifact_integrity_errors(
    run_dir: Path,
    state: dict[str, Any],
    events: list[dict[str, Any]],
    stage_id: str | None = None,
) -> list[str]:
    """Re-hash every recorded artifact and compare it with the journal.

    The journal is the source of truth: `GATE_DECISION` and `TASK_DONE` carry
    the hashes of what was approved, `ARTIFACT_SUBMIT` the hashes of what was
    submitted.  This is what turns the flight recorder from a log into a
    tamper-evident artifact store.
    """
    approved: dict[str, list[dict[str, Any]]] = {}
    submitted: dict[str, dict[str, dict[str, Any]]] = {}
    task_done: dict[str, list[dict[str, Any]]] = {}
    task_submitted: dict[str, dict[str, dict[str, Any]]] = {}
    for event in events:
        artifacts = event.get("artifacts") or []
        if not isinstance(artifacts, list) or not artifacts:
            continue
        verdict = (event.get("verdict") or {}).get("status")
        stage = str(event.get("stage") or "")
        task = event.get("task_id")
        if event.get("event_type") == "GATE_DECISION" and verdict in {"PASS", "ACCEPTED_RISK"} and not task:
            approved[stage] = artifacts
        elif event.get("event_type") == "TASK_DONE" and task:
            task_done[str(task)] = artifacts
        elif event.get("event_type") == "ARTIFACT_SUBMIT" and task:
            task_submitted.setdefault(str(task), {}).update(
                {str(item.get("path")): item for item in artifacts}
            )
        elif event.get("event_type") == "ARTIFACT_SUBMIT" and stage:
            submitted.setdefault(stage, {}).update(
                {str(item.get("path")): item for item in artifacts}
            )

    errors: list[str] = []
    for stage, status in state["stage_status"].items():
        if stage_id and stage != stage_id:
            continue
        if status == "accepted":
            entries = approved.get(stage) or list(submitted.get(stage, {}).values())
            if entries:
                errors.extend(_check_recorded_artifacts(run_dir, entries, f"stage {stage}"))
        elif status == "review":
            entries = list(submitted.get(stage, {}).values())
            if entries:
                errors.extend(_check_recorded_artifacts(run_dir, entries, f"stage {stage}"))
    for task_id, task in state["tasks"].items():
        if stage_id and task.get("stage") != stage_id:
            continue
        if task.get("status") == "accepted":
            entries = task_done.get(task_id) or list(task_submitted.get(task_id, {}).values())
        elif task.get("status") == "review":
            entries = list(task_submitted.get(task_id, {}).values())
        else:
            entries = []
        if entries:
            errors.extend(_check_recorded_artifacts(run_dir, entries, f"task {task_id}"))
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
    try:
        events = read_events(run_dir / "blackbox.jsonl")
    except FactoryError as exc:
        events = []
        errors.append(str(exc))
    errors.extend(artifact_integrity_errors(run_dir, state, events, stage_id))
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
