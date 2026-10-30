"""Deterministic executor for tests and for proving the loop without an LLM.

It writes the committed `examples/nano-todo` reference artifacts, so a full
`nano` run exercises every validator with real content.  It is a fixture, not
a model: it proves that the cycle, the gates and the journal work, and it says
nothing about whether an agent can do the same.

Scenarios exist to test the loop's refusal paths: a broken artifact, a write
to `state.json`, a write outside the run directory, and a slow step that
exhausts the time budget.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from ..config import PACKAGE_ROOT, project_root
from ..errors import RunError
from ..metrics import write_metrics
from .base import ExecutionResult, RunContext

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "nano"
PLACEHOLDER_RE = re.compile(r"\{\{[a-zA-Z0-9_.-]+\}\}")
SCENARIOS = ("ok", "break-artifact", "write-state", "escape", "slow")


def _within(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    root = parent.resolve()
    return resolved == root or root in resolved.parents


def _template_fill(spec: dict[str, Any]) -> str:
    template = spec.get("template")
    if not template:
        raise RunError(
            f"fake executor has no fixture and no template for artifact {spec.get('id')!r}"
        )
    path = PACKAGE_ROOT / str(template)
    text = path.read_text(encoding="utf-8")
    text = PLACEHOLDER_RE.sub("n/a", text)
    return text.rstrip() + "\n"


def _deliverable(task: dict[str, Any]) -> str:
    reqs = ", ".join(f"`{req}`" for req in task.get("reqs") or []) or "—"
    files = ", ".join(f"`{item}`" for item in task.get("allowed_files") or []) or "—"
    return (
        f"# Отчёт о выполнении задачи\n\n"
        f"## 1. Что сделано\n"
        f"- Task ID: `{task['task_id']}`\n"
        f"- Роль: `{task['owner']}`\n"
        f"- Изменённые файлы: {files}\n\n"
        f"## 2. REQ\n"
        f"- Покрытые `REQ`: {reqs}\n"
        f"- Не покрытые: нет\n\n"
        f"## 3. Проверка\n"
        f"```bash\npython3 -m unittest discover -s product/tests\n```\n"
        f"- Код возврата: `0`\n- Тесты: `3 passed`\n\n"
        f"## 4. Diff\n"
        f"```diff\n+ реализация задачи {task['task_id']}\n```\n\n"
        f"Статус: `[ТРЕБУЕТ ПРОВЕРКИ]` — `PASS` ставит только `02-independent-verifier`.\n"
    )


class FakeExecutor:
    """Writes the reference scenario; never calls a model or the network."""

    name = "fake"

    def __init__(
        self,
        scenario: str = "ok",
        fixture_root: Path | None = None,
        sleep_seconds: float = 0.0,
    ) -> None:
        if scenario not in SCENARIOS:
            raise RunError(f"unknown fake scenario {scenario!r}; choose from {sorted(SCENARIOS)}")
        self.scenario = scenario
        self.fixture_root = fixture_root or FIXTURE_ROOT
        self.sleep_seconds = sleep_seconds

    # -- writing -------------------------------------------------------
    def _fixture(self, relative: str) -> str | None:
        path = self.fixture_root / relative
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return None

    def _write(self, target: Path, text: str, run_dir: Path) -> str:
        allowed_root = run_dir if _within(target, run_dir) else project_root()
        if not _within(target, allowed_root):
            raise RunError(f"fake executor refused to write outside the run: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target.relative_to(allowed_root).as_posix()

    def _write_spec(
        self, spec: dict[str, Any], order: dict[str, Any], run_dir: Path, state: dict[str, Any]
    ) -> list[str]:
        artifact_id = str(spec.get("id") or "")
        raw = str(spec.get("resolved_path") or spec.get("path") or "")
        if artifact_id == "metrics":
            write_metrics(run_dir)
            return ["artifacts/metrics.json"]

        if spec.get("per_task"):
            task_id = str(order.get("task_id") or "")
            task = state["tasks"].get(task_id, {})
            text = self._fixture(f"tasks/{task_id}/deliverable.md") or _deliverable(task)
            return [self._write(Path(raw), text, run_dir)]

        if spec.get("per_pattern"):
            base = str(spec.get("path") or "")
            directory = base.rsplit("/", 1)[0]
            written: list[str] = []
            for source in sorted((self.fixture_root / directory).glob("*.md")):
                relative = f"{directory}/{source.name}"
                written.append(
                    self._write(run_dir / relative, source.read_text(encoding="utf-8"), run_dir)
                )
            if not written:
                stem = base.replace("{num}", "001")
                written.append(self._write(run_dir / stem, _template_fill(spec), run_dir))
            return written

        if spec.get("directory"):
            prefix = str(spec.get("path") or "")
            written = []
            for source in sorted((self.fixture_root / prefix).rglob("*")):
                if not source.is_file():
                    continue
                # An installed wheel byte-compiles package data, so the fixture
                # may contain __pycache__; those files are not artifacts.
                if "__pycache__" in source.parts or source.suffix in {".pyc", ".pyo"}:
                    continue
                relative = source.relative_to(self.fixture_root).as_posix()
                written.append(
                    self._write(run_dir / relative, source.read_text(encoding="utf-8"), run_dir)
                )
            return written

        if spec.get("root"):
            relative = str(spec.get("path") or raw)
            target = project_root() / relative
            text = self._fixture(relative) or _template_fill(spec)
            return [self._write(target, text, run_dir)]

        text = self._fixture(raw) or _template_fill(spec)
        return [self._write(run_dir / raw, text, run_dir)]

    # -- executor contract --------------------------------------------
    def execute(self, order: dict[str, Any], context: RunContext) -> ExecutionResult:
        from ..state import load_state

        state = load_state(context.run_dir)
        written: list[str] = []
        for spec in order.get("artifacts") or []:
            written.extend(self._write_spec(spec, order, context.run_dir, state))

        if self.scenario == "break-artifact" and written:
            broken = context.run_dir / written[0]
            broken.write_text(broken.read_text(encoding="utf-8") + "\n{{TODO}}\n", encoding="utf-8")

        if self.scenario == "write-state":
            target = context.run_dir / "state.json"
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        if self.scenario == "escape":
            outside = context.run_dir.parent / "escape.txt"
            outside.write_text("written outside the run\n", encoding="utf-8")

        if self.scenario == "slow" or self.sleep_seconds:
            time.sleep(self.sleep_seconds or 1.0)

        transcript = context.path_in_runtime()
        transcript.write_text(
            f"# fake executor, scenario={self.scenario}, step={context.step}\n"
            f"role: {context.role}\nstage: {context.stage}\n"
            "written:\n" + "\n".join(f"- {item}" for item in written) + "\n",
            encoding="utf-8",
        )
        return ExecutionResult(
            exit_code=0,
            stdout="\n".join(written),
            summary=f"fake executor wrote {len(written)} artifact path(s) for {context.role}",
            artifacts=written,
            usage={"tokens_total": None, "usd": None, "executor": self.name},
            transcript_path=str(transcript),
        )


def copy_fixture_tree(destination: Path) -> None:
    """Copy the reference artifacts into a run directory (used by tests)."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(FIXTURE_ROOT, destination)
