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
