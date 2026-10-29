"""Configuration loading and validation."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .errors import ConfigError

PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = PACKAGE_ROOT / "schemas"


def project_root() -> Path:
    """Root for repository-level files such as skills-lock.json."""
    env = os.environ.get("FACTORY_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    candidate = PACKAGE_ROOT.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd().resolve()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping")
    return data


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc


def validate_instance(instance: Any, schema: dict[str, Any], label: str) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ConfigError(f"{label} is invalid at {where}: {exc.message}") from exc


def validate_against_schema(instance: Any, schema_name: str, label: str) -> None:
    schema = load_json(SCHEMA_DIR / schema_name)
    validate_instance(instance, schema, label)


def load_pipeline() -> dict[str, Any]:
    data = _read_yaml(PACKAGE_ROOT / "pipeline.yaml")
    validate_against_schema(data, "pipeline.schema.json", "factory/pipeline.yaml")
    return data


def load_artifacts() -> dict[str, Any]:
    data = _read_yaml(PACKAGE_ROOT / "artifacts.yaml")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ConfigError("factory/artifacts.yaml must define a non-empty artifacts map")
    seen_paths: dict[str, str] = {}
    for artifact_id, spec in artifacts.items():
        if not isinstance(spec, dict) or "path" not in spec or "owner" not in spec:
            raise ConfigError(f"artifact {artifact_id!r} must define path and owner")
        path = str(spec["path"])
        key = path.replace("{task_id}", "*").replace("{num}", "*")
        if key in seen_paths:
            raise ConfigError(f"artifact path collision: {artifact_id} and {seen_paths[key]} both use {path}")
        seen_paths[key] = artifact_id
        if spec.get("template"):
            template = PACKAGE_ROOT / str(spec["template"])
            if not template.exists():
                raise ConfigError(f"artifact {artifact_id}: missing template {template}")
        if spec.get("schema"):
            schema = PACKAGE_ROOT / str(spec["schema"])
            if not schema.exists():
                raise ConfigError(f"artifact {artifact_id}: missing schema {schema}")
        if not spec.get("directory") and not spec.get("schema") and not spec.get("required_sections"):
            raise ConfigError(f"artifact {artifact_id}: needs schema, required_sections or directory=true")
    return data


def load_models() -> dict[str, Any]:
    data = _read_yaml(PACKAGE_ROOT / "models.yaml")
    tiers = data.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise ConfigError("factory/models.yaml must define tiers")
    return data


def load_budget() -> dict[str, Any]:
    return _read_yaml(PACKAGE_ROOT / "budget.yaml")


def resolve_model(tier: str) -> dict[str, str]:
    models = load_models()
    tiers = models["tiers"]
    if tier not in tiers:
        raise ConfigError(f"unknown model tier: {tier}")
    spec = tiers[tier]
    model_id = os.environ.get(str(spec.get("model_env", "")), spec.get("model", "runtime-default"))
    return {"tier": tier, "model_id": str(model_id), "effort": str(spec.get("effort", "medium"))}


def stage_map(pipeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {stage["id"]: stage for stage in pipeline["stages"]}


def mode_roles(pipeline: dict[str, Any], mode: str) -> list[str]:
    modes = pipeline["modes"]
    if mode not in modes:
        raise ConfigError(f"unknown mode: {mode}")
    roles = modes[mode]["roles"]
    if roles == ["*"]:
        role_dir = PACKAGE_ROOT / "roles"
        return sorted(p.stem for p in role_dir.glob("*.md"))
    return list(roles)
