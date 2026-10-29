
import pytest

from factory.engine import init_run
from factory.errors import RunError
from factory.paths import runs_root, validate_run_id


@pytest.mark.parametrize("bad", ["../outside", "/tmp/absolute", "", "Upper", "with space", "a/b", ".hidden", "-leading"])
def test_bad_run_ids_rejected(bad):
    with pytest.raises(RunError):
        validate_run_id(bad)


@pytest.mark.parametrize("good", ["run-01", "a", "demo-2026", "x" * 63])
def test_good_run_ids_accepted(good):
    assert validate_run_id(good) == good


def test_run_dir_stays_inside_root(tmp_path):
    root = runs_root(tmp_path)
    assert (root / "run-01").parent == root


def test_init_refuses_existing_run(nano_run, tmp_path):
    with pytest.raises(RunError, match="already exists"):
        init_run("run-01", "nano", root=tmp_path)


def test_init_force_archives_previous_run(nano_run, tmp_path):
    run_dir, _, _ = nano_run
    (run_dir / "artifacts" / "note.md").write_text("old", encoding="utf-8")
    new_dir, state, event = init_run("run-01", "nano", root=tmp_path, force=True)
    assert new_dir == run_dir
    assert state["event_count"] == 1
    archives = list((tmp_path / ".archive").glob("run-01-*"))
    assert archives and (archives[0] / "artifacts" / "note.md").read_text() == "old"
    assert event["event_type"] == "INIT"


def test_no_absolute_or_traversal_write(tmp_path):
    with pytest.raises(RunError):
        init_run("../escape", "nano", root=tmp_path)
    assert not (tmp_path.parent / "escape").exists()
