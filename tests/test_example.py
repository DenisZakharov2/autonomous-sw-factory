import json
from pathlib import Path

from factory.verify import verify_run

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "nano-todo"


def test_reference_run_is_complete(monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(EXAMPLE))
    errors = verify_run(EXAMPLE, "s9_learn", require_gate=True)
    assert errors == []


def test_reference_run_state_is_closed(monkeypatch):
    state = json.loads((EXAMPLE / "state.json").read_text(encoding="utf-8"))
    assert state["event_count"] == 33
    assert all(status == "accepted" for status in state["stage_status"].values())
    assert all(gate["status"] == "approved" for gate in state["gate_status"].values())
    assert state["human_spec_approved"] is True


def test_reference_run_has_no_placeholders(monkeypatch):
    text = "\n".join(path.read_text(encoding="utf-8") for path in EXAMPLE.rglob("*.md"))
    assert "{{" not in text
    assert "[ТРЕБУЕТ ПРОВЕРКИ]" in text  # deliverable status, not a PASS
