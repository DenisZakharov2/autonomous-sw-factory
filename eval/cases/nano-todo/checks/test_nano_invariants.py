"""Invariants of the nano reference product that `expected.json` cannot express.

pytest runs this file with the run directory as CWD, so every path below is
relative to the run.  It checks content, not wording: the same REQ set in the
spec and the traceability matrix, one plan row per task, PASS in acceptance.
"""
import json
import re
from pathlib import Path


def _read(relative: str) -> str:
    return Path(relative).read_text(encoding="utf-8")


def test_spec_and_traceability_share_the_requirement_set():
    spec_reqs = set(re.findall(r"\bREQ-[A-Z0-9][A-Z0-9-]*\b", _read("artifacts/spec.md")))
    trace = json.loads(_read("artifacts/traceability.json"))["requirements"]
    assert spec_reqs == set(trace), (sorted(spec_reqs), sorted(trace))


def test_every_requirement_has_a_task_and_a_test():
    trace = json.loads(_read("artifacts/traceability.json"))["requirements"]
    for req, item in trace.items():
        assert item["tasks"], req
        assert item["tests"], req
        for reference in item["tests"]:
            assert Path(reference.split("::", 1)[0]).is_file(), (req, reference)


def test_plan_has_one_row_per_task_and_acceptance_is_green():
    plan = _read("artifacts/plan.md")
    rows = [line for line in plan.splitlines() if line.startswith("| `TASK-")]
    assert len(rows) == 2, rows
    acceptance = _read("artifacts/acceptance.md")
    for req in ("REQ-001", "REQ-002", "REQ-003"):
        row = next(line for line in acceptance.splitlines() if req in line and line.startswith("|"))
        assert "PASS" in row, row
