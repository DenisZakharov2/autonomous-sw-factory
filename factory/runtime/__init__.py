"""Executable runtime: work orders in, verified artifacts out.

`factory run` wires one of these executors into `run_loop`:

- `FakeExecutor` writes the reference `nano` scenario and needs no model;
- `OpenCodeExecutor` shells out to `opencode --prompt ... --auto`;
- `HermesExecutor` shells out to `hermes -z ... --in ...`.
"""
from __future__ import annotations

from .base import ExecutionResult, Executor, RunContext, RuntimeFailure
from .loop import LoopResult, run_loop

__all__ = [
    "ExecutionResult",
    "Executor",
    "LoopResult",
    "RunContext",
    "RuntimeFailure",
    "run_loop",
]
