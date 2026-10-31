"""Seed trees for brownfield eval cases.

A brownfield case starts from an existing repository, but `init_run` creates
an empty run directory.  The seed is a pinned tar.gz with real `.git` history:
a plain `seed/` directory cannot be committed (git turns a nested repository
into a submodule link), and the restore drill needs history to be meaningful.
"""
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

import factory.eval as eval_module
from factory.config import ConfigError
from factory.eval import _seed_into, load_case, validate_cases
from factory.runtime.fake import FakeExecutor

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "cases"


def _case(tmp_path: Path, name: str = "seeded") -> Path:
    case_dir = tmp_path / name
    shutil.copytree(CASES / "nano-todo", case_dir)
    (case_dir / "case.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "id": name,
                "mode": "brownfield",
                "title": "Seeded brownfield case",
                "max_steps": 5,
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "expected.json").write_text(
        json.dumps({"schema_version": "1.0.0", "status": "complete"}),
        encoding="utf-8",
    )
    shutil.rmtree(case_dir / "checks", ignore_errors=True)
    return case_dir


def _write_tar(path: Path, entries: dict[str, bytes | None], links: dict[str, str] | None = None) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, payload in entries.items():
            if payload is None:
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
                continue
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(payload))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)


def _pin(case_dir: Path, archive: Path, *, commit: str | None = None, require_git: bool = False) -> None:
    seed_dir = case_dir / "seed"
    seed_dir.mkdir()
    shutil.copy2(archive, seed_dir / "repo.tar.gz")
    import hashlib

    digest = hashlib.sha256((seed_dir / "repo.tar.gz").read_bytes()).hexdigest()
    manifest = {
        "schema_version": "1.0.0",
        "source": "https://example.invalid/repo",
        "commit": commit or "0" * 40,
        "archive": "repo.tar.gz",
        "sha256": digest,
        "require_git": require_git,
    }
    (seed_dir / "seed.json").write_text(json.dumps(manifest), encoding="utf-8")


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def test_seed_is_extracted_and_keeps_git_history(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "source"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "eval@example.invalid", cwd=repo)
    _git("config", "user.name", "Eval", cwd=repo)
    (repo / "Cargo.toml").write_text("[package]\nname = \"demo\"\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "seed", cwd=repo)
    commit = _git("rev-parse", "HEAD", cwd=repo)

    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(repo, arcname=".")

    case_dir = _case(tmp_path)
    _pin(case_dir, archive, commit=commit, require_git=True)
    bundle = load_case(case_dir)
    assert bundle["seed"]["manifest"]["commit"] == commit

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "idea.md").write_text("idea\n", encoding="utf-8")
    _seed_into(run_dir, bundle["seed"])

    assert (run_dir / "Cargo.toml").is_file()
    assert (run_dir / ".git" / "HEAD").is_file()
    assert _git("rev-parse", "HEAD", cwd=run_dir) == commit


def test_seed_hash_mismatch_is_a_case_error(tmp_path):
    archive = tmp_path / "repo.tar.gz"
    _write_tar(archive, {"Cargo.toml": b"x\n"})
    case_dir = _case(tmp_path)
    _pin(case_dir, archive)
    manifest_file = case_dir / "seed" / "seed.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["sha256"] = "f" * 64
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ConfigError, match="sha256 mismatch"):
        load_case(case_dir)


def test_seed_cannot_overwrite_the_control_plane(tmp_path):
    archive = tmp_path / "repo.tar.gz"
    _write_tar(archive, {"state.json": b"{}\n", "Cargo.toml": b"x\n"})
    case_dir = _case(tmp_path)
    _pin(case_dir, archive)
    bundle = load_case(case_dir)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(ConfigError, match="control plane"):
        _seed_into(run_dir, bundle["seed"])
    assert not (run_dir / "state.json").exists()


def test_seed_rejects_path_traversal_and_escaping_links(tmp_path):
    archive = tmp_path / "repo.tar.gz"
    _write_tar(archive, {"Cargo.toml": b"x\n"})
    case_dir = _case(tmp_path)
    _pin(case_dir, archive)
    bundle = load_case(case_dir)

    traversal = tmp_path / "traversal.tar.gz"
    _write_tar(traversal, {"../evil.txt": b"x\n"})
    with pytest.raises(ConfigError, match="escapes"):
        _seed_into(tmp_path / "run", {"manifest": bundle["seed"]["manifest"], "archive": traversal})

    link = tmp_path / "link.tar.gz"
    _write_tar(link, {"Cargo.toml": b"x\n"}, links={"escape": "/etc/passwd"})
    with pytest.raises(ConfigError, match="link escapes"):
        _seed_into(tmp_path / "run2", {"manifest": bundle["seed"]["manifest"], "archive": link})


def test_doctor_reports_a_seed_without_git_history(tmp_path):
    archive = tmp_path / "repo.tar.gz"
    _write_tar(archive, {"Cargo.toml": b"x\n"})
    case_dir = _case(tmp_path)
    _pin(case_dir, archive, require_git=True)
    errors = validate_cases(tmp_path)
    assert any("no .git/HEAD" in error for error in errors), errors


def test_run_case_extracts_the_seed_before_the_loop(tmp_path, monkeypatch):
    archive = tmp_path / "repo.tar.gz"
    _write_tar(archive, {"Cargo.toml": b"x\n", "src/lib.rs": b"// seed\n"})
    case_dir = _case(tmp_path, "seeded-run")
    _pin(case_dir, archive)
    bundle = load_case(case_dir)

    seen = {}

    def fake_drive(run_id, executor, root, *, max_steps, max_cycles, budget):
        path = root / run_id
        seen["cargo"] = (path / "Cargo.toml").is_file()
        seen["lib"] = (path / "src" / "lib.rs").read_text(encoding="utf-8")
        return {"status": "complete", "code": 0, "message": "stopped by test", "loop_steps": 0}

    monkeypatch.setattr(eval_module, "_drive_run", fake_drive)
    eval_module.run_case(bundle, FakeExecutor(), tmp_path / "runs", "seed")
    assert seen["cargo"] is True
    assert seen["lib"] == "// seed\n"
