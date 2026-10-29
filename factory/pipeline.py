"""Pipeline loading, mode resolution, validation and work orders."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import (
    PACKAGE_ROOT,
    load_artifacts,
    load_pipeline,
    mode_roles,
    project_root,
)
from .errors import ConfigError

ROLE_DIR = PACKAGE_ROOT / "roles"


def role_ids() -> list[str]:
    return sorted(path.stem for path in ROLE_DIR.glob("*.md"))


def stage_map(pipeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {stage["id"]: stage for stage in pipeline["stages"]}


def artifact_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: value for key, value in registry["artifacts"].items()}


def effective_roles(pipeline: dict[str, Any], mode: str, stage: dict[str, Any]) -> list[str]:
    mode_cfg = pipeline["modes"][mode]
    roles = mode_roles(pipeline, mode)
    overrides = mode_cfg.get("stage_roles", {})
    if stage["id"] in overrides:
        selected = list(overrides[stage["id"]])
    else:
        selected = [role for role in stage["roles"] if role in roles]
    unknown = [role for role in selected if role not in roles]
    if unknown:
        raise ConfigError(f"mode {mode} stage {stage['id']} uses roles outside the mode: {unknown}")
    return selected


def effective_owner(pipeline: dict[str, Any], mode: str, artifact_id: str, default: str) -> str:
    overrides = pipeline["modes"][mode].get("artifact_owners", {})
    return str(overrides.get(artifact_id, default))


def resolve_path(run_dir: Path, artifact: dict[str, Any], *, task_id: str | None = None) -> Path:
    path = str(artifact["path"])
    if artifact.get("root"):
        return (project_root() / path).resolve()
    if "{task_id}" in path:
        if not task_id:
            raise ConfigError(f"artifact {artifact.get('id', path)} needs a task_id")
        path = path.replace("{task_id}", task_id)
    return (run_dir / path).resolve()


def stage_artifacts(
    pipeline: dict[str, Any],
    registry: dict[str, Any],
    mode: str,
    stage: dict[str, Any],
    *,
    task_id: str | None = None,
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    result = []
    for artifact_id in stage["artifacts"]:
        if artifact_id not in registry["artifacts"]:
            raise ConfigError(f"stage {stage['id']} references unknown artifact {artifact_id}")
        spec = dict(registry["artifacts"][artifact_id])
        spec["id"] = artifact_id
        spec["owner"] = effective_owner(pipeline, mode, artifact_id, str(spec.get("owner", "")))
        if spec.get("per_task") and not task_id:
            continue
        if spec.get("per_task"):
            spec["resolved_path"] = str(resolve_path(run_dir or Path("."), spec, task_id=task_id))
        elif spec.get("root"):
            spec["resolved_path"] = str(resolve_path(run_dir or Path("."), spec))
        else:
            spec["resolved_path"] = str(spec["path"])
        result.append(spec)
    return result


def validate_pipeline(pipeline: dict[str, Any] | None = None, registry: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    pipeline = pipeline or load_pipeline()
    registry = registry or load_artifacts()
    roles = set(role_ids())
    if len(roles) != 22:
        errors.append(f"expected 22 role files, found {len(roles)}")
    stages = pipeline["stages"]
    ids = [stage["id"] for stage in stages]
    if len(ids) != len(set(ids)):
        errors.append("stage ids must be unique")
    orders = [stage["order"] for stage in stages]
    if orders != sorted(orders) or len(orders) != len(set(orders)):
        errors.append("stage order must be unique and increasing")
    by_id = {stage["id"]: stage for stage in stages}
    gate_ids: set[str] = set()
    for stage in stages:
        if stage["rework_to"] not in by_id:
            errors.append(f"{stage['id']}: rework_to points to unknown stage {stage['rework_to']}")
        for entry in stage["entry"]:
            if not entry.endswith(".exit"):
                errors.append(f"{stage['id']}: entry {entry!r} must look like '<stage>.exit'")
                continue
            source = entry[: -len(".exit")]
            if source not in by_id:
                errors.append(f"{stage['id']}: entry points to unknown stage {source}")
        for role in stage["roles"]:
            if role not in roles:
                errors.append(f"{stage['id']}: unknown role {role}")
        for artifact_id in stage["artifacts"]:
            if artifact_id not in registry["artifacts"]:
                errors.append(f"{stage['id']}: unknown artifact {artifact_id}")
        gate = stage["gate"]
        if gate["id"] in gate_ids:
            errors.append(f"{stage['id']}: duplicate gate id {gate['id']}")
        gate_ids.add(gate["id"])
        if gate["approver"] != "human" and gate["approver"] not in roles:
            errors.append(f"{stage['id']}: gate approver {gate['approver']} is not a role")
        if not stage["exit"]:
            errors.append(f"{stage['id']}: exit criteria are empty")
    for stage in stages:
        for artifact_id in stage["artifacts"]:
            spec = registry["artifacts"][artifact_id]
            owner = str(spec.get("owner", ""))
            if owner.startswith("{") and owner.endswith("}"):
                continue
            if owner and owner not in roles:
                errors.append(f"artifact {artifact_id}: unknown owner {owner}")
    for mode, cfg in pipeline["modes"].items():
        try:
            mode_set = set(mode_roles(pipeline, mode))
        except ConfigError as exc:
            errors.append(str(exc))
            continue
        unknown = sorted(mode_set - roles)
        if unknown:
            errors.append(f"mode {mode}: unknown roles {unknown}")
        for stage_id, selected in cfg.get("stage_roles", {}).items():
            if stage_id not in by_id:
                errors.append(f"mode {mode}: stage_roles references unknown stage {stage_id}")
                continue
            unknown = sorted(set(selected) - mode_set)
            if unknown:
                errors.append(f"mode {mode} stage {stage_id}: roles outside mode {unknown}")
        for artifact_id, owner in cfg.get("artifact_owners", {}).items():
            if artifact_id not in registry["artifacts"]:
                errors.append(f"mode {mode}: artifact_owners references unknown artifact {artifact_id}")
            if owner not in mode_set:
                errors.append(f"mode {mode}: artifact owner {owner} is not active")
        for stage in stages:
            try:
                active = effective_roles(pipeline, mode, stage)
            except ConfigError as exc:
                errors.append(str(exc))
                continue
            if not active:
                errors.append(f"mode {mode}: stage {stage['id']} has no active role")
                continue
            for artifact_id in stage["artifacts"]:
                spec = registry["artifacts"][artifact_id]
                owner = effective_owner(pipeline, mode, artifact_id, str(spec.get("owner", "")))
                if owner.startswith("{"):
                    continue
                if owner not in active:
                    errors.append(
                        f"mode {mode}: stage {stage['id']} artifact {artifact_id} owner {owner} is not in the active roles {active}"
                    )
    return errors
