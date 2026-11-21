import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.engine import init_run  # noqa: E402 — the repo root goes on sys.path above


@contextmanager
def _hidden_factory_env():
    """Hide the operator's FACTORY_* roots for the duration of the block."""
    old_root = os.environ.pop("FACTORY_ROOT", None)
    old_runs = os.environ.pop("FACTORY_RUNS_ROOT", None)
    try:
        yield
    finally:
        if old_root is not None:
            os.environ["FACTORY_ROOT"] = old_root
        if old_runs is not None:
            os.environ["FACTORY_RUNS_ROOT"] = old_runs


@pytest.fixture(autouse=True)
def _clean_factory_env():
    """A test must not depend on where the operator pointed `FACTORY_ROOT`."""
    with _hidden_factory_env():
        yield


@pytest.fixture()
def nano_run(tmp_path):
    run_dir, state, event = init_run("run-01", "nano", root=tmp_path)
    return run_dir, state, event


@pytest.fixture()
def runs_root(tmp_path):
    return tmp_path


def verification_report_text(
    subject: str = "stage s4_build",
    *,
    verdict: str = "PASS",
    checks: int = 3,
    reason: str = "all three checks returned 0",
) -> str:
    lines = [
        f"# Проверка: {subject}",
        "",
        "## 1. Проверено",
        "",
        f"- Предмет проверки: `{subject}`",
        "- Артефакты (только чтение): `artifacts/spec.md`",
        "",
        "## 2. Проверки",
        "",
    ]
    for index in range(1, checks + 1):
        lines += [
            f"### Проверка {index}: проверка {index}",
            "",
            "- Команда: `factory verify --run-id run-01 --stage s4_build`",
            "- Код возврата: `0`",
            "- Вывод:",
            "",
            "```text",
            f"check {index}: ok",
            "```",
            "",
        ]
    lines += [
        "## 3. Вердикт",
        "",
        f"- Вердикт: {verdict}",
        f"- Причина: `{reason}`",
        "",
    ]
    return "\n".join(lines)


def write_task_verification(
    run_dir: Path,
    task_id: str,
    *,
    verdict: str = "PASS",
    checks: int = 3,
    reason: str = "all three checks returned 0",
) -> Path:
    path = run_dir / "tasks" / task_id / "verification" / "verification.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        verification_report_text(
            f"task {task_id}", verdict=verdict, checks=checks, reason=reason
        ),
        encoding="utf-8",
    )
    return path


def _product_replay_block(run_dir: Path) -> str:
    """A fourth check that the host can and will re-run.

    `write_stage_verification` is used by gate tests on runs that already have
    product files.  Since PR-D a PASS on such a run must carry at least one
    command from the operator's allowlist, so the fixture runs the product's
    own test suite now and claims exactly what it printed -- the same thing a
    real verifier would do.  An empty product has nothing to replay, and a
    product without test files has no command the fixture can honestly claim.
    """
    product = run_dir / "product"
    if not product.is_dir():
        return ""
    tests = [
        path
        for path in product.rglob("*.py")
        if path.is_file()
        and (path.name.startswith("test_") or path.name.endswith("_test.py"))
    ]
    if not tests:
        return ""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "product"],
        cwd=run_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    claimed = lines[-1] if lines else "OK"
    return (
        "\n### Проверка 4: тесты продукта\n\n"
        "- Команда: `python3 -m pytest -q product`\n"
        f"- Код возврата: `{completed.returncode}`\n"
        "- Вывод:\n\n"
        "```text\n"
        f"{claimed}\n"
        "```\n"
    )


def write_stage_verification(
    run_dir: Path,
    stage_id: str,
    *,
    verdict: str = "PASS",
    checks: int = 3,
    reason: str = "all three checks returned 0",
) -> Path:
    path = run_dir / "artifacts" / "verification" / stage_id / "verification.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = verification_report_text(f"stage {stage_id}", verdict=verdict, checks=checks, reason=reason)
    if checks >= 3 and verdict == "PASS":
        block = _product_replay_block(run_dir)
        if block:
            text = text.replace("\n## 3. Вердикт", block + "\n## 3. Вердикт", 1)
    path.write_text(text, encoding="utf-8")
    return path
