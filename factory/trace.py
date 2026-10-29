"""Requirement traceability matrix."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import extract_req_ids
from .config import load_json, validate_instance
from .errors import RunError


def load_traceability(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "artifacts" / "traceability.json"
    if not path.exists():
        raise RunError(f"traceability not found: {path}")
    data = load_json(path)
    schema = load_json(Path(__file__).resolve().parent / "schemas" / "traceability.schema.json")
    validate_instance(data, schema, str(path))
    return data


def build_matrix(run_dir: Path) -> dict[str, Any]:
    spec = run_dir / "artifacts" / "spec.md"
    if not spec.exists():
        raise RunError(f"spec not found: {spec}")
    reqs = sorted(extract_req_ids(spec.read_text(encoding="utf-8")))
    trace = load_traceability(run_dir)["requirements"]
    rows = []
    for req in reqs:
        item = trace.get(req, {})
        rows.append(
            {
                "req": req,
                "spec": bool(item.get("spec")),
                "tasks": list(item.get("tasks", [])),
                "tests": list(item.get("tests", [])),
                "acceptance": item.get("acceptance", ""),
                "covered": bool(item.get("spec") and item.get("tasks") and item.get("tests") and item.get("acceptance")),
            }
        )
    covered = sum(1 for row in rows if row["covered"])
    return {
        "run_id": run_dir.name,
        "requirements": rows,
        "total": len(rows),
        "covered": covered,
        "coverage": (covered / len(rows)) if rows else 0.0,
    }
