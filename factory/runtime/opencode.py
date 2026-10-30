"""OpenCode adapter: `opencode run --format json ...`.

OpenCode v2 exposes two non-interactive entry points: the global
`--prompt ... --auto` form (which still opens the TUI scaffolding) and
`opencode run`, which streams JSON events and exits.  The adapter uses `run`
by default because a factory step must terminate on its own; `prompt` mode is
kept for older builds where `run` is unavailable.

The hard timeout is not optional: a build with no provider configured hangs
instead of failing, and an unbounded factory step would hang the whole run.
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


def _parse_usage(stdout: str) -> dict[str, Any]:
    """Best-effort: OpenCode's JSON events may carry tokens and cost."""
    tokens_in = tokens_out = 0
    usd: float | None = None
    seen_tokens = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else event
        usage = part.get("usage") if isinstance(part, dict) else None
        if not isinstance(usage, dict):
            usage = event.get("tokens") if isinstance(event.get("tokens"), dict) else None
        if isinstance(usage, dict):
            for key in ("input", "input_tokens", "prompt_tokens"):
                if isinstance(usage.get(key), (int, float)):
                    tokens_in += int(usage[key])
                    seen_tokens = True
            for key in ("output", "output_tokens", "completion_tokens"):
                if isinstance(usage.get(key), (int, float)):
                    tokens_out += int(usage[key])
                    seen_tokens = True
        cost = part.get("cost") if isinstance(part, dict) else None
        if isinstance(cost, (int, float)):
            usd = (usd or 0.0) + float(cost)
    return {
        "tokens_total": (tokens_in + tokens_out) if seen_tokens else None,
        "tokens_in": tokens_in if seen_tokens else None,
        "tokens_out": tokens_out if seen_tokens else None,
        "usd": usd,
    }


def _error_event(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error":
            error = event.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            return str(message or "opencode reported an error")
    return None


class OpenCodeExecutor:
    name = "opencode"

    def __init__(
        self,
        *,
        binary: str = "opencode",
        model: str | None = None,
        mode: str = "run",
        timeout: float = 1800.0,
        sandbox: bool = False,
        read_write: tuple[str, ...] = (),
        extra_args: tuple[str, ...] = (),
    ) -> None:
        if mode not in {"run", "prompt"}:
            raise ValueError("opencode mode must be 'run' or 'prompt'")
        self.binary = binary
        self.model = model or os.environ.get("FACTORY_OPENCODE_MODEL")
        self.mode = mode
        self.timeout = timeout
        self.sandbox = sandbox
        self.read_write = tuple(read_write)
        self.extra_args = tuple(extra_args)

    def command(
        self, prompt: str, run_dir: Path, extra_read_write: tuple[str, ...] = ()
    ) -> list[str]:
        if self.mode == "prompt":
            cmd = [self.binary, "--prompt", prompt, "--auto", "--standalone"]
            cmd.extend(self.extra_args)
            cmd.append(str(run_dir))
        else:
            cmd = [self.binary, "run", "--auto", "--standalone", "--format", "json"]
            if self.model:
                cmd.extend(["--model", self.model])
            cmd.extend(self.extra_args)
            cmd.append(prompt)
        return sandbox_command(
            cmd, run_dir, read_write=self.read_write + tuple(extra_read_write), enabled=self.sandbox
        )

    def execute(self, order: dict[str, Any], context: RunContext) -> ExecutionResult:
        transcript = context.path_in_runtime()
        cmd = self.command(context.prompt, context.run_dir, extra_read_write=context.extra_read_write)
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
                f"# opencode timeout after {elapsed:.1f}s\n\n## stdout\n{stdout[-20000:]}"
                f"\n\n## stderr\n{stderr[-20000:]}\n",
            )
            return ExecutionResult(
                exit_code=124,
                stdout=stdout,
                stderr=f"opencode timed out after {self.timeout:.0f}s",
                summary="opencode timed out",
                usage={"seconds": round(elapsed, 3)},
                transcript_path=str(transcript),
            )
        except FileNotFoundError:
            return ExecutionResult(
                exit_code=127,
                stderr=f"opencode binary not found: {self.binary}",
                summary="opencode is not installed",
            )
        elapsed = time.monotonic() - started
        error = _error_event(completed.stdout)
        exit_code = completed.returncode
        if error and exit_code == 0:
            exit_code = 1
        usage = _parse_usage(completed.stdout)
        usage["seconds"] = round(elapsed, 3)
        display = [("<prompt>" if item == context.prompt else item) for item in cmd]
        last_line = ""
        if completed.stdout.strip():
            last_line = completed.stdout.strip().splitlines()[-1][:500]
        safe_write_text(
            transcript,
            f"# opencode ({self.mode}) step {context.step} — {context.role} @ {context.stage}\n"
            f"exit_code: {exit_code}\ncommand: {' '.join(display)}\n\n"
            f"## stdout\n{completed.stdout[-40000:]}\n\n## stderr\n{completed.stderr[-20000:]}\n",
        )
        summary = error or last_line or f"opencode exited with {exit_code}"
        return ExecutionResult(
            exit_code=exit_code,
            stdout=completed.stdout,
            stderr=completed.stderr or (error or ""),
            summary=str(summary)[:2000],
            usage=usage,
            transcript_path=str(transcript),
        )
