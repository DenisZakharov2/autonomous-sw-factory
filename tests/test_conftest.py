"""The suite must not depend on where the operator pointed `FACTORY_ROOT`.

A developer with `FACTORY_ROOT=~/work/another-project` in the shell got a
different suite than CI: some tests read the ambient root instead of their
`tmp_path`. The autouse fixture hides both roots for every test; these tests
pin that it does, and that it puts the operator's values back.
"""
from __future__ import annotations

import os

from conftest import _hidden_factory_env


def test_the_suite_hides_the_operator_roots(monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", "/tmp/operator-root")
    monkeypatch.setenv("FACTORY_RUNS_ROOT", "/tmp/operator-runs")

    with _hidden_factory_env():
        assert "FACTORY_ROOT" not in os.environ
        assert "FACTORY_RUNS_ROOT" not in os.environ

    assert os.environ["FACTORY_ROOT"] == "/tmp/operator-root"
    assert os.environ["FACTORY_RUNS_ROOT"] == "/tmp/operator-runs"


def test_the_autouse_fixture_is_in_effect():
    """Runs under the fixture: the ambient roots must already be gone."""
    assert "FACTORY_ROOT" not in os.environ
    assert "FACTORY_RUNS_ROOT" not in os.environ
