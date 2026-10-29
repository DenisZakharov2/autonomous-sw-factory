import re
from pathlib import Path

from factory import __version__
from factory.config import load_artifacts

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_single_source():
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == __version__
    assert __version__ in (ROOT / "README.md").read_text(encoding="utf-8")
    assert __version__ in (ROOT / "README.en.md").read_text(encoding="utf-8")


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
    for path in list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = match.group(1).split("#")[0]
            if not target or target.startswith(("http", "mailto:", "#")):
                continue
            if not (path.parent / target).resolve().exists():
                missing.append((path.name, target))
    assert missing == []


def test_readme_does_not_claim_full_autonomy():
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "не «полностью автономная фабрика" in text or "не полностью автономная" in text
