import json
import subprocess
from pathlib import Path

import pytest

from factory.quarantine import (
    AUTO_ALLOW,
    BLOCK_INCOMPLETE,
    BLOCK_RETRY,
    ESCALATE_HUMAN,
    REJECT_AND_PURGE,
    decide,
    hash_skill_dir,
    load_lock,
    parse_report,
    quarantine_skill,
    verify_lock,
)


def report(recommendation="SAFE", severity="LOW", complete=True, coverage=100.0, failed=None):
    statuses = [{"analyzer_id": "x", "status": "failed"}] if failed else []
    return {
        "skill": {"name": "demo"},
        "risk_assessment": {"recommendation": recommendation, "severity": severity},
        "analysis_completeness": {
            "is_complete": complete,
            "coverage_percent": coverage,
            "analyzer_statuses": statuses,
        },
        "metadata": {"skillspector_version": "2.12.0"},
    }


def test_parse_report():
    result = parse_report(report())
    assert result.recommendation == "SAFE"
    assert result.severity == "LOW"
    assert result.is_complete is True
    assert result.coverage_percent == 100.0


def test_decision_matrix():
    assert decide(0, parse_report(report())) == AUTO_ALLOW
    assert decide(0, parse_report(report(complete=False))) == BLOCK_INCOMPLETE
    assert decide(0, parse_report(report(failed=True))) == BLOCK_INCOMPLETE
    assert decide(1, parse_report(report())) == BLOCK_RETRY
    assert decide(0, parse_report(report("DO_NOT_INSTALL", "CRITICAL"))) == REJECT_AND_PURGE
    assert decide(0, parse_report(report("SAFE", "HIGH"))) == REJECT_AND_PURGE
    assert decide(0, parse_report(report("CAUTION", "MEDIUM"))) == ESCALATE_HUMAN
    assert decide(0, parse_report(report(coverage=80.0))) == BLOCK_INCOMPLETE
    assert decide(0, parse_report(report("SAFE", "MEDIUM"))) == REJECT_AND_PURGE


def make_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    return skill


def fake_runner(payload, exit_code=0):
    def run(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, exit_code, stdout="", stderr="")
    return run


def test_quarantine_writes_lock_on_safe(tmp_path):
    skill = make_skill(tmp_path)
    lock = tmp_path / "skills-lock.json"
    action, _result, entry = quarantine_skill(
        skill, name="demo", pack="local", commit="local", license="MIT",
        lock_path=lock, approved_by="human:test", runner=fake_runner(report()),
    )
    assert action == AUTO_ALLOW
    assert entry and entry["sha256"] == hash_skill_dir(skill)
    data = load_lock(lock)
    assert data["skills"][0]["name"] == "demo"
    assert verify_lock(lock, tmp_path) == []


def test_incomplete_scan_does_not_write_lock(tmp_path):
    skill = make_skill(tmp_path)
    lock = tmp_path / "skills-lock.json"
    action, _, entry = quarantine_skill(
        skill, name="demo", pack="local", commit="local", license="MIT",
        lock_path=lock, runner=fake_runner(report(complete=False)),
    )
    assert action == BLOCK_INCOMPLETE
    assert entry is None
    assert not lock.exists()


def test_substituted_skill_is_detected(tmp_path):
    skill = make_skill(tmp_path)
    lock = tmp_path / "skills-lock.json"
    quarantine_skill(
        skill, name="demo", pack="local", commit="local", license="MIT",
        lock_path=lock, runner=fake_runner(report()),
    )
    (skill / "SKILL.md").write_text("changed", encoding="utf-8")
    errors = verify_lock(lock, tmp_path)
    assert any("sha256 mismatch" in error for error in errors)


def test_caution_goes_to_human(tmp_path):
    skill = make_skill(tmp_path)
    action, _, entry = quarantine_skill(
        skill, name="demo", pack="local", commit="local", license="MIT",
        lock_path=tmp_path / "lock.json", runner=fake_runner(report("CAUTION", "MEDIUM")),
    )
    assert action == ESCALATE_HUMAN
    assert entry is None


def test_scanner_missing_is_error(tmp_path):
    from factory.errors import QuarantineError
    from factory.quarantine import scan_skill

    skill = make_skill(tmp_path)
    with pytest.raises(QuarantineError):
        scan_skill(skill, scanner="/definitely/not/a/scanner")
