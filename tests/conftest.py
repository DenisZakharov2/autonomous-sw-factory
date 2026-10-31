import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.engine import init_run


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
    """A report that satisfies (or deliberately fails) the verifier validator."""
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
    path.write_text(
        verification_report_text(
            f"stage {stage_id}", verdict=verdict, checks=checks, reason=reason
        ),
        encoding="utf-8",
    )
    return path
