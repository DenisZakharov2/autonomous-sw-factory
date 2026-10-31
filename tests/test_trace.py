"""`traceability.json` names a symbol, not just a file.

The old rule accepted `tests/test_cli.py::test_that_does_not_exist` as long as
`tests/test_cli.py` existed, so a matrix could claim coverage 1.0 over tests
nobody wrote (`nano-product-mini`, BUG-001).  These tests pin the replacement
rule: the last `::` segment must be declared in the referenced file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.engine import init_run
from factory.trace import build_matrix

PLAN = "| `TASK-001` | `12-fullstack-developer` | `REQ-001` | `-` | `a.py` | `true` |\n"


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(tmp_path: Path, *, tests=None, files: dict[str, str] | None = None) -> Path:
    run_dir, _, _ = init_run("run-01", "nano", root=tmp_path)
    _write(run_dir, "artifacts/spec.md", "## REQ-001\n\nПриложение ведёт список задач.\n")
    _write(run_dir, "artifacts/plan.md", PLAN)
    _write(run_dir, "product/tests/placeholder.py", "# the product test tree exists\n")
    for relative, text in (files or {}).items():
        _write(run_dir, relative, text)
    _write(
        run_dir,
        "artifacts/traceability.json",
        json.dumps(
            {
                "schema_version": "1.0.0",
                "requirements": {
                    "REQ-001": {
                        "spec": "artifacts/spec.md#REQ-001",
                        "tasks": ["TASK-001"],
                        "tests": tests if tests is not None else [],
                        "acceptance": "A-1",
                    }
                },
            }
        ),
    )
    return run_dir


def _row(run_dir: Path) -> dict:
    matrix = build_matrix(run_dir)
    assert matrix["total"] == 1
    return matrix["requirements"][0]


def test_pytest_function_is_found(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/tests/test_todo.py::test_add"],
        files={"product/tests/test_todo.py": "def test_add():\n    assert True\n"},
    )
    row = _row(run_dir)
    assert row["tests_ok"] is True, row["test_problems"]
    assert row["covered"] is True


def test_async_function_is_found(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/tests/test_api.py::test_async"],
        files={
            "product/tests/test_api.py": "async def test_async():\n    assert True\n"
        },
    )
    assert _row(run_dir)["tests_ok"] is True


def test_unittest_class_is_found(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/tests/test_cli.py::CliTestCase"],
        files={
            "product/tests/test_cli.py": (
                "import unittest\n\n\nclass CliTestCase(unittest.TestCase):\n"
                "    def test_one(self):\n        pass\n"
            )
        },
    )
    assert _row(run_dir)["tests_ok"] is True


def test_unittest_method_behind_a_class_segment_is_found(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/tests/test_cli.py::CliTestCase::test_one"],
        files={
            "product/tests/test_cli.py": (
                "import unittest\n\n\nclass CliTestCase(unittest.TestCase):\n"
                "    def test_one(self):\n        pass\n"
            )
        },
    )
    assert _row(run_dir)["tests_ok"] is True


def test_rust_test_function_is_found(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/src/lib.rs::tests::parses_a_flag"],
        files={
            "product/src/lib.rs": (
                "pub fn parse() -> bool { true }\n\n"
                "#[cfg(test)]\nmod tests {\n"
                "    use super::*;\n\n"
                "    #[test]\n"
                "    fn parses_a_flag() {\n        assert!(parse());\n    }\n"
                "}\n"
            )
        },
    )
    assert _row(run_dir)["tests_ok"] is True


def test_tokio_test_attribute_counts(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/src/net.rs::tests::handles_a_connection"],
        files={
            "product/src/net.rs": (
                "async fn serve() {}\n\n"
                "#[cfg(test)]\nmod tests {\n"
                "    #[tokio::test]\n"
                "    async fn handles_a_connection() {\n        serve().await;\n    }\n"
                "}\n"
            )
        },
    )
    assert _row(run_dir)["tests_ok"] is True


def test_attribute_may_be_separated_by_comments_and_blank_lines(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/src/lib.rs::tests::sorted"],
        files={
            "product/src/lib.rs": (
                "#[cfg(test)]\nmod tests {\n"
                "    // regression: sorting drops duplicates\n"
                "    #[test]\n\n"
                "    // keep this attribute chain working\n"
                "    fn sorted() {}\n"
                "}\n"
            )
        },
    )
    assert _row(run_dir)["tests_ok"] is True


def test_rust_function_without_test_attribute_does_not_count(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/src/lib.rs::tests::helper"],
        files={
            "product/src/lib.rs": (
                "#[cfg(test)]\nmod tests {\n"
                "    #[test]\n    fn real_test() {}\n\n"
                "    fn helper() {}\n"
                "}\n"
            )
        },
    )
    row = _row(run_dir)
    assert row["tests_ok"] is False
    assert "does not declare helper" in row["test_problems"][0]


def test_missing_symbol_is_a_gap_even_when_the_file_exists(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/tests/test_todo.py::test_renamed_away"],
        files={"product/tests/test_todo.py": "def test_add():\n    assert True\n"},
    )
    row = _row(run_dir)
    assert row["tests_ok"] is False
    assert row["covered"] is False
    assert "test_renamed_away" in row["test_problems"][0]
    assert "test_todo.py" in row["test_problems"][0]


def test_reference_without_a_symbol_only_needs_the_file(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/tests/test_todo.py", "product/tests/test_other.py"],
        files={
            "product/tests/test_todo.py": "def test_add():\n    assert True\n",
            "product/tests/test_other.py": "# no tests yet, but the file is real\n",
        },
    )
    assert _row(run_dir)["tests_ok"] is True


def test_missing_file_is_still_a_gap(tmp_path):
    run_dir = _run(tmp_path, tests=["product/tests/test_absent.py::test_x"])
    row = _row(run_dir)
    assert row["tests_ok"] is False
    assert "does not exist" in row["test_problems"][0]


def test_symbol_in_another_file_of_the_same_directory_does_not_count(tmp_path):
    run_dir = _run(
        tmp_path,
        tests=["product/tests/test_one.py::test_shared"],
        files={
            "product/tests/test_one.py": "def test_other():\n    assert True\n",
            "product/tests/test_two.py": "def test_shared():\n    assert True\n",
        },
    )
    row = _row(run_dir)
    assert row["tests_ok"] is False
    assert "does not declare test_shared" in row["test_problems"][0]


def test_reference_outside_the_run_is_rejected(tmp_path):
    run_dir = _run(tmp_path, tests=["../outside.py::test_x"])
    _write(tmp_path, "outside.py", "def test_x():\n    assert True\n")
    row = _row(run_dir)
    assert row["tests_ok"] is False
    assert "escapes the run" in row["test_problems"][0]


def test_symlinked_test_file_is_not_followed(tmp_path):
    run_dir = _run(tmp_path, tests=["product/tests/test_link.py::test_x"])
    target = _write(tmp_path, "outside.py", "def test_x():\n    assert True\n")
    link = run_dir / "product" / "tests" / "test_link.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    row = _row(run_dir)
    assert row["tests_ok"] is False
    assert "symlink" in row["test_problems"][0]
