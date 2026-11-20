"""The staging step: an executor writes a copy, the host promotes it.

These tests need no bubblewrap.  They exercise the contract `run_loop` uses:
`_writable_entries` says what may change, `staging.prepare` makes the copy,
the executor writes into it, and `staging.promote` moves only the declared
paths into the run.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from factory.config import project_root
from factory.engine import init_run
from factory.runtime.base import RunContext, RuntimeFailure
from factory.runtime.fake import FakeExecutor
from factory.runtime.loop import _writable_entries
from factory.runtime.staging import (
    build_plan,
    cleanup,
    prepare,
    promote,
    staging_root,
)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("FACTORY_RUNS_ROOT", str(runs))
    init_run("run-01", "nano", root=runs, idea="staging probe")
    return tmp_path


def _prepared(project: Path, order: dict):
    run_dir = project / "runs" / "run-01"
    plan = build_plan(
        _writable_entries(order, run_dir, None),
        run_dir=run_dir,
        project=project_root(),
        staging=staging_root(run_dir),
        allow_project_root=True,
    )
    prepared = prepare(plan, run_dir=run_dir)
    return run_dir, plan, prepared


def _context(run_dir: Path, order: dict, prepared) -> RunContext:
    return RunContext(
        run_id="run-01",
        run_dir=run_dir,
        step=1,
        order=order,
        mode="nano",
        prompt="",
        runtime_dir=run_dir / "runtime",
        extra_staged=tuple((str(dest), str(src)) for dest, src in prepared.binds),
        staging_dir=staging_root(run_dir),
    )


def _cancel(project: Path, plan, prepared) -> None:
    os.environ.pop("FACTORY_ROOT", None)
    cleanup(prepared, run_dir=project / "runs" / "run-01")


def test_a_product_artifact_reaches_the_run_through_staging(project):
    order = {
        "action": "produce",
        "stage": "s4_build",
        "run_id": "run-01",
        "artifacts": [
            {
                "id": "product_src",
                "path": "product/src/",
                "owner": "12-fullstack-developer",
                "directory": True,
            }
        ],
        "pending_artifacts": [],
        "next_role": "12-fullstack-developer",
    }
    run_dir, plan, prepared = _prepared(project, order)
    product = run_dir / "product" / "src" / "todo.py"
    assert not product.exists()

    result = FakeExecutor().execute(order, _context(run_dir, order, prepared))
    assert result.ok
    assert not product.exists()
    staged = run_dir / "runtime" / "agent" / "out" / "product" / "src" / "todo.py"
    assert staged.is_file()

    promote(plan, run_dir=run_dir, project=project_root())
    assert product.is_file()
    assert "def " in product.read_text(encoding="utf-8")
    cleanup(prepared, run_dir=run_dir)
    assert not staging_root(run_dir).exists()
    os.environ.pop("FACTORY_ROOT", None)


def test_a_project_root_artifact_reaches_the_project_through_staging(project):
    order = {
        "action": "produce",
        "stage": "s15_quarantine",
        "run_id": "run-01",
        "artifacts": [
            {
                "id": "skills_lock",
                "path": "skills-lock.json",
                "owner": "00-orchestrator",
                "root": True,
            }
        ],
        "pending_artifacts": [],
        "next_role": "00-orchestrator",
    }
    run_dir, plan, prepared = _prepared(project, order)
    target = project / "skills-lock.json"
    assert not target.exists()

    result = FakeExecutor().execute(order, _context(run_dir, order, prepared))
    assert result.ok
    assert not target.exists()
    assert (run_dir / "runtime" / "agent" / "out" / "_root" / "skills-lock.json").is_file()

    promote(plan, run_dir=run_dir, project=project_root())
    assert target.is_file()
    cleanup(prepared, run_dir=run_dir)
    os.environ.pop("FACTORY_ROOT", None)


def test_a_symlink_in_staging_is_refused(project, tmp_path):
    order = {
        "action": "produce",
        "stage": "s4_build",
        "run_id": "run-01",
        "artifacts": [
            {
                "id": "product_src",
                "path": "product/src/",
                "owner": "12-fullstack-developer",
                "directory": True,
            }
        ],
    }
    run_dir, plan, prepared = _prepared(project, order)
    planted = run_dir / "runtime" / "agent" / "out" / "product" / "src" / "leak.py"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    try:
        planted.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    with pytest.raises(RuntimeFailure):
        promote(plan, run_dir=run_dir, project=project_root())
    assert not (run_dir / "product" / "src" / "leak.py").exists()
    cleanup(prepared, run_dir=run_dir)
    os.environ.pop("FACTORY_ROOT", None)


def test_a_hardlink_in_staging_is_refused(project):
    order = {
        "action": "produce",
        "stage": "s4_build",
        "run_id": "run-01",
        "artifacts": [
            {
                "id": "product_src",
                "path": "product/src/",
                "owner": "12-fullstack-developer",
                "directory": True,
            }
        ],
    }
    run_dir, plan, prepared = _prepared(project, order)
    staged = run_dir / "runtime" / "agent" / "out" / "product" / "src"
    try:
        os.link(run_dir / "state.json", staged / "state-link")
    except OSError:
        pytest.skip("this filesystem does not support hard links")

    with pytest.raises(RuntimeFailure):
        promote(plan, run_dir=run_dir, project=project_root())
    assert not (run_dir / "product" / "src" / "state-link").exists()
    cleanup(prepared, run_dir=run_dir)
    os.environ.pop("FACTORY_ROOT", None)
