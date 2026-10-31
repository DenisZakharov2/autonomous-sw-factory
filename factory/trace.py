"""Requirement traceability matrix."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .artifacts import _row_has_req, _table_rows, artifact_path, extract_req_ids
from .config import load_artifacts, load_json, validate_instance
from .errors import RunError
from .state import load_state


def load_traceability(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "artifacts" / "traceability.json"
    if not path.exists():
        raise RunError(f"traceability not found: {path}")
    data = load_json(path)
    schema = load_json(Path(__file__).resolve().parent / "schemas" / "traceability.schema.json")
    validate_instance(data, schema, str(path))
    return data


def _task_exists(run_dir: Path, task_id: str) -> bool:
    plan = run_dir / "artifacts" / "plan.md"
    if plan.exists() and task_id in plan.read_text(encoding="utf-8"):
        return True
    state = run_dir / "state.json"
    if state.exists():
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return task_id in (data.get("tasks") or {})
    return False


def _test_exists(run_dir: Path, reference: str) -> bool:
    file_part = reference.split("::", 1)[0]
    if not file_part:
        return False
    candidate = (run_dir / file_part).resolve()
    root = run_dir.resolve()
    return (candidate == root or root in candidate.parents) and candidate.exists()


def _tests_root(run_dir: Path) -> Path:
    """Where this run's product tests live.

    Greenfield uses `product/tests`; brownfield remaps the artifact to the
    seeded tree (Rust tests may be inline in `src/`).  Reading the registry
    keeps traceability honest instead of silently accepting any reference when
    the greenfield path is absent.
    """
    try:
        mode = str(load_state(run_dir).get("mode") or "")
    except Exception:
        mode = ""
    spec = load_artifacts(mode or None)["artifacts"]["product_tests"]
    return artifact_path(run_dir, spec)


def build_matrix(run_dir: Path) -> dict[str, Any]:
    spec = run_dir / "artifacts" / "spec.md"
    if not spec.exists():
        raise RunError(f"spec not found: {spec}")
    reqs = sorted(extract_req_ids(spec.read_text(encoding="utf-8")))
    trace = load_traceability(run_dir)["requirements"]
    tests_root = _tests_root(run_dir)
    tests_present = tests_root.exists() and any(
        item.is_file() and "__pycache__" not in item.parts for item in tests_root.rglob("*")
    )
    acceptance = run_dir / "artifacts" / "acceptance.md"
    acceptance_rows = _table_rows(acceptance.read_text(encoding="utf-8"), "## 1. Матрица приёмки") if acceptance.exists() else []
    rows = []
    for req in reqs:
        item = trace.get(req, {})
        tasks = list(item.get("tasks", []))
        tests = list(item.get("tests", []))
        tasks_ok = all(_task_exists(run_dir, task) for task in tasks) if tasks else False
        tests_ok = bool(tests) and tests_present and all(_test_exists(run_dir, ref) for ref in tests)
        acceptance_ok = bool(item.get("acceptance"))
        if acceptance_rows:
            acceptance_ok = acceptance_ok and any(
                _row_has_req(row, req)
                and re.search(r"\bPASS\b", row[-1] if row else "", re.IGNORECASE)
                for row in acceptance_rows
            )
        covered = bool(item.get("spec") and tasks and tests and item.get("acceptance")) and tasks_ok and tests_ok and acceptance_ok
        rows.append(
            {
                "req": req,
                "spec": bool(item.get("spec")),
                "tasks": tasks,
                "tests": tests,
                "acceptance": item.get("acceptance", ""),
                "tasks_ok": tasks_ok,
                "tests_ok": tests_ok,
                "acceptance_ok": acceptance_ok,
                "covered": covered,
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
