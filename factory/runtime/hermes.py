"""Hermes adapter: `hermes -z <prompt> --in <run_dir> --usage-file ...`.

Hermes runs one turn and exits, and `--usage-file` writes real token counts.
That matters twice: the factory can account for cost, and a provider error is
visible even when the CLI itself exits 0.  The adapter maps `failed: true` in
the usage file to a non-zero exit code so the loop stops instead of treating
an error message as an artifact.

Some providers reject a reasoning effort they do not implement (Nemotron, for
example, accepts only none/medium/high), so the adapter sends `medium` by
default instead of leaving the choice to the global config.

The adapter also narrows the toolset: a documentation stage writes files, so
`file` is enough; `s4_build` also needs `terminal` to run the product's check
command.  The full Hermes toolset adds ~16k tokens of schemas to *every* API
call of a step, which was the largest single line item in a real run (see
`docs/runtime.md`, §5.3).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .base import ExecutionResult, RunContext, safe_write_text
from .sandbox import sandbox_command, sandbox_env


def _read_usage(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


class HermesExecutor:
    name = "hermes"

    def __init__(
        self,
        *,
        binary: str = "hermes",
        model: str | None = None,
        reasoning: str = "medium",
        timeout: float = 1800.0,
        sandbox: bool = False,
        read_write: tuple[str, ...] = (),
        yolo: bool = False,
        toolset: str | None = "auto",
        skills: str | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.binary = binary
        self.model = model or os.environ.get("FACTORY_HERMES_MODEL")
        self.reasoning = reasoning
        self.timeout = timeout
        self.sandbox = sandbox
        self.read_write = tuple(read_write)
        self.yolo = yolo
        self.toolset = toolset
        self.skills = skills
        self.extra_args = tuple(extra_args)

    def toolset_for(self, stage: str | None) -> str | None:
        """The narrowest toolset that can do this stage's work.

        `s4_build` runs the product's check command and `s_in` runs the real
        backup/restore drill (`git bundle`, `tar`, a restore clone), so both
        keep `terminal`.  Every other stage produces Markdown/JSON, which the
        `file` toolset covers; loading web, browser, delegation and image
        schemas there costs tokens without changing the artifact.
        """
        if self.toolset and self.toolset.strip().lower() == "auto":
            return "file,terminal" if stage in {"s4_build", "s_in"} else "file"
        return self.toolset

    def command(
        self,
        prompt: str,
        run_dir: Path,
        usage_file: Path,
        extra_read_write: tuple[str, ...] = (),
        stage: str | None = None,
    ) -> list[str]:
        cmd = [self.binary, "-z", prompt, "--in", str(run_dir), "--usage-file", str(usage_file)]
        if self.model:
            cmd.extend(["-m", self.model])
        if self.reasoning:
            cmd.extend(["--reasoning", self.reasoning])
        toolset = self.toolset_for(stage)
        if toolset:
            cmd.extend(["-t", toolset])
        if self.skills is not None:
            cmd.extend(["--skills", self.skills])
        if self.yolo:
            cmd.append("--yolo")
        cmd.extend(self.extra_args)
        return sandbox_command(
            cmd, run_dir, read_write=self.read_write + tuple(extra_read_write), enabled=self.sandbox
        )

    def execute(self, order: dict[str, Any], context: RunContext) -> ExecutionResult:
        transcript = context.path_in_runtime()
        usage_path = context.path_in_executor(f"usage-{context.sequence or context.step}.json")
        cmd = self.command(
            context.prompt,
            context.run_dir,
            usage_path,
            extra_read_write=context.extra_read_write,
            stage=context.stage,
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(context.run_dir),
                env=sandbox_env(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            safe_write_text(
                transcript,
                f"# hermes timeout after {elapsed:.1f}s\n\n## stdout\n{stdout[-20000:]}"
                f"\n\n## stderr\n{stderr[-20000:]}\n",
            )
            return ExecutionResult(
                exit_code=124,
                stdout=stdout,
                stderr=f"hermes timed out after {self.timeout:.0f}s",
                summary="hermes timed out",
                usage={"seconds": round(elapsed, 3)},
                transcript_path=str(transcript),
            )
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=127,
                stderr=f"hermes binary not found: {self.binary}",
                summary="hermes is not installed",
            )
        elapsed = time.monotonic() - started
        usage_raw = _read_usage(usage_path)
        failed = bool(usage_raw.get("failed")) or usage_raw.get("completed") is False
        exit_code = completed.returncode
        if failed and exit_code == 0:
            exit_code = 1
        tokens = usage_raw.get("total_tokens")
        usage = {
            "seconds": round(elapsed, 3),
            "tokens_total": int(tokens) if isinstance(tokens, (int, float)) else None,
            "tokens_in": usage_raw.get("input_tokens"),
            "tokens_out": usage_raw.get("output_tokens"),
            "cache_read": usage_raw.get("cache_read_tokens"),
            "api_calls": usage_raw.get("api_calls"),
            "usd": usage_raw.get("estimated_cost_usd"),
            "model": usage_raw.get("model"),
        }
        display = [("<prompt>" if item == context.prompt else item) for item in cmd]
        safe_write_text(
            transcript,
            f"# hermes step {context.step} — {context.role} @ {context.stage}\n"
            f"exit_code: {exit_code}\ncommand: {' '.join(display)}\n\n"
            f"## usage\n{json.dumps(usage, ensure_ascii=False, indent=2)}\n\n"
            f"## stdout\n{completed.stdout[-40000:]}\n\n## stderr\n{completed.stderr[-20000:]}\n",
        )
        summary = (completed.stdout or "").strip()[-2000:] or (completed.stderr or "").strip()[-2000:]
        return ExecutionResult(
            exit_code=exit_code,
            stdout=completed.stdout,
            stderr=completed.stderr or ("" if not failed else "hermes reported a failed turn"),
            summary=summary,
            usage=usage,
            transcript_path=str(transcript),
        )
