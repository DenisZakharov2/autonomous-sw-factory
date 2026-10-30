import argparse
import re
from pathlib import Path

from factory import __version__
from factory.cli import build_parser
from factory.config import load_artifacts

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {".git", ".venv", ".pytest_cache", ".ruff_cache", "build", "dist", "runs"}


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not EXCLUDED_DIRS & set(path.relative_to(ROOT).parts)
    )


def test_version_is_single_source():
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == __version__
    for name in ("README.md", "README.en.md", "DESIGN.md", "docs/quickstart.md"):
        assert __version__ in (ROOT / name).read_text(encoding="utf-8"), name


def test_docs_do_not_claim_an_older_current_version():
    for name in ("DESIGN.md", "docs/metrics.md", "docs/threat-model.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "0.1.0" not in text, f"{name} still calls 0.1.0 current"


def test_license_is_complete_mit():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Permission is hereby granted" in text
    assert "THE SOFTWARE IS PROVIDED" in text


def test_notice_is_separate_and_has_upstreams():
    text = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "DietrichGebert" in text
    assert "mattpocock" in text
    assert "SkillSpector" in text


def test_every_registered_artifact_has_a_template_or_is_a_directory():
    for artifact_id, spec in load_artifacts()["artifacts"].items():
        if spec.get("directory") or spec.get("schema"):
            continue
        assert spec.get("template"), f"{artifact_id} has no template"
        assert (ROOT / "factory" / spec["template"]).exists(), f"{artifact_id} template missing"


def test_markdown_links_are_not_broken():
    missing = []
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = match.group(1).split("#")[0]
            if not target or target.startswith(("http", "mailto:", "#")):
                continue
            if not (path.parent / target).resolve().exists():
                missing.append((path.relative_to(ROOT).as_posix(), target))
    assert missing == []


def test_readme_does_not_claim_full_autonomy():
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "не «полностью автономная фабрика" in text or "не полностью автономная" in text


def _subcommands() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def documented_commands() -> set[str]:
    """`factory <subcommand>` lines inside fenced code blocks only."""
    pattern = re.compile(r"^(?:\$ )?factory ([a-z][a-z-]*)\b")
    found: set[str] = set()
    for path in markdown_files():
        in_fence = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence:
                continue
            match = pattern.match(line.strip())
            if match:
                found.add(match.group(1))
    return found


def test_documented_factory_commands_exist():
    known = _subcommands()
    assert known, "the CLI parser exposes no subcommands"
    unknown = sorted(documented_commands() - known)
    assert unknown == [], f"documented commands missing from the CLI: {unknown}"


def test_docs_cover_the_main_commands():
    documented = documented_commands()
    for command in ("doctor", "init", "submit", "verify", "gate", "task", "quarantine"):
        assert command in documented, f"no code example for `factory {command}`"
