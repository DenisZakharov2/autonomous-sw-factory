import json

import pytest

from factory.config import load_artifacts, load_pipeline, mode_roles
from factory.errors import ConfigError
from factory.pipeline import (
    effective_owner,
    effective_roles,
    role_ids,
    stage_artifacts,
    validate_pipeline,
)
from factory.state import find_stage
from factory.verify import doctor_report


def test_doctor_is_clean():
    report = doctor_report()
    assert report["errors"] == []
    assert report["roles"] == 22
    assert report["stages"] == 13
    assert report["templates"] >= 30
    assert report["schemas"] >= 9


def test_mode_role_counts():
    pipeline = load_pipeline()
    assert len(mode_roles(pipeline, "nano")) == 4
    assert len(mode_roles(pipeline, "standard")) == 12
    assert len(mode_roles(pipeline, "brownfield")) == 12
    assert len(mode_roles(pipeline, "enterprise")) == 22


def test_every_stage_has_owner_in_every_mode():
    pipeline = load_pipeline()
    registry = load_artifacts()
    for mode in pipeline["modes"]:
        for stage in pipeline["stages"]:
            active = effective_roles(pipeline, mode, stage)
            assert active, (mode, stage["id"])
            for artifact_id in stage["artifacts"]:
                spec = registry["artifacts"][artifact_id]
                if spec.get("per_task"):
                    continue
                owner = effective_owner(pipeline, mode, artifact_id, spec["owner"])
                assert owner in active, (mode, stage["id"], artifact_id, owner, active)


def test_nano_spec_owner_is_interviewer():
    pipeline = load_pipeline()
    registry = load_artifacts()
    stage = find_stage(pipeline, "s1_spec")
    artifacts = stage_artifacts(pipeline, registry, "nano", stage)
    owners = {item["id"]: item["owner"] for item in artifacts}
    assert owners["spec"] == "01-smart-interviewer"
    assert owners["spec_human"] == "01-smart-interviewer"
    assert owners["traceability"] == "01-smart-interviewer"


def test_pipeline_has_three_human_gates():
    pipeline = load_pipeline()
    human = [stage["gate"]["id"] for stage in pipeline["stages"] if stage["gate"]["approver"] == "human"]
    assert human == ["gate_s_in", "gate_s00", "gate_s0", "gate_s1", "gate_s7", "gate_s9"]


def test_invalid_pipeline_is_rejected():
    pipeline = load_pipeline()
    broken = json.loads(json.dumps(pipeline))
    broken["stages"][0]["rework_to"] = "does_not_exist"
    errors = validate_pipeline(broken, load_artifacts())
    assert any("rework_to" in error for error in errors)


def test_role_ids_are_22():
    assert len(role_ids()) == 22


def test_unknown_mode_rejected():
    with pytest.raises(ConfigError):
        mode_roles(load_pipeline(), "turbo")
