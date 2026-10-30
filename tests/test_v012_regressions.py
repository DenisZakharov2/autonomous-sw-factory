"""Regression tests for the 0.1.2 audit fixes."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from factory.artifacts import validate_acceptance, validate_markdown, validate_plan
from factory.config import load_pipeline
from factory.engine import (
    accept_risk,
    gate_approve,
    gate_reject,
    next_action,
    stage_reset,
    submit_work,
    task_accept,
    task_claim,
    task_reject,
    task_reset,
    task_submit,
    veto,
)
from factory.errors import QuarantineError, RunError
from factory.metrics import compute_metrics
from factory.quarantine import hash_skill_dir
from factory.state import load_state, save_state
from factory.trace import build_matrix
from factory.verify import verify_run

BRIEF = (
    "## 1. Цель (JTBD)\n\nХранить задачи.\n"
    "## 2. Out-of-Scope\n\nНет синхронизации.\n"
    "## 3. Требования\n\nОдна задача — одна строка.\n"
    "## 4. Профиль\n\nОдин пользователь.\n"
)
GLOSSARY = "## Термины\n\nЗадача — единица работы.\n"
PLAN = (
    "# Plan\n## 1. Задачи\n"
    "| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Проверка |\n"
    "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
    "| `TASK-001` | `12-fullstack-developer` | `REQ-001` | `-` | `product/src/todo.py` | `true` |\n"
)
PLAN_DEP = PLAN + (
    "| `TASK-002` | `12-fullstack-developer` | `REQ-002` | `TASK-001` | `product/src/todo.py` | `true` |\n"
)


def _write(run: Path, relative: str, text: str) -> Path:
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _advance_to(run: Path, target: str) -> None:
    pipeline = load_pipeline()
    order = {stage["id"]: stage["order"] for stage in pipeline["stages"]}
    state = load_state(run)
    for stage in pipeline["stages"]:
        if stage["order"] < order[target]:
            state["stage_status"][stage["id"]] = "accepted"
            state["gate_status"][stage["gate"]["id"]]["status"] = "approved"
    state["stage_status"][target] = "in_progress"
    state["current_stage"] = target
    save_state(run, state)


def _submit_intake(run: Path) -> dict:
    _write(run, "artifacts/interview-brief.md", BRIEF)
    _write(run, "artifacts/GLOSSARY.md", GLOSSARY)
    return submit_work(
        run.name,
        stage_id="s00_intake",
        role="01-smart-interviewer",
        artifacts=["artifacts/interview-brief.md", "artifacts/GLOSSARY.md"],
        root=run.parent,
    )


def _approve_intake(run: Path) -> dict:
    _submit_intake(run)
    return gate_approve(
        run.name, gate_id="gate_s00", approver="human:denis",
        evidence=["brief accepted"], root=run.parent,
    )


def test_submit_requires_at_least_one_artifact(nano_run):
    run, _, _ = nano_run
    with pytest.raises(RunError, match="at least one --artifact"):
        submit_work("run-01", stage_id="s00_intake", role="01-smart-interviewer",
                    artifacts=[], root=run.parent)
    assert load_state(run)["stage_status"]["s00_intake"] == "pending"


def test_submit_records_hashes(nano_run):
    run, _, _ = nano_run
    event = _submit_intake(run)
    assert {item["path"] for item in event["artifacts"]} == {
        "artifacts/interview-brief.md", "artifacts/GLOSSARY.md",
    }
    assert all(len(item["sha256"]) == 64 for item in event["artifacts"])
    assert load_state(run)["stage_status"]["s00_intake"] == "review"


def test_tamper_after_submit_is_detected(nano_run):
    run, _, _ = nano_run
    _submit_intake(run)
    _write(run, "artifacts/interview-brief.md", BRIEF + "\nПодмена.\n")
    errors = verify_run(run, "s00_intake")
    assert any("tampered after submission/approval" in error for error in errors)


def test_tamper_after_approval_is_detected(nano_run):
    run, _, _ = nano_run
    _approve_intake(run)
    _write(run, "artifacts/GLOSSARY.md", GLOSSARY + "\nПодмена.\n")
    errors = verify_run(run, "s00_intake", require_gate=True)
    assert any("tampered after submission/approval" in error for error in errors)


def test_stages_cannot_be_closed_out_of_order(nano_run):
    run, _, _ = nano_run
    with pytest.raises(RunError, match="not current"):
        gate_approve("run-01", gate_id="gate_s1", approver="human:denis",
                     evidence=["spec ok"], root=run.parent)
    with pytest.raises(RunError, match="not current"):
        gate_approve("run-01", gate_id="gate_s2", approver="02-independent-verifier",
                     evidence=["arch ok"], root=run.parent)


def test_gate_requires_review_status(nano_run):
    run, _, _ = nano_run
    with pytest.raises(RunError, match="expected one of"):
        gate_approve("run-01", gate_id="gate_s00", approver="human:denis",
                     evidence=["nothing submitted"], root=run.parent)


def test_reject_returns_the_stage_to_produce(nano_run):
    run, _, _ = nano_run
    _submit_intake(run)
    gate_reject("run-01", gate_id="gate_s00", approver="human:denis",
                evidence=["rewrite the brief"], root=run.parent)
    order = next_action("run-01", root=run.parent)
    assert order["action"] == "produce"
    assert order["next_command"].startswith("factory submit")


def test_verify_command_no_longer_requires_the_pending_gate(nano_run):
    run, _, _ = nano_run
    _submit_intake(run)
    order = next_action("run-01", root=run.parent)
    assert order["action"] == "gate"
    assert "--require-gate" not in order["next_command"]
    assert order["next_command"].startswith("factory gate approve")
    assert verify_run(run, "s00_intake") == []


def test_directory_artifact_is_hashed_and_tamper_detected(nano_run):
    run, _, _ = nano_run
    _advance_to(run, "s2_architecture")
    specs = {
        "artifacts/architecture.md": "## 1. Компоненты\n\nКомпонент.\n## 2. REQ mapping\n\nREQ-001.\n## 3. Решения\n\nADR.\n",
        "artifacts/api-contract.md": "## 1. Эндпоинты\n\nGET /tasks.\n## 2. Модели\n\nTask.\n## 3. Ошибки\n\n400.\n",
        "artifacts/data-model.md": "## 1. Таблицы\n\ntasks.\n## 2. Индексы\n\nid.\n## 3. Миграции\n\n001.\n",
        "artifacts/threat-model.md": "## 1. Активы\n\nДанные.\n## 2. Векторы\n\nВвод.\n## 3. Контрмеры\n\nВалидация.\n",
        "artifacts/adr/ADR-1.md": "## Контекст\n\nКонтекст.\n## Решение\n\nРешение.\n## Последствия\n\nПоследствия.\n",
        "artifacts/ux-flows.md": "## 1. Экраны\n\nЭкран.\n## 2. Потоки\n\nПоток.\n## 3. Состояния\n\nСостояние.\n",
        "artifacts/ai-design.md": "## 1. Промпты\n\nПромпт.\n## 2. Валидация\n\nВалидация.\n## 3. Fallback\n\nFallback.\n## 4. Eval\n\nEval.\n",
        "artifacts/screens/todo.md": "## 1. Экраны\n\nСписок задач.\n",
    }
    for relative, text in specs.items():
        _write(run, relative, text)
    paths = [path for path in specs if not path.startswith("artifacts/screens/")]
    paths.append("artifacts/screens")
    event = submit_work(
        "run-01", stage_id="s2_architecture", role="12-fullstack-developer",
        artifacts=paths, root=run.parent,
    )
    directory = next(item for item in event["artifacts"] if item["path"].endswith("/"))
    assert directory["path"] == "artifacts/screens/"
    gate_approve("run-01", gate_id="gate_s2", approver="02-independent-verifier",
                 evidence=["arch ok"], root=run.parent)
    _write(run, "artifacts/screens/todo.md", "## 1. Экраны\n\nПодмена.\n")
    errors = verify_run(run, "s2_architecture", require_gate=True)
    assert any("tampered after submission/approval" in error for error in errors)


def test_root_artifact_can_be_submitted(nano_run, tmp_path, monkeypatch):
    run, _, _ = nano_run
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    (tmp_path / "skills-lock.json").write_text(
        json.dumps({"schema_version": "1.0.0", "skills": []}), encoding="utf-8"
    )
    _advance_to(run, "s15_quarantine")
    event = submit_work(
        "run-01", stage_id="s15_quarantine", role="00-orchestrator",
        artifacts=["skills-lock.json"], root=run.parent,
    )
    assert [item["path"] for item in event["artifacts"]] == ["skills-lock.json"]


def test_task_dispatch_claim_submit_accept(nano_run):
    run, _, _ = nano_run
    _advance_to(run, "s4_build")
    _write(run, "artifacts/plan.md", PLAN)
    first = next_action("run-01", root=run.parent)
    assert first["task_id"] == "TASK-001"
    assert first["next_command"].startswith("factory task claim")
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    second = next_action("run-01", root=run.parent)
    assert second["next_command"].startswith("factory task submit")
    _write(run, "product/src/todo.py", "print('ok')\n")
    task_submit("run-01", "TASK-001", "12-fullstack-developer",
                ["product/src/todo.py"], root=run.parent)
    third = next_action("run-01", root=run.parent)
    assert third["action"] == "verify"
    assert third["roles"] == ["02-independent-verifier"]
    assert third["next_command"].startswith("factory task accept")
    task_accept("run-01", "TASK-001", root=run.parent)


def test_dependent_task_is_not_dispatched_early(nano_run):
    run, _, _ = nano_run
    _advance_to(run, "s4_build")
    _write(run, "artifacts/plan.md", PLAN_DEP)
    order = next_action("run-01", root=run.parent)
    assert order["task_id"] == "TASK-001"
    task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
    _write(run, "product/src/todo.py", "print('ok')\n")
    task_submit("run-01", "TASK-001", "12-fullstack-developer",
                ["product/src/todo.py"], root=run.parent)
    order = next_action("run-01", root=run.parent)
    assert order["task_id"] == "TASK-001"
    assert order["action"] == "verify"


def test_build_gate_requires_accepted_tasks(nano_run):
    run, _, _ = nano_run
    _advance_to(run, "s4_build")
    _write(run, "artifacts/plan.md", PLAN)
    from factory.engine import import_plan

    import_plan("run-01", root=run.parent)
    _write(run, "tasks/TASK-001/deliverable.md", "## 1. Что сделано\n\nСделано.\n")
    _write(run, "product/src/todo.py", "print('ok')\n")
    _write(run, "product/tests/test_todo.py", "def test_ok():\n    assert True\n")
    from factory.artifacts import validate_stage
    from factory.config import load_artifacts

    stage = next(s for s in load_pipeline()["stages"] if s["id"] == "s4_build")
    errors = validate_stage(run, stage, load_artifacts(), load_state(run)["tasks"])
    assert any("is not accepted" in error for error in errors)


def test_accept_risk_unblocks_an_escalated_gate(nano_run):
    run, _, _ = nano_run
    for attempt in range(3):
        gate_reject("run-01", gate_id="gate_s00", approver="human:denis",
                    evidence=[f"reject {attempt}"], root=run.parent)
    assert load_state(run)["stage_status"]["s00_intake"] == "escalated"
    event = accept_risk(
        "run-01", gate_id="gate_s00", approver="human:denis",
        risk="Accept the brief as is", mitigation="Manual review later",
        root=run.parent,
    )
    assert event["event_type"] == "RISK_ACCEPTANCE"
    state = load_state(run)
    assert state["stage_status"]["s00_intake"] == "accepted"
    assert state["gate_status"]["gate_s00"]["status"] == "approved"


def test_stage_reset_unblocks_and_clears_attempts(nano_run):
    run, _, _ = nano_run
    for attempt in range(3):
        gate_reject("run-01", gate_id="gate_s00", approver="human:denis",
                    evidence=[f"reject {attempt}"], root=run.parent)
    stage_reset("run-01", stage_id="s00_intake", approver="human:denis",
                reason="Retry with a better brief", root=run.parent)
    state = load_state(run)
    assert state["stage_status"]["s00_intake"] == "pending"
    assert state["gate_status"]["gate_s00"]["attempts"] == 0


def test_blocking_veto_reopens_and_then_escalates(nano_run):
    run, _, _ = nano_run
    for index in range(3):
        veto("run-01", stage_id="s00_intake", role="01-smart-interviewer",
             code=f"VETO-00{index}", evidence=["brief is ambiguous"], root=run.parent)
    assert load_state(run)["stage_status"]["s00_intake"] == "escalated"
    with pytest.raises(RunError, match="no blocking veto"):
        veto("run-01", stage_id="s00_intake", role="12-fullstack-developer",
             code="VETO-X", evidence=["nope"], root=run.parent)


def test_human_gate_requires_a_token_when_key_is_set(nano_run, monkeypatch):
    run, _, _ = nano_run
    _submit_intake(run)
    monkeypatch.setenv("FACTORY_APPROVAL_KEY", "secret")
    with pytest.raises(RunError, match="approval-token"):
        gate_approve("run-01", gate_id="gate_s00", approver="human:denis",
                     evidence=["ok"], root=run.parent)
    token = hmac.new(b"secret", b"run-01|gate_s00|human:denis", hashlib.sha256).hexdigest()
    gate_approve("run-01", gate_id="gate_s00", approver="human:denis",
                 evidence=["ok"], root=run.parent, approval_token=token)


def test_event_model_tier_comes_from_the_role_card(nano_run):
    run, _, _ = nano_run
    event = _submit_intake(run)
    assert event["model"]["tier"] == "tier-3-frontier"


def test_verification_coverage_is_zero_on_init(nano_run):
    run, _, _ = nano_run
    assert compute_metrics(run)["verification_coverage"] == 0.0


def test_markdown_rejects_raw_templates_and_empty_sections(tmp_path):
    template = Path(__file__).resolve().parent.parent / "factory/templates/interview-brief.tmpl"
    raw = tmp_path / "raw.md"
    raw.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    sections = ["## 1. Цель (JTBD)", "## 2. Out-of-Scope", "## 3. Требования", "## 4. Профиль"]
    assert any("unresolved template placeholders" in error for error in validate_markdown(raw, sections))
    empty = tmp_path / "empty.md"
    empty.write_text("\n".join(sections) + "\n", encoding="utf-8")
    assert any("has no content" in error for error in validate_markdown(empty, sections))


def test_acceptance_ignores_ordinary_fail_words_but_flags_open_critical(nano_run):
    run, _, _ = nano_run
    _write(run, "artifacts/spec.md", "REQ-001: задачи.\n")
    _write(
        run, "artifacts/acceptance.md",
        "## 1. Матрица приёмки\n\n| ID | Критерий | Статус |\n|---|---|---|\n"
        "| REQ-001 | Invalid input must fail | PASS |\n"
        "| A-2 | Filter pending tasks | PASS |\n",
    )
    assert validate_acceptance(run) == []
    _write(
        run, "artifacts/acceptance.md",
        "## 1. Матрица приёмки\n\n| ID | Критерий | Статус |\n|---|---|---|\n"
        "| REQ-001 | works | PASS |\n\n"
        "## 2. Дефекты\n\n| ID | Severity | Описание | Статус |\n|---|---|---|---|\n"
        "| BUG-001 | Critical | RCE | Open |\n",
    )
    assert any("open Critical defect" in error for error in validate_acceptance(run))


def test_plan_req_prefix_collision_is_detected(nano_run):
    run, _, _ = nano_run
    _write(run, "artifacts/spec.md", "REQ-1: alpha\nREQ-10: beta\n")
    _write(run, "artifacts/plan.md", "## 1. Задачи\n\n| TASK-001 | 12 | REQ-10 | - | a.py | true |\n")
    assert "plan.md does not cover REQ-1" in validate_plan(run)


def test_traceability_checks_real_links(nano_run):
    run, _, _ = nano_run
    _write(run, "artifacts/spec.md", "REQ-001: задачи.\n")
    _write(run, "artifacts/spec-human.md", "REQ-001: задачи.\n")
    _write(run, "artifacts/plan.md", "| TASK-001 | 12 | REQ-001 | - | a.py | true |\n")
    _write(run, "product/tests/test_todo.py", "def test_ok():\n    assert True\n")
    _write(
        run, "artifacts/traceability.json",
        json.dumps({"schema_version": "1.0.0", "requirements": {
            "REQ-001": {
                "spec": "artifacts/spec.md#REQ-001",
                "tasks": ["TASK-999"],
                "tests": ["product/tests/test_missing.py::test_x"],
                "acceptance": "A-1",
            }
        }}),
    )
    matrix = build_matrix(run)
    assert matrix["coverage"] == 0.0
    assert matrix["requirements"][0]["tasks_ok"] is False
    assert matrix["requirements"][0]["tests_ok"] is False


def test_quarantine_rejects_symlinks(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (skill / "leak").symlink_to("/etc/hostname")
    with pytest.raises(QuarantineError, match="symlink forbidden"):
        hash_skill_dir(skill)


def test_doctor_ignores_a_foreign_project_version(tmp_path, monkeypatch):
    from factory import cli

    (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("proprietary\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["doctor"]) == 0


def test_package_data_lists_all_shipped_docs():
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "FACTORY_GLOSSARY.md" in pyproject
    assert "orchestrator/*.md" in pyproject


def test_partial_submit_keeps_the_stage_in_progress(nano_run):
    run, _, _ = nano_run
    _write(run, "artifacts/interview-brief.md", BRIEF)
    submit_work("run-01", stage_id="s00_intake", role="01-smart-interviewer",
                artifacts=["artifacts/interview-brief.md"], root=run.parent)
    assert load_state(run)["stage_status"]["s00_intake"] == "in_progress"
    assert next_action("run-01", root=run.parent)["action"] == "produce"


def test_task_reset_unblocks_an_escalated_task(nano_run):
    run, _, _ = nano_run
    _advance_to(run, "s4_build")
    _write(run, "artifacts/plan.md", PLAN)
    from factory.engine import import_plan

    import_plan("run-01", root=run.parent)
    _write(run, "product/src/todo.py", "print('ok')\n")
    for attempt in range(3):
        task_claim("run-01", "TASK-001", "12-fullstack-developer", root=run.parent)
        task_submit("run-01", "TASK-001", "12-fullstack-developer",
                    ["product/src/todo.py"], root=run.parent)
        task_reject("run-01", "TASK-001", f"attempt {attempt}", root=run.parent)
    assert load_state(run)["tasks"]["TASK-001"]["status"] == "escalated"
    task_reset("run-01", task_id="TASK-001", approver="human:denis",
               reason="retry", root=run.parent)
    task = load_state(run)["tasks"]["TASK-001"]
    assert task["status"] == "pending"
    assert task["attempts"] == 0
