"""SkillSpector quarantine gate.

The gate is deliberately fail-closed:
- a crashed or incomplete scan never installs;
- only a complete SAFE/LOW scan with enough coverage auto-allows;
- CAUTION goes to a human;
- DO_NOT_INSTALL or HIGH/CRITICAL is purged;
- the lock records a directory hash, so replacing a file is detectable.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import load_json, validate_instance
from .errors import ConfigError, QuarantineError
from .journal import utc_now

AUTO_ALLOW = "AUTO_ALLOW"
ESCALATE_HUMAN = "ESCALATE_HUMAN"
BLOCK_INCOMPLETE = "BLOCK_INCOMPLETE"
BLOCK_RETRY = "BLOCK_RETRY"
REJECT_AND_PURGE = "REJECT_AND_PURGE"


@dataclass
class ScanResult:
    recommendation: str = "DO_NOT_INSTALL"
    severity: str = "CRITICAL"
    is_complete: bool = False
    coverage_percent: float = 0.0
    failed_analyzers: list[str] = field(default_factory=list)
    scanner_version: str = "unknown"
    skill_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def parse_report(data: dict[str, Any]) -> ScanResult:
    risk = data.get("risk_assessment") or {}
    completeness = data.get("analysis_completeness") or {}
    statuses = completeness.get("analyzer_statuses") or []
    failed = [
        str(item.get("analyzer_id", "unknown"))
        for item in statuses
        if isinstance(item, dict) and item.get("status") in {"failed", "partial"}
    ]
    metadata = data.get("metadata") or {}
    skill = data.get("skill") or {}
    return ScanResult(
        recommendation=str(risk.get("recommendation", "DO_NOT_INSTALL")).upper(),
        severity=str(risk.get("severity", "CRITICAL")).upper(),
        is_complete=bool(completeness.get("is_complete", False)),
        coverage_percent=float(completeness.get("coverage_percent", 0.0) or 0.0),
        failed_analyzers=failed,
        scanner_version=str(metadata.get("skillspector_version", "unknown")),
        skill_name=str(skill.get("name", "")),
        raw=data,
    )


def decide(exit_code: int, result: ScanResult, min_coverage: float = 95.0) -> str:
    if not result.is_complete or result.failed_analyzers:
        return BLOCK_INCOMPLETE
    if exit_code != 0:
        # A scan that did not finish cleanly is not evidence of safety.
        return BLOCK_RETRY
    if result.recommendation == "DO_NOT_INSTALL" or result.severity in {"HIGH", "CRITICAL"}:
        return REJECT_AND_PURGE
    if result.coverage_percent < min_coverage:
        return BLOCK_INCOMPLETE
    if (
        result.recommendation == "SAFE"
        and result.severity == "LOW"
        and result.coverage_percent >= min_coverage
    ):
        return AUTO_ALLOW
    if result.recommendation == "CAUTION":
        return ESCALATE_HUMAN
    return REJECT_AND_PURGE


def scan_skill(
    path: Path,
    *,
    scanner: str = "skillspector",
    min_coverage: float = 95.0,
    no_llm: bool = False,
    timeout: int = 600,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> tuple[int, ScanResult, str]:
    path = path.resolve()
    if not path.exists():
        raise QuarantineError(f"skill path does not exist: {path}")
    with tempfile.TemporaryDirectory(prefix="factory-scan-") as tmp:
        report_path = Path(tmp) / "report.json"
        command = [
            scanner, "scan", str(path),
            "--format", "json",
            "--output", str(report_path),
            "--fail-on-incomplete",
            "--min-coverage", str(min_coverage),
        ]
        if no_llm:
            command.append("--no-llm")
        run = runner or subprocess.run
        try:
            completed = run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError as exc:
            raise QuarantineError(f"scanner not found: {scanner}") from exc
        except subprocess.TimeoutExpired as exc:
            raise QuarantineError(f"scanner timed out after {timeout}s") from exc
        output = (completed.stdout or "") + (completed.stderr or "")
        if not report_path.exists():
            raise QuarantineError(f"scanner produced no JSON report; output:\n{output[-2000:]}")
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise QuarantineError(f"scanner report is not valid JSON: {exc}") from exc
        return int(completed.returncode), parse_report(data), output


def hash_skill_dir(path: Path) -> str:
    """Hash relative paths plus file bytes; order is deterministic."""
    import hashlib

    digest = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file())
    if not files:
        raise QuarantineError(f"skill directory is empty: {path}")
    for file in files:
        digest.update(str(file.relative_to(path)).replace(os.sep, "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def make_lock_entry(
    *,
    name: str,
    pack: str,
    commit: str,
    path: str,
    skill_dir: Path,
    license: str,
    model_invocable: bool,
    result: ScanResult,
    approved_by: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "pack": pack,
        "commit": commit or "local",
        "path": path.replace(os.sep, "/"),
        "sha256": hash_skill_dir(skill_dir),
        "license": license,
        "model_invocable": bool(model_invocable),
        "scanner": {
            "name": "skillspector",
            "version": result.scanner_version,
            "severity": result.severity,
            "recommendation": result.recommendation,
            "is_complete": result.is_complete,
            "coverage_percent": result.coverage_percent,
            "scanned_at": utc_now(),
        },
        "approved_by": approved_by,
    }


def load_lock(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1.0.0", "skills": []}
    data = load_json(path)
    schema = load_json(Path(__file__).resolve().parent / "schemas" / "skills-lock.schema.json")
    validate_instance(data, schema, str(path))
    return data


def write_lock(path: Path, data: dict[str, Any]) -> None:
    schema = load_json(Path(__file__).resolve().parent / "schemas" / "skills-lock.schema.json")
    validate_instance(data, schema, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def add_lock_entry(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    data = load_lock(path)
    data["skills"] = [item for item in data["skills"] if item.get("name") != entry["name"]]
    data["skills"].append(entry)
    write_lock(path, data)
    return data


def verify_lock(path: Path, skills_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    try:
        data = load_lock(path)
    except ConfigError as exc:
        return [str(exc)]
    root = (skills_root or path.parent).resolve()
    for entry in data.get("skills", []):
        rel = Path(str(entry.get("path", "")))
        candidate = (root / rel).resolve()
        if root not in candidate.parents and candidate != root:
            errors.append(f"{entry.get('name')}: path escapes skills root")
            continue
        if not candidate.exists():
            errors.append(f"{entry.get('name')}: locked path does not exist: {candidate}")
            continue
        try:
            actual = hash_skill_dir(candidate)
        except QuarantineError as exc:
            errors.append(str(exc))
            continue
        if actual != entry.get("sha256"):
            errors.append(f"{entry.get('name')}: sha256 mismatch (file substituted or changed)")
        scanner = entry.get("scanner") or {}
        if not entry.get("commit") or entry.get("commit") == "":
            errors.append(f"{entry.get('name')}: missing commit")
        if scanner.get("recommendation") != "SAFE" or not scanner.get("is_complete"):
            errors.append(f"{entry.get('name')}: not approved by a complete SAFE scan")
    return errors


def quarantine_skill(
    skill_dir: Path,
    *,
    name: str,
    pack: str,
    commit: str,
    license: str,
    lock_path: Path,
    approved_by: str = "human:pending",
    scanner: str = "skillspector",
    min_coverage: float = 95.0,
    no_llm: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> tuple[str, ScanResult, dict[str, Any] | None]:
    exit_code, result, _ = scan_skill(
        skill_dir, scanner=scanner, min_coverage=min_coverage, no_llm=no_llm, runner=runner
    )
    action = decide(exit_code, result, min_coverage)
    entry = None
    if action == AUTO_ALLOW:
        try:
            relative = Path(os.path.relpath(skill_dir, lock_path.parent))
        except ValueError:
            relative = Path(skill_dir)
        entry = make_lock_entry(
            name=name, pack=pack, commit=commit, path=str(relative), skill_dir=skill_dir,
            license=license, model_invocable=True, result=result, approved_by=approved_by,
        )
        add_lock_entry(lock_path, entry)
    return action, result, entry
