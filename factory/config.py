"""Configuration loading and validation."""
from __future__ import annotations

import copy
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml
from jsonschema import exceptions as jsonschema_exceptions
from jsonschema.validators import validator_for

from .errors import ConfigError

PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = PACKAGE_ROOT / "schemas"

# `jsonschema.validate` re-validates the schema itself on every call, which
# costs ~60 ms per validation.  A gate runs dozens of validations against the
# same handful of package schemas, so the result is cached by content.  Only
# successful checks are cached; a broken schema still raises every time.
_CHECKED_SCHEMAS: "OrderedDict[str, bool]" = OrderedDict()
_CHECKED_SCHEMAS_LIMIT = 128


def check_schema_cached(cls: Any, schema: dict[str, Any]) -> None:
    key = json.dumps(schema, sort_keys=True, ensure_ascii=False, default=str)
    if key in _CHECKED_SCHEMAS:
        return
    cls.check_schema(schema)
    _CHECKED_SCHEMAS[key] = True
    while len(_CHECKED_SCHEMAS) > _CHECKED_SCHEMAS_LIMIT:
        _CHECKED_SCHEMAS.popitem(last=False)


def project_root() -> Path:
    """Root for repository-level files such as skills-lock.json."""
    env = os.environ.get("FACTORY_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    candidate = PACKAGE_ROOT.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    # Папка репозитория определяется расположением пакета factory, а не текущей
    # директорией: иначе тест, сменивший CWD через monkeypatch.chdir, подсунет
    # doctor'у чужой tmp_path с левым VERSION/LICENSE.
    return candidate


def _file_key(path: Path) -> tuple[str, int, int]:
    """The identity of a file's content: its path, mtime and size."""
    try:
        stat = path.stat()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    return (str(path), stat.st_mtime_ns, stat.st_size)


# The router asks for the pipeline and the artifact registry on every order,
# and each call used to re-parse the YAML and re-validate it against the
# schema.  On a full run that was hundreds of parses — most of the suite's
# runtime, and the reason a loaded CI runner could cross the job timeout.
# The cache is keyed by the file's identity, so a changed file is read again;
# only successful loads are cached.
_YAML_CACHE: "OrderedDict[tuple[str, int, int], dict[str, Any]]" = OrderedDict()
_JSON_CACHE: "OrderedDict[tuple[str, int, int], Any]" = OrderedDict()
_CONFIG_CACHE_LIMIT = 32


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    key = _file_key(path)
    cached = _YAML_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping")
    _YAML_CACHE[key] = copy.deepcopy(data)
    while len(_YAML_CACHE) > _CONFIG_CACHE_LIMIT:
        _YAML_CACHE.popitem(last=False)
    return data


def load_json(path: Path) -> Any:
    key = _file_key(path)
    cached = _JSON_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    _JSON_CACHE[key] = copy.deepcopy(data)
    while len(_JSON_CACHE) > _CONFIG_CACHE_LIMIT:
        _JSON_CACHE.popitem(last=False)
    return data


def validate_instance(instance: Any, schema: dict[str, Any], label: str) -> None:
    cls = validator_for(schema)
    check_schema_cached(cls, schema)
    validator = cls(schema)
    error = jsonschema_exceptions.best_match(validator.iter_errors(instance))
    if error is not None:
        where = ".".join(str(p) for p in error.absolute_path) or "<root>"
        raise ConfigError(f"{label} is invalid at {where}: {error.message}") from error


def validate_against_schema(instance: Any, schema_name: str, label: str) -> None:
    schema = load_json(SCHEMA_DIR / schema_name)
    validate_instance(instance, schema, label)


# The validated pipeline and the validated artifact registry, keyed by the
# file's identity and the mode.
_PIPELINE_CACHE: "OrderedDict[tuple[str, int, int], dict[str, Any]]" = OrderedDict()
_ARTIFACTS_CACHE: "OrderedDict[tuple[tuple[str, int, int], str | None], dict[str, Any]]" = (
    OrderedDict()
)


def load_pipeline() -> dict[str, Any]:
    path = PACKAGE_ROOT / "pipeline.yaml"
    key = _file_key(path)
    cached = _PIPELINE_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)
    data = _read_yaml(path)
    validate_against_schema(data, "pipeline.schema.json", "factory/pipeline.yaml")
    _PIPELINE_CACHE[key] = copy.deepcopy(data)
    while len(_PIPELINE_CACHE) > _CONFIG_CACHE_LIMIT:
        _PIPELINE_CACHE.popitem(last=False)
    return data


def load_artifacts(mode: str | None = None) -> dict[str, Any]:
    path = PACKAGE_ROOT / "artifacts.yaml"
    cache_key = (_file_key(path), mode)
    cached = _ARTIFACTS_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    data = _read_yaml(path)
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ConfigError("factory/artifacts.yaml must define a non-empty artifacts map")
    if mode is not None:
        # Brownfield starts from an existing tree, so the greenfield
        # `product/src` convention does not describe it.  A mode may remap a
        # few artifact paths; everything else stays registry-wide.
        modes = load_pipeline().get("modes", {})
        if mode not in modes:
            raise ConfigError(f"unknown mode: {mode!r}")
        overrides = modes[mode].get("artifact_paths") or {}
        for artifact_id, path in overrides.items():
            if artifact_id not in artifacts:
                raise ConfigError(
                    f"mode {mode!r} remaps unknown artifact {artifact_id!r}"
                )
            if not isinstance(path, str) or not path.strip():
                raise ConfigError(
                    f"mode {mode!r} artifact {artifact_id!r} has an empty path"
                )
            artifacts[artifact_id] = {**artifacts[artifact_id], "path": path}
    seen_paths: dict[str, str] = {}
    for artifact_id, spec in artifacts.items():
        if not isinstance(spec, dict) or "path" not in spec or "owner" not in spec:
            raise ConfigError(f"artifact {artifact_id!r} must define path and owner")
        artifact_path = str(spec["path"])
        normalized = artifact_path.replace("{task_id}", "*").replace("{num}", "*")
        if normalized in seen_paths:
            raise ConfigError(
                f"artifact path collision: {artifact_id} and "
                f"{seen_paths[normalized]} both use {artifact_path}"
            )
        seen_paths[normalized] = artifact_id
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
    _ARTIFACTS_CACHE[cache_key] = copy.deepcopy(data)
    while len(_ARTIFACTS_CACHE) > _CONFIG_CACHE_LIMIT:
        _ARTIFACTS_CACHE.popitem(last=False)
    return data


def load_models() -> dict[str, Any]:
    data = _read_yaml(PACKAGE_ROOT / "models.yaml")
    tiers = data.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise ConfigError("factory/models.yaml must define tiers")
    return data


def load_budget() -> dict[str, Any]:
    """Attempt limits are enforced by the engine; token/minute/usd budgets by the runtime."""
    data = _read_yaml(PACKAGE_ROOT / "budget.yaml")
    attempts = data.get("default_max_attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise ConfigError("budget.yaml: default_max_attempts must be a positive integer")
    veto = data.get("max_veto_retries")
    if not isinstance(veto, int) or isinstance(veto, bool) or veto < 1:
        raise ConfigError("budget.yaml: max_veto_retries must be a positive integer")
    if data.get("on_attempts_exhausted", "escalate") not in {"escalate", "pause"}:
        raise ConfigError("budget.yaml: on_attempts_exhausted must be escalate or pause")
    enforce = data.get("enforce", {})
    if not isinstance(enforce, dict):
        raise ConfigError("budget.yaml: enforce must be a mapping")
    _validate_verify_config(data)
    return data


# A shell in the verification allowlist would take the argv the host built and
# hand its meaning to the command line inside it: the host stops parsing, the
# shell starts.  The same for `env`, which runs its arguments.
VERIFY_DENIED_PROGRAMS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "env", "xargs", "su", "sudo", "doas"}
)
VERIFY_OUTPUT_MODES = frozenset({"strict", "ignore"})


def _validate_verify_config(data: dict[str, Any]) -> None:
    """The operator's allowlist is configuration, so it gets the same strictness.

    A malformed entry is not "just ignored": it would silently widen or narrow
    the boundary the gate depends on.  `None` and an absent key mean the same
    thing here — host replay is not configured.
    """
    commands = data.get("verify_commands", [])
    if commands is None:
        commands = []
    if not isinstance(commands, list):
        raise ConfigError("budget.yaml: verify_commands must be a list of argv lists")
    for entry in commands:
        if not isinstance(entry, list) or not entry:
            raise ConfigError(
                "budget.yaml: every verify_commands entry must be a non-empty argv list"
            )
        if not all(isinstance(token, str) and token and "\x00" not in token for token in entry):
            raise ConfigError(
                "budget.yaml: verify_commands entries must be non-empty strings without NULs"
            )
        program = Path(entry[0]).name
        if program in VERIFY_DENIED_PROGRAMS:
            raise ConfigError(
                f"budget.yaml: verify_commands may not run {program!r}; "
                "a shell inside the allowlist would interpret the argv the host built"
            )
    timeout = data.get("verify_timeout_s", 600)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < timeout <= 7200
    ):
        raise ConfigError("budget.yaml: verify_timeout_s must be a number in (0, 7200]")
    limit = data.get("verify_max_commands", 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 64:
        raise ConfigError("budget.yaml: verify_max_commands must be an integer in 1..64")
    require = data.get("verify_require_for_pass", True)
    if not isinstance(require, bool):
        raise ConfigError("budget.yaml: verify_require_for_pass must be true or false")
    mode = data.get("verify_output", "strict")
    if mode not in VERIFY_OUTPUT_MODES:
        raise ConfigError("budget.yaml: verify_output must be strict or ignore")


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
