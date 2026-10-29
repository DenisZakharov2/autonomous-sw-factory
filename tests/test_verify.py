import json
from pathlib import Path

import pytest

from factory.artifacts import (
    extract_req_ids,
    validate_acceptance,
    validate_plan,
    validate_stage,
    validate_traceability,
)
from factory.config import (
    ConfigError,
    load_json,
    validate_instance,
)
from factory.verify import (
    validate_role_card,
    validate_roles,
    validate_schemas,
    validate_templates,
)


def test_roles_are_unique_and_valid():
    assert validate_roles() == []


def test_role_missing_section_is_rejected(tmp_path):
    source = Path("factory/roles/05-business-analyst.md").read_text(encoding="utf-8")
    bad = source.replace("## 5. Критерии готовности (Definition of Done)", "## 5. Done")
    path = tmp_path / "05-business-analyst.md"
    path.write_text(bad, encoding="utf-8")
    errors = validate_role_card(path)
    assert any("eight required sections" in error for error in errors)


def test_role_with_bad_scope_is_rejected(tmp_path):
    source = Path("factory/roles/05-business-analyst.md").read_text(encoding="utf-8")
    bad = source.replace('write_scopes: ["own_artifacts"]', 'write_scopes: ["everything"]')
    path = tmp_path / "05-business-analyst.md"
    path.write_text(bad, encoding="utf-8")
    errors = validate_role_card(path)
    assert any("unknown write_scopes" in error for error in errors)


def test_schemas_are_valid():
    assert validate_schemas() == []


def test_templates_cover_required_sections():
    assert validate_templates() == []


def test_garbage_state_is_rejected():
    schema = load_json(Path("factory/schemas/state.schema.json"))
    garbage = {
        "schema_version": "1.0.0",
        "run_id": 123,
        "mode": None,
        "current_stage": [],
        "stage_status": {},
        "gate_status": {},
        "tasks": {},
        "veto_counters": {},
        "human_spec_approved": "yes",
        "restore_drill_passed": 1,
        "last_event_hash": "nope",
        "event_count": -1,
        "created_at": "x",
        "updated_at": "y",
    }
    with pytest.raises(ConfigError):
        validate_instance(garbage, schema, "state")


def test_garbage_event_is_rejected():
    schema = load_json(Path("factory/schemas/event.schema.json"))
    garbage = {"event_id": 1, "run_id": None, "stage": 7, "verdict": "PASS"}
    with pytest.raises(ConfigError):
        validate_instance(garbage, schema, "event")


def test_skills_lock_rejects_entry_without_hash():
    schema = load_json(Path("factory/schemas/skills-lock.schema.json"))
    with pytest.raises(ConfigError):
        validate_instance({"schema_version": "1.0.0", "skills": [{"name": "x"}]}, schema, "lock")


def test_pipeline_without_stages_is_rejected():
    schema = load_json(Path("factory/schemas/pipeline.schema.json"))
    with pytest.raises(ConfigError):
        validate_instance({"schema_version": "1.0.0", "name": "x", "modes": {}}, schema, "pipeline")


def _write_spec(run_dir: Path, reqs=("REQ-001", "REQ-002")):
    rows = "\n".join(f"| `{req}` | requirement | `Must-Have` | `source` |" for req in reqs)
    (run_dir / "artifacts" / "spec.md").write_text(
        "# Spec\n## 1. Требования\n| ID | Requirement | Priority | Source |\n| --- | --- | --- | --- |\n"
        + rows
        + "\n## 2. Приёмка\n| ID | Given | When | Then |\n| --- | --- | --- | --- |\n",
        encoding="utf-8",
    )
    (run_dir / "artifacts" / "spec-human.md").write_text(
        "# Human\n## 1. Что делает система\n"
        + " ".join(reqs)
        + "\n## 2. Как проверяем\n| ID | Что | Как |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _write_trace(run_dir: Path, reqs=("REQ-001", "REQ-002")):
    data = {
        "schema_version": "1.0.0",
        "requirements": {
            req: {"spec": f"artifacts/spec.md#{req}", "tasks": ["TASK-001"], "tests": ["tests/test_x.py::test"], "acceptance": "A-1"}
            for req in reqs
        },
    }
    (run_dir / "artifacts" / "traceability.json").write_text(json.dumps(data), encoding="utf-8")


def test_req_parity_and_traceability(nano_run):
    run_dir, _, _ = nano_run
    _write_spec(run_dir)
    _write_trace(run_dir)
    assert validate_traceability(run_dir) == []


def test_req_parity_failure(nano_run):
    run_dir, _, _ = nano_run
    _write_spec(run_dir, ("REQ-001",))
    (run_dir / "artifacts" / "spec-human.md").write_text(
        "# Human\n## 1. Что делает система\nREQ-002\n## 2. Как проверяем\n",
        encoding="utf-8",
    )
    _write_trace(run_dir, ("REQ-001",))
    errors = validate_traceability(run_dir)
    assert any("parity" in error for error in errors)


def test_plan_must_cover_every_req(nano_run):
    run_dir, _, _ = nano_run
    _write_spec(run_dir)
    (run_dir / "artifacts" / "plan.md").write_text(
        "# Plan\n## 1. Задачи\n| TASK-001 | 12 | REQ-001 |\n## 2. Покрытие REQ\nREQ-001\n",
        encoding="utf-8",
    )
    errors = validate_plan(run_dir)
    assert any("REQ-002" in error for error in errors)


def test_acceptance_fail_is_rejected(nano_run):
    run_dir, _, _ = nano_run
    _write_spec(run_dir, ("REQ-001",))
    (run_dir / "artifacts" / "acceptance.md").write_text(
        "# Acceptance\n## 1. Матрица приёмки\n| REQ-001 | criterion | evidence | FAIL |\n## 2. Дефекты\n",
        encoding="utf-8",
    )
    errors = validate_acceptance(run_dir)
    assert any("FAIL" in error for error in errors)


def test_stage_validation_catches_missing_artifact(nano_run):
    from factory.config import load_artifacts
    from factory.state import find_stage

    run_dir, _, _ = nano_run
    stage = find_stage(__import__("factory.config", fromlist=["load_pipeline"]).load_pipeline(), "s00_intake")
    errors = validate_stage(run_dir, stage, load_artifacts(), {})
    assert any("missing artifact" in error for error in errors)


def test_extract_req_ids_ignores_placeholders():
    assert extract_req_ids("REQ-001 and REQ-### and REQ-NFR-002") == {"REQ-001", "REQ-NFR-002"}
