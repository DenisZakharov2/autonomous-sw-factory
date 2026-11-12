"""Machine-readable evidence for gates and for the human who takes over.

A gate approver needs facts, not a summary: the deterministic verdict, the
hashes that were recorded, and the journal position.  Evidence is written
under `runtime/evidence/`, outside the artifact namespace, so it never
pollutes the hashes the journal covers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..journal import read_events, sha256_file, utc_now
from ..paths import safe_write_text
from ..state import load_state
from ..verify import verify_run


class EvidenceStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.directory = run_dir / "runtime" / "evidence"

    def _sequence(self) -> int:
        """Use the journal position so evidence files never overwrite each other.

        The state is the fast answer; the journal is the fallback when the
        state is unreadable.  Returning a constant would collapse every
        evidence file onto one name, and the audit trail would lose its own
        entries quietly.
        """
        try:
            return int(load_state(self.run_dir)["event_count"])
        except Exception:  # noqa: BLE001 - the state may be damaged mid-run
            return len(read_events(self.run_dir / "blackbox.jsonl"))

    def _write(self, name: str, payload: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / name
        safe_write_text(
            path,
            json.dumps({"timestamp_utc": utc_now(), **payload}, ensure_ascii=False, indent=2)
            + "\n",
        )
        return path

    def write_step(self, step: int, order: dict[str, Any], payload: dict[str, Any]) -> Path:
        stage = str(order.get("stage") or "stage")
        name = f"step-{self._sequence():04d}-{stage}-{step:02d}.json"
        return self._write(name, {"step": step, "order": order, **payload})

    def write_human_stop(self, order: dict[str, Any], payload: dict[str, Any]) -> Path:
        stage = str(order.get("stage") or "stage")
        name = f"human-{self._sequence():04d}-{stage}.json"
        return self._write(name, {"order": order, **payload})

    def gate_evidence(
        self, order: dict[str, Any], extra: list[str] | None = None
    ) -> list[str]:
        stage = str(order.get("stage") or "")
        evidence: list[str] = []
        errors = verify_run(self.run_dir, stage, require_gate=False) if stage else []
        evidence.append(
            f"factory verify --stage {stage}: {len(errors)} error(s)"
            + (f" ({errors[0]})" if errors else "")
        )
        state_path = self.run_dir / "state.json"
        if state_path.is_file():
            evidence.append(f"state.json sha256 {sha256_file(state_path)[:16]}")
        try:
            events = read_events(self.run_dir / "blackbox.jsonl")
        except Exception:  # noqa: BLE001 - evidence must never mask the gate
            events = []
        if events:
            evidence.append(
                f"journal: {len(events)} events, head {str(events[-1].get('hash'))[:16]}"
            )
        for event in events:
            if event.get("event_type") != "ARTIFACT_SUBMIT":
                continue
            if str(event.get("stage")) != stage:
                continue
            for item in event.get("artifacts") or []:
                evidence.append(
                    f"artifact {item.get('path')} sha256 {str(item.get('sha256'))[:16]}"
                )
        if extra:
            evidence.extend(str(line) for line in extra)
        return evidence[:16]
