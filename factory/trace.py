"""Requirement traceability matrix.

A reference in `traceability.json` is `path` or `path::symbol`.  The file part
must exist inside the run; the symbol part must be declared in that file.  The
rule exists because the matrix used to accept `test_x.py::test_that_was_renamed`
as long as `test_x.py` existed, so a run could report coverage 1.0 while every
named test was a fiction (see `handoff.md`, the `nano-product-mini` BUG-001).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .artifacts import _row_has_req, _table_rows, artifact_path, extract_req_ids
from .config import load_artifacts, load_json, validate_instance
from .errors import RunError
from .state import load_state

PY_DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)[ \t]*\(", re.MULTILINE)
PY_CLASS_RE = re.compile(r"^[ \t]*class[ \t]+([A-Za-z_]\w*)\b", re.MULTILINE)
RS_FN_RE = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?fn[ \t]+([A-Za-z_]\w*)[ \t]*[<(]",
    re.MULTILINE,
)
RS_TEST_ATTR_RE = re.compile(r"^#\[[ \t]*(?:tokio::)?test(?:[ \t]*\(|[ \t]*\])")
RS_ATTR_RE = re.compile(r"^#\[.*\]$")
RS_COMMENT_RE = re.compile(r"^(?://|/\*|\*|\*/)")


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


def _reference_path(run_dir: Path, file_part: str) -> Path | None:
    """Lexical containment: the referenced file must live inside the run.

    `resolve()` is deliberately not used: a symlink inside the run must not be
    followed out of it, so containment is decided on the written path only.
    """
    if not file_part:
        return None
    root = Path(os.path.normpath(str(run_dir)))
    candidate = Path(os.path.normpath(str(run_dir / file_part)))
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _has_symlink(candidate: Path, run_dir: Path) -> bool:
    current = Path(os.path.normpath(str(run_dir)))
    for part in candidate.relative_to(current).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _read_test_file(path: Path, cache: dict[Path, str | None] | None) -> str | None:
    key = path
    if cache is not None and key in cache:
        return cache[key]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = None
    if cache is not None:
        cache[key] = text
    return text


def _rust_test_attribute(text: str, offset: int) -> bool:
    """True when `#[test]` / `#[tokio::test]` sits directly above this `fn`.

    Blank lines and comments are allowed between the attribute and the `fn`;
    any other line ends the search, so a `fn` inside `#[cfg(test)] mod tests`
    without its own `#[test]` does not count.
    """
    for line in reversed(text[:offset].splitlines()):
        stripped = line.strip()
        if not stripped or RS_COMMENT_RE.match(stripped):
            continue
        if not stripped.startswith("#["):
            return False
        if RS_TEST_ATTR_RE.match(stripped):
            return True
        if not RS_ATTR_RE.match(stripped):
            return False
    return False


def _symbol_declared(text: str, symbol: str, suffix: str) -> bool:
    if suffix in {".py", ".pyi"}:
        return symbol in PY_DEF_RE.findall(text) or symbol in PY_CLASS_RE.findall(text)
    if suffix == ".rs":
        for match in RS_FN_RE.finditer(text):
            if match.group(1) == symbol and _rust_test_attribute(text, match.start()):
                return True
        return False
    return re.search(rf"\b{re.escape(symbol)}\b", text) is not None


def _test_problem(
    run_dir: Path, reference: str, cache: dict[Path, str | None] | None = None
) -> str | None:
    """Why this reference does not resolve, or None when it does.

    `path` (no `::`) only has to exist — the historical contract.  `path::name`
    additionally requires `name` to be declared in that file: a `def`/`async
    def`/`class` for Python, a `fn` carrying `#[test]`/`#[tokio::test]` for
    Rust, a whole word for anything else.  Segments before the last one
    (`Class::method`, `tests::name`) are not matched: for Rust it is enough
    that the module exists, and Python nesting is not required.
    """
    file_part, _, rest = reference.partition("::")
    candidate = _reference_path(run_dir, file_part)
    if candidate is None:
        return f"test path escapes the run: {file_part or reference}"
    if _has_symlink(candidate, run_dir):
        return f"test path is a symlink: {file_part}"
    if not candidate.is_file():
        return f"test file does not exist: {file_part}"
    segments = [segment.strip() for segment in rest.split("::") if segment.strip()]
    if not segments:
        return None
    symbol = re.split(r"[\[(]", segments[-1], maxsplit=1)[0].strip()
    if not symbol:
        return f"test name is empty: {reference}"
    text = _read_test_file(candidate, cache)
    if text is None:
        return f"test file is not readable as UTF-8: {file_part}"
    if not _symbol_declared(text, symbol, candidate.suffix.lower()):
        return f"test file exists but does not declare {symbol}: {file_part}"
    return None


def _test_exists(run_dir: Path, reference: str) -> bool:
    return _test_problem(run_dir, reference) is None


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
    read_cache: dict[Path, str | None] = {}
    rows = []
    for req in reqs:
        item = trace.get(req, {})
        tasks = list(item.get("tasks", []))
        tests = list(item.get("tests", []))
        tasks_ok = all(_task_exists(run_dir, task) for task in tasks) if tasks else False
        test_problems = [
            problem
            for problem in (_test_problem(run_dir, str(ref), read_cache) for ref in tests)
            if problem
        ]
        tests_ok = bool(tests) and tests_present and not test_problems
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
                "test_problems": test_problems,
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
