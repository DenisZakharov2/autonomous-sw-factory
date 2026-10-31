"""Evidence that the backup/restore drill was real, not a template.

A fake-contract run writes no tokens and no backups, so the drill is skipped:
there is no product to restore.  A real run must have a git bundle, a worktree
archive, a restore clone with the same HEAD as the seeded tree, and a PASS act.
"""
import json
import subprocess
from pathlib import Path

import pytest

RUN = Path.cwd()
PINNED_COMMIT = "dfe54336fed20cbed9ba12e6ff8be1f7877346f0"


def _rows():
    path = RUN / "runtime" / "usage.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_restore_drill_is_real():
    if not any(row.get("tokens_total") for row in _rows()):
        pytest.skip("contract-only run: no model tokens, no real product changes")

    tree = RUN
    backups = tree / "backups"
    bundle = backups / "pre-factory.bundle"
    archive = backups / "dirty-worktree.tar.gz"
    manifest = backups / "manifest.sha256"
    assert bundle.is_file(), "backups/pre-factory.bundle is missing"
    assert archive.is_file(), "backups/dirty-worktree.tar.gz is missing"
    assert manifest.is_file(), "backups/manifest.sha256 is missing"

    verified = _git("bundle", "verify", str(bundle), cwd=tree)
    assert verified.returncode == 0, verified.stderr

    head = _git("rev-parse", "HEAD", cwd=tree)
    assert head.returncode == 0, head.stderr
    assert head.stdout.strip() == PINNED_COMMIT, "the seeded tree was moved"

    restore = backups / "restore-check"
    if not (restore / ".git").is_dir():
        candidates = [path for path in backups.iterdir() if (path / ".git").is_dir()]
        assert candidates, "no restore clone with .git inside backups/"
        restore = candidates[0]
    restored = _git("rev-parse", "HEAD", cwd=restore)
    assert restored.returncode == 0, restored.stderr
    assert restored.stdout.strip() == PINNED_COMMIT, "restore clone is not the seeded commit"
    status = _git("status", "--porcelain", cwd=restore)
    assert status.returncode == 0 and not status.stdout.strip(), "restore clone is dirty"

    act = (tree / "artifacts" / "restore-drill.md").read_text(encoding="utf-8")
    assert "PASS" in act, "restore-drill.md was never marked PASS"
