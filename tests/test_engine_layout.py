"""The engine package keeps the names its callers import.

The split moves code, not the public surface: `factory.engine` re-exports
everything that `factory/cli.py`, `factory/eval.py`, `factory/runtime/loop.py`,
`factory/runtime/worktree.py` and the tests import from it.  These tests pin
the first slice — the verification cluster — and would fail if a later move
dropped a re-export.
"""
from __future__ import annotations

import factory.engine as engine


def test_the_verification_cluster_is_its_own_module():
    from factory.engine import verification

    for name in (
        "verification_report",
        "verification_is_stale",
        "record_verification",
        "stage_declares_verification",
    ):
        assert getattr(engine, name) is getattr(verification, name), name


def test_the_names_the_runtime_imports_from_the_package_are_present():
    for name in (
        "VERIFIER",
        "_model_for",
        "_task_deliverable_errors",
        "gate_approve",
        "gate_reject",
        "next_action",
        "submit_work",
        "task_accept",
        "task_claim",
        "task_reject",
        "verification_policy",
        "verification_report",
    ):
        assert hasattr(engine, name), name
