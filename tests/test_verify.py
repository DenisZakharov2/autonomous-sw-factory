import json
import subprocess
from pathlib import Path

import pytest

from factory.artifacts import (
    check_sections,
    collect_artifact_entries,
    extract_req_ids,
    resolve_recorded_path,
    validate_acceptance,
    validate_plan,
    validate_stage,
    validate_traceability,
)
from factory.journal import sha256_file
from factory.config import (
    ConfigError,
    load_budget,
    load_json,
    validate_instance,
)
from factory.verify import (
    schema_parity_errors,
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


def test_acceptance_pending_is_rejected(nano_run):
    run_dir, _, _ = nano_run
    _write_spec(run_dir, ("REQ-001",))
    (run_dir / "artifacts" / "acceptance.md").write_text(
        "# Acceptance\n## 1. Матрица приёмки\n| REQ-001 | criterion | evidence | `PENDING` |\n"
        "## 2. Дефекты\n",
        encoding="utf-8",
    )
    errors = validate_acceptance(run_dir)
    assert any("PENDING" in error for error in errors)
    assert any("lacks an explicit PASS row" in error for error in errors)


def test_acceptance_requires_pass_for_every_req(nano_run):
    run_dir, _, _ = nano_run
    _write_spec(run_dir, ("REQ-001", "REQ-002"))
    (run_dir / "artifacts" / "acceptance.md").write_text(
        "# Acceptance\n## 1. Матрица приёмки\n"
        "| REQ | Критерий | Доказательство | Статус |\n"
        "| `REQ-001` | add | `test_add` | `PASS` |\n"
        "## 2. Дефекты\n| `BUG-001` | `Low` | cosmetic | `Open` |\n",
        encoding="utf-8",
    )
    errors = validate_acceptance(run_dir)
    assert errors == ["acceptance.md lacks an explicit PASS row for REQ-002"]


def test_acceptance_blocked_marker_is_rejected(nano_run):
    run_dir, _, _ = nano_run
    _write_spec(run_dir, ("REQ-001",))
    (run_dir / "artifacts" / "acceptance.md").write_text(
        "# Acceptance\n## 1. Матрица приёмки\n| `REQ-001` | add | none | `PASS` |\n"
        "| `REQ-002` | - | - | `BLOCKED` |\n## 2. Дефекты\n",
        encoding="utf-8",
    )
    assert any("BLOCKED" in error for error in validate_acceptance(run_dir))


def test_check_sections_requires_a_standalone_heading():
    required = ["## 1. Что сделано"]
    assert check_sections("# Report\n## 1. Что сделано\nbody\n", required) == []
    assert check_sections("# Report\nsee `## 1. Что сделано` inline\n", required) == required
    assert check_sections("# Report\n## 1. Что сделано и проверено\n", required) == required


def test_empty_screens_directory_is_rejected(nano_run):
    from factory.config import load_artifacts, load_pipeline
    from factory.state import find_stage

    run_dir, _, _ = nano_run
    stage = find_stage(load_pipeline(), "s2_architecture")
    errors = validate_stage(run_dir, stage, load_artifacts(), {})
    assert any("artifacts/screens" in error and "empty" in error for error in errors)


def test_empty_patches_directory_is_rejected(nano_run):
    from factory.config import load_artifacts, load_pipeline
    from factory.state import find_stage

    run_dir, _, _ = nano_run
    stage = find_stage(load_pipeline(), "s9_learn")
    errors = validate_stage(run_dir, stage, load_artifacts(), {})
    assert any("artifacts/patches" in error and "empty" in error for error in errors)


def test_standalone_schemas_match_the_state_records():
    state_schema = load_json(Path("factory/schemas/state.schema.json"))
    for name, key in (("task.schema.json", "task"), ("gate.schema.json", "gate")):
        other = load_json(Path("factory/schemas") / name)
        assert schema_parity_errors(state_schema, name, other, key) == []

    drifted = load_json(Path("factory/schemas/gate.schema.json"))
    drifted["properties"].pop("decided_by")
    assert schema_parity_errors(state_schema, "gate.schema.json", drifted, "gate")


def test_schema_parity_reports_a_missing_definition():
    assert schema_parity_errors({}, "gate.schema.json", {}, "gate") == [
        "state.schema.json is missing $defs.gate"
    ]


def test_budget_config_is_typed():
    budget = load_budget()
    assert isinstance(budget["default_max_attempts"], int)
    assert isinstance(budget["max_veto_retries"], int)
    assert budget["default_max_attempts"] >= 1


def test_doctor_reports_the_legacy_polkit_rule(tmp_path, monkeypatch):
    """Старое правило даёт root без пароля — doctor обязан об этом сказать."""
    from factory.runtime import isolation
    from factory.verify import doctor_report

    legacy = tmp_path / "50-factory-transient-units.rules"
    legacy.write_text(
        'polkit.addRule(function(){ return polkit.Result.YES; });\n', encoding="utf-8"
    )
    monkeypatch.setattr(isolation, "LEGACY_POLKIT_RULE", legacy)
    errors = doctor_report()["errors"]
    assert any("root без пароля" in error for error in errors), errors

    monkeypatch.setattr(isolation, "LEGACY_POLKIT_RULE", tmp_path / "absent.rules")
    assert not any("root без пароля" in error for error in doctor_report()["errors"])


def test_doctor_probes_when_the_rules_directory_is_unreadable(tmp_path, monkeypatch):
    """Каталог правил polkit закрыт (`0750 root:polkitd`) — наличие файла не проверить.

    Тогда спрашиваем polkit напрямую: если транзиентный юнит запускается без
    пароля, root у оператора есть, даже если файл правила ему не виден.
    """
    from factory import verify
    from factory.runtime import isolation

    monkeypatch.setattr(isolation, "LEGACY_POLKIT_RULE", tmp_path / "absent.rules")
    monkeypatch.setattr(verify, "_transient_units_allowed", lambda: True)
    monkeypatch.setattr(verify, "_has_passwordless_sudo", lambda: False)
    warnings = verify.doctor_report()["warnings"]
    assert any("транзиентные systemd-юниты" in warning for warning in warnings), warnings

    # Root, выданный администратором машины через sudo, — не наша находка.
    monkeypatch.setattr(verify, "_has_passwordless_sudo", lambda: True)
    assert not verify.doctor_report()["warnings"]

    monkeypatch.setattr(verify, "_transient_units_allowed", lambda: False)
    assert not verify.doctor_report()["warnings"]


def test_an_unreadable_rules_directory_is_not_a_crash(tmp_path, monkeypatch):
    """`Path.is_file()` на закрытом каталоге поднимает PermissionError."""
    from factory import verify
    from factory.runtime import isolation

    legacy = tmp_path / "rules.d" / "50-factory-transient-units.rules"
    legacy.parent.mkdir()
    legacy.write_text("polkit", encoding="utf-8")

    def refuse(_path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(isolation, "LEGACY_POLKIT_RULE", legacy)
    monkeypatch.setattr(verify.Path, "is_file", refuse)
    assert verify._legacy_rule_installed() is False
    assert verify._executor_boundary_errors() == []


def test_the_privilege_probe_runs_nothing_but_true(monkeypatch):
    """Проба не должна давать root никому, кроме как на `true`."""
    from factory import verify

    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(verify.shutil, "which", lambda name: "/usr/bin/systemd-run")
    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    assert verify._transient_units_allowed() is False
    assert seen["argv"][-1] == "/usr/bin/true"
    assert "/bin/sh" not in seen["argv"]


def test_root_artifact_ignores_a_stray_copy_inside_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    project_copy = tmp_path / "skills-lock.json"
    project_copy.write_text('{"schema_version": "1.0.0", "skills": []}\n', encoding="utf-8")
    stray = run_dir / "skills-lock.json"
    stray.write_text("{}", encoding="utf-8")

    entries = collect_artifact_entries(
        run_dir, ["skills-lock.json"], root_paths=["skills-lock.json"]
    )
    assert entries[0]["path"] == "skills-lock.json"
    assert entries[0]["sha256"] == sha256_file(project_copy)
    assert entries[0]["sha256"] != sha256_file(stray)
    resolved = resolve_recorded_path(
        run_dir, "skills-lock.json", root_paths=["skills-lock.json"]
    )
    assert resolved == project_copy.resolve()
