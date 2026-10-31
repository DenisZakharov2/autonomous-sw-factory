"""External eval set: fixed cases, invariants and a comparable baseline.

`verify` answers "is this run intact".  It does not answer "did the runtime get
better".  An eval case fixes the invariants of one run (artifacts, REQ parity,
build tasks, closed gates, runnable product tests) plus the numbers that make
two runs comparable (work steps, journal events, rework, billable tokens, usd,
seconds).  Text is never compared: it depends on the model.

Every case runs in its own `runs/<case>-<stamp>/` with `FACTORY_ROOT` pointed
at that directory, so a root artifact such as `skills-lock.json` stays inside
the run instead of overwriting the repository copy.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .artifacts import artifact_path, pattern_paths
from .config import load_artifacts, load_json, load_pipeline, validate_against_schema
from .engine import gate_approve, init_run
from .errors import ConfigError, FactoryError
from .journal import read_events
from .metrics import compute_metrics
from .paths import run_dir as safe_run_dir
from .paths import runs_root as default_runs_root
from .paths import validate_run_id
from .runtime.base import Executor
from .runtime.loop import run_loop
from .runtime.usage import UsageLedger
from .state import load_state
from .trace import build_matrix
from .verify import verify_run

EVAL_APPROVER = "human:eval"
CONSTRAINTS_NAME = "constraints.md"
SEED_DIR = "seed"
SEED_MANIFEST = "seed.json"
SEED_SCHEMA = "eval-seed.schema.json"
# Names owned by the run's control plane.  A seed is a product tree, never a
# second state store: an archive carrying one of these would either overwrite
# the journal or be silently ignored, so extraction refuses it outright.
SEED_PROTECTED = (
    "state.json",
    "blackbox.jsonl",
    "blackbox.lock",
    "idea.md",
    CONSTRAINTS_NAME,
    "AUDIT_TRAIL.md",
    "checkpoint.json",
    "runtime",
    "checks",
)
DEFAULT_MAX_STEPS = 200
DEFAULT_MAX_CYCLES = 20
DEFAULT_THRESHOLDS = {
    "steps_growth": 0,
    "events_fall": 0,
    "tokens_growth": 0.10,
    "usd_growth": 0.10,
}
PASSED_RE = re.compile(r"(\d+)\s+passed\b")
RAN_RE = re.compile(r"Ran\s+(\d+)\s+tests?\b")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalised_member(name: str) -> PurePosixPath:
    """Archive paths are POSIX; a leading `./` is noise, not a directory."""
    return PurePosixPath(name[2:] if name.startswith("./") else name)


def _seed_members(archive: Path) -> list[tarfile.TarInfo]:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            return tar.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise ConfigError(f"cannot read seed archive {archive}: {exc}") from exc


def _case_seed(case_dir: Path) -> dict[str, Any] | None:
    """Optional seed tree shipped with the case.

    Brownfield starts from an existing repository, and `init_run` only creates
    an empty run directory.  The seed is a tarball, not a plain directory: a
    nested `.git` cannot be committed to the factory repository (git turns it
    into a submodule link), while the restore drill needs real history.  The
    manifest pins the source commit and the archive hash, so a case cannot
    silently drift from the tree it was measured on.
    """
    seed_dir = case_dir / SEED_DIR
    if not seed_dir.is_dir():
        return None
    manifest_file = seed_dir / SEED_MANIFEST
    if not manifest_file.is_file():
        raise ConfigError(f"missing {SEED_MANIFEST} in {seed_dir}")
    manifest = load_json(manifest_file)
    validate_against_schema(manifest, SEED_SCHEMA, str(manifest_file))
    archive = seed_dir / manifest["archive"]
    if not archive.is_file():
        raise ConfigError(f"seed archive not found: {archive}")
    actual = _sha256_file(archive)
    if actual != manifest["sha256"]:
        raise ConfigError(
            f"seed archive sha256 mismatch for {archive}: archive is {actual}, "
            f"manifest says {manifest['sha256']}"
        )
    return {"dir": seed_dir, "manifest": manifest, "archive": archive}


def _seed_errors(seed: dict[str, Any]) -> list[str]:
    """Structural checks `factory doctor` can run without extracting."""
    errors: list[str] = []
    members = _seed_members(seed["archive"])
    if not members:
        return [f"seed archive is empty: {seed['archive']}"]
    names = {_normalised_member(member.name).as_posix() for member in members}
    if seed["manifest"].get("require_git"):
        if ".git/HEAD" not in names:
            errors.append("seed archive has no .git/HEAD, so the restore drill has no history")
    for member in members:
        path = _normalised_member(member.name)
        if path.is_absolute() or ".." in path.parts:
            errors.append(f"seed archive member escapes the run: {member.name}")
        if member.issym() or member.islnk():
            target = PurePosixPath(member.linkname)
            if target.is_absolute() or ".." in target.parts:
                errors.append(f"seed archive link escapes the run: {member.name} -> {member.linkname}")
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            errors.append(f"seed archive contains a special file: {member.name}")
    return errors


def _seed_into(run_dir: Path, seed: dict[str, Any]) -> None:
    """Extract the case seed into a freshly initialised run directory."""
    errors = _seed_errors(seed)
    if errors:
        raise ConfigError("seed cannot be used: " + "; ".join(errors))
    members: list[tarfile.TarInfo] = []
    for member in _seed_members(seed["archive"]):
        path = _normalised_member(member.name)
        if not path.parts or path.as_posix() == ".":
            continue
        if path.parts[0] in SEED_PROTECTED:
            raise ConfigError(
                f"seed archive member {member.name!r} would overwrite the run control plane"
            )
        members.append(member)
    with tarfile.open(seed["archive"], "r:gz") as tar:
        try:
            tar.extractall(run_dir, members=members, filter="data")
        except TypeError:  # Python < 3.12 before the security backport
            tar.extractall(run_dir, members=members)
    manifest = seed["manifest"]
    if not manifest.get("require_git"):
        return
    if not (run_dir / ".git").is_dir():
        raise ConfigError("seed archive did not produce .git in the run directory")
    git = shutil.which("git")
    if git is None:
        raise ConfigError("seed requires git history but git is not installed")
    completed = subprocess.run(
        [git, "-C", str(run_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    actual = (completed.stdout or "").strip()
    if completed.returncode != 0 or actual != manifest["commit"]:
        raise ConfigError(
            f"seed HEAD is {actual or 'unreadable'!r}, manifest pins {manifest['commit']!r}"
        )


def _case_constraints(case_dir: Path) -> str | None:
    """Optional frame of the case: how many REQ, which tasks, who submits.

    The case owns its limits; the mode prompt stays generic.  A blank file is
    the same as no file, so no empty section ever reaches a prompt.
    """
    path = case_dir / CONSTRAINTS_NAME
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {CONSTRAINTS_NAME} in {case_dir}: {exc}") from exc
    return text if text.strip() else None


def load_case(case_dir: Path) -> dict[str, Any]:
    """Load and validate one case; every missing or malformed part is an error."""
    case_file = case_dir / "case.json"
    if not case_file.is_file():
        raise ConfigError(f"missing case.json in {case_dir}")
    case = load_json(case_file)
    validate_against_schema(case, "eval-case.schema.json", str(case_file))
    if case["id"] != case_dir.name:
        raise ConfigError(f"case id {case['id']!r} does not match directory {case_dir.name!r}")
    validate_run_id(case["id"])
    idea_file = case_dir / "idea.md"
    if not idea_file.is_file():
        raise ConfigError(f"missing idea.md in {case_dir}")
    idea = idea_file.read_text(encoding="utf-8").strip()
    if not idea:
        raise ConfigError(f"idea.md is empty in {case_dir}")
    expected_file = case_dir / "expected.json"
    if not expected_file.is_file():
        raise ConfigError(f"missing expected.json in {case_dir}")
    expected = load_json(expected_file)
    validate_against_schema(expected, "eval-expected.schema.json", str(expected_file))
    checks_dir = case_dir / "checks"
    return {
        "dir": case_dir,
        "case": case,
        "idea": idea + "\n",
        "expected": expected,
        "constraints": _case_constraints(case_dir),
        "seed": _case_seed(case_dir),
        "checks": checks_dir if checks_dir.is_dir() else None,
    }


def load_cases(cases_dir: Path, only: set[str] | None = None) -> list[dict[str, Any]]:
    if not cases_dir.is_dir():
        raise ConfigError(f"eval cases directory not found: {cases_dir}")
    directories = sorted(path for path in cases_dir.iterdir() if path.is_dir())
    if only:
        unknown = only - {path.name for path in directories}
        if unknown:
            raise ConfigError(f"unknown eval case id(s): {sorted(unknown)}")
        directories = [path for path in directories if path.name in only]
    if not directories:
        raise ConfigError(f"no eval cases found in {cases_dir}")
    bundles = [load_case(path) for path in directories]
    seen: set[str] = set()
    for bundle in bundles:
        case_id = bundle["case"]["id"]
        if case_id in seen:
            raise ConfigError(f"duplicate eval case id: {case_id}")
        seen.add(case_id)
    return bundles


def validate_cases(cases_dir: Path) -> list[str]:
    """Non-throwing variant used by `factory doctor`."""
    errors: list[str] = []
    if not cases_dir.is_dir():
        return [f"eval cases directory not found: {cases_dir}"]
    directories = sorted(path for path in cases_dir.iterdir() if path.is_dir())
    if not directories:
        return [f"no eval cases found in {cases_dir}"]
    seen: set[str] = set()
    for path in directories:
        try:
            bundle = load_case(path)
        except FactoryError as exc:
            errors.append(f"eval case {path.name}: {exc}")
            continue
        case_id = bundle["case"]["id"]
        if case_id in seen:
            errors.append(f"eval case {path.name}: duplicate id {case_id}")
        seen.add(case_id)
        if bundle["seed"] is not None:
            errors.extend(f"eval case {path.name}: {error}" for error in _seed_errors(bundle["seed"]))
    return errors


@contextlib.contextmanager
def _factory_root(path: Path) -> Iterator[None]:
    """Point root artifacts at the run for the duration of one case."""
    previous = os.environ.get("FACTORY_ROOT")
    os.environ["FACTORY_ROOT"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("FACTORY_ROOT", None)
        else:
            os.environ["FACTORY_ROOT"] = previous


def _budget_for(case: dict[str, Any], mode_budget: dict[str, Any] | None) -> dict[str, Any]:
    budget = dict(mode_budget or {})
    for key, value in (case.get("budget") or {}).items():
        if value is not None:
            budget[key] = value
    return budget


def _case_run_id(case_id: str, stamp: str) -> str:
    run_id = f"{case_id}-{stamp}"
    validate_run_id(run_id)
    return run_id


def _drive_run(
    run_id: str,
    executor: Executor,
    root: Path,
    *,
    max_steps: int,
    max_cycles: int,
    budget: dict[str, Any],
) -> dict[str, Any]:
    """Run the loop, crossing human gates as `human:eval`.

    The eval harness is not a human: it records `human:eval` so the journal
    says exactly who closed a gate, and the report repeats it.  A real release
    decision still needs a real `human:<id>`.
    """
    loop_steps = 0
    last = None
    for _ in range(max_cycles):
        remaining = max_steps - loop_steps
        if remaining <= 0:
            break
        last = run_loop(run_id, executor, max_steps=remaining, root=root, budget=budget)
        loop_steps += last.steps
        if last.code != 2:
            break
        if not last.order or not last.order.get("gate"):
            raise FactoryError("human gate stop came without a gate in the work order")
        gate_id = str(last.order["gate"]["id"])
        gate_approve(
            run_id,
            gate_id=gate_id,
            approver=EVAL_APPROVER,
            evidence=[f"eval harness auto-approval for case {run_id}"],
            root=root,
        )
    if last is None:
        raise FactoryError("eval case produced no loop step (max_steps is not positive)")
    return {"status": last.status, "code": last.code, "message": last.message, "loop_steps": loop_steps}


def _run_command(
    command: list[str], cwd: Path, timeout: int, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _product_tests(run_dir: Path, spec: dict[str, Any] | None) -> dict[str, Any]:
    if not spec:
        return {"ran": False, "ok": True, "command": [], "tests": None, "exit_code": None}
    command = [sys.executable if item == "{python}" else item for item in spec["command"]]
    timeout = int(spec.get("timeout_seconds", 120))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = _run_command(command, run_dir, timeout, env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ran": True,
            "ok": False,
            "command": command,
            "tests": None,
            "exit_code": None,
            "error": f"product tests could not run: {exc}",
        }
    output = (completed.stdout or "") + (completed.stderr or "")
    match = RAN_RE.search(output) or PASSED_RE.search(output)
    tests = int(match.group(1)) if match else None
    minimum = int(spec.get("min_tests", 0))
    error = None
    if completed.returncode != 0:
        error = f"product tests exited {completed.returncode}"
    elif tests is None:
        error = "product tests printed no test count"
    elif tests < minimum:
        error = f"product tests ran {tests}, expected at least {minimum}"
    return {
        "ran": True,
        "ok": error is None,
        "command": command,
        "tests": tests,
        "min_tests": minimum,
        "exit_code": completed.returncode,
        "error": error,
        "output_tail": output[-1500:],
    }


def _run_checks(run_dir: Path, checks_dir: Path | None) -> dict[str, Any]:
    if checks_dir is None:
        return {"ran": False, "ok": True, "files": []}
    target = run_dir / "checks"
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source in sorted(checks_dir.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        destination = target / source.relative_to(checks_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination.relative_to(run_dir).as_posix())
    if not copied:
        return {"ran": False, "ok": True, "files": [], "note": "checks/ has no Python files"}
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(target)]
    try:
        completed = _run_command(command, run_dir, 300, env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ran": True, "ok": False, "files": copied, "error": f"pytest could not run: {exc}"}
    output = (completed.stdout or "") + (completed.stderr or "")
    error = None
    if "No module named pytest" in output:
        error = "checks require pytest; install the dev extra"
    elif completed.returncode != 0:
        error = f"checks exited {completed.returncode}"
    return {
        "ran": True,
        "ok": error is None,
        "files": copied,
        "exit_code": completed.returncode,
        "error": error,
        "output_tail": output[-1500:],
    }


def _artifact_errors(
    run_dir: Path, state: dict[str, Any], expected: dict[str, Any], registry: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    for artifact_id in expected.get("artifacts") or []:
        spec = registry["artifacts"].get(artifact_id)
        if spec is None:
            errors.append(f"expected.json names unknown artifact {artifact_id!r}")
            continue
        spec = dict(spec)
        spec["id"] = artifact_id
        if spec.get("per_task"):
            missing = [
                task_id
                for task_id in state["tasks"]
                if not (run_dir / "tasks" / task_id / "deliverable.md").is_file()
            ]
            if missing:
                errors.append(f"missing per-task deliverable for {missing}")
            continue
        if spec.get("per_pattern"):
            if not pattern_paths(run_dir, spec):
                errors.append(f"missing artifact: {artifact_id} ({spec['path']})")
            continue
        path = artifact_path(run_dir, spec)
        if not path.exists():
            errors.append(f"missing artifact: {artifact_id} ({spec['path']})")
        elif spec.get("directory") and not any(
            item.is_file() and "__pycache__" not in item.parts for item in path.rglob("*")
        ):
            errors.append(f"directory artifact is empty: {artifact_id}")
    return errors


def _requirement_errors(run_dir: Path, expected: dict[str, Any]) -> list[str]:
    wanted = expected.get("requirements")
    if wanted is None:
        return []
    errors: list[str] = []
    try:
        matrix = build_matrix(run_dir)
    except FactoryError as exc:
        return [f"traceability unavailable: {exc}"]
    found = {row["req"] for row in matrix["requirements"]}
    missing = sorted(set(wanted) - found)
    extra = sorted(found - set(wanted))
    if missing:
        errors.append(f"requirements missing from traceability/spec: {missing}")
    if extra:
        errors.append(f"unexpected requirements in traceability/spec: {extra}")
    for row in matrix["requirements"]:
        if row["req"] in wanted and not row["covered"]:
            errors.append(
                f"{row['req']} is not covered (tasks={len(row['tasks'])}, "
                f"tests={len(row['tests'])}, acceptance={row['acceptance']!r})"
            )
    return errors


def _state_errors(state: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    build_tasks = [task for task in state["tasks"].values() if task.get("stage") == "s4_build"]
    wanted_tasks = (expected.get("tasks") or {}).get("count")
    if wanted_tasks is not None and len(build_tasks) != int(wanted_tasks):
        errors.append(f"s4_build has {len(build_tasks)} task(s), expected {wanted_tasks}")
    for gate_id in expected.get("gates_closed") or []:
        record = state["gate_status"].get(gate_id, {})
        if record.get("status") != "approved":
            errors.append(f"gate {gate_id} is {record.get('status', 'missing')}")
    return errors


def _submitter_errors(events: list[dict[str, Any]], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    submitters: dict[str, set[str]] = {}
    for event in events:
        if event.get("event_type") != "ARTIFACT_SUBMIT" or event.get("task_id"):
            continue
        submitters.setdefault(str(event.get("stage")), set()).add(str(event.get("role")))
    for stage, roles in (expected.get("submitters") or {}).items():
        missing = sorted(set(roles) - submitters.get(stage, set()))
        if missing:
            errors.append(f"stage {stage}: no artifact submit from {missing}")
    return errors


def _usage_rows(run_dir: Path) -> list[dict[str, Any]]:
    return UsageLedger(run_dir / "runtime" / "usage.jsonl").records()


def _billable(row: dict[str, Any]) -> int | None:
    tokens_in, tokens_out = row.get("tokens_in"), row.get("tokens_out")
    if tokens_in is None or tokens_out is None:
        return None
    return int(tokens_in) + int(tokens_out)


def _sum_optional(values: list[int | None]) -> int | None:
    total = 0
    for value in values:
        if value is None:
            return None
        total += value
    return total


def _observed(
    run_dir: Path,
    drive: dict[str, Any],
    product_tests: dict[str, Any],
    checks: dict[str, Any],
    verification_errors: list[str],
    state: dict[str, Any],
    events: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ledger = UsageLedger(run_dir / "runtime" / "usage.jsonl")
    totals = ledger.totals()
    approved = {
        gate_id: record
        for gate_id, record in state["gate_status"].items()
        if record.get("status") == "approved" and record.get("decided_by") != "system:greenfield"
    }
    pass_at_1 = (
        sum(1 for record in approved.values() if not record.get("attempts")) / len(approved)
        if approved
        else None
    )
    rework = [
        event
        for event in events
        if event.get("event_type") in {"REWORK", "VETO"}
        or (event.get("verdict") or {}).get("reason_code") == "GATE_REJECTED"
    ]
    per_stage: dict[str, dict[str, Any]] = {}
    for stage in load_pipeline()["stages"]:
        stage_id = stage["id"]
        stage_usage = [row for row in usage_rows if row.get("stage") == stage_id]
        stage_events = [event for event in events if event.get("stage") == stage_id]
        stage_tasks = [
            task for task in state["tasks"].values() if task.get("stage") == stage_id
        ]
        per_stage[stage_id] = {
            "steps": len(stage_usage),
            "events": len(stage_events),
            "seconds": round(sum(float(row.get("seconds") or 0.0) for row in stage_usage), 3),
            "tokens_billable": _sum_optional([_billable(row) for row in stage_usage]),
            "tasks": len(stage_tasks),
            "tasks_accepted": sum(1 for task in stage_tasks if task.get("status") == "accepted"),
        }
    return {
        "status": drive["status"],
        "steps": totals.steps,
        "loop_steps": drive["loop_steps"],
        "events": len(events),
        "rework_events": len(rework),
        "tasks": len([task for task in state["tasks"].values() if task.get("stage") == "s4_build"]),
        "gates_closed": sorted(approved),
        "pass_at_1": pass_at_1,
        "tokens": totals.tokens,
        "tokens_billable": totals.billable_tokens,
        "usd": totals.usd,
        "seconds": round(totals.seconds, 3),
        "prompt_chars": sum(int(row.get("prompt_chars") or 0) for row in usage_rows),
        "api_calls": _sum_optional([row.get("api_calls") for row in usage_rows])
        if any(row.get("api_calls") is not None for row in usage_rows)
        else None,
        "models": sorted({str(row["model"]) for row in usage_rows if row.get("model")}),
        "stages": per_stage,
        "product_tests": product_tests,
        "checks": checks,
        "verification_errors": verification_errors,
    }


def _case_errors(observed: dict[str, Any], verification_errors: list[str]) -> list[str]:
    errors = list(observed.get("artifact_errors") or [])
    errors.extend(observed.get("requirement_errors") or [])
    errors.extend(observed.get("state_errors") or [])
    errors.extend(observed.get("submitter_errors") or [])
    errors.extend(verification_errors)
    product_tests = observed["product_tests"]
    if product_tests.get("error"):
        errors.append(product_tests["error"])
    checks = observed["checks"]
    if checks.get("error"):
        errors.append(checks["error"])
    return errors


def run_case(
    bundle: dict[str, Any],
    executor: Executor,
    runs_root: Path,
    stamp: str,
    budget_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case = bundle["case"]
    expected = bundle["expected"]
    run_id = _case_run_id(case["id"], stamp)
    run_dir = safe_run_dir(run_id, runs_root)
    registry = load_artifacts(case["mode"])
    mode_budget = load_pipeline()["modes"][case["mode"]].get("budget")
    budget = _budget_for(case, mode_budget)
    for key, value in (budget_override or {}).items():
        if value is not None:
            budget[key] = value
    max_steps = int(case.get("max_steps", DEFAULT_MAX_STEPS))
    max_cycles = int(case.get("max_cycles", DEFAULT_MAX_CYCLES))

    with _factory_root(run_dir):
        init_run(run_id, case["mode"], root=runs_root, idea=bundle["idea"], force=run_dir.exists())
        constraints = bundle.get("constraints")
        if constraints:
            (run_dir / CONSTRAINTS_NAME).write_text(constraints, encoding="utf-8")
        seed = bundle.get("seed")
        if seed is not None:
            _seed_into(run_dir, seed)
        drive = _drive_run(
            run_id,
            executor,
            runs_root,
            max_steps=max_steps,
            max_cycles=max_cycles,
            budget=budget,
        )
        state = load_state(run_dir)
        events = read_events(run_dir / "blackbox.jsonl")
        usage_rows = _usage_rows(run_dir)
        verification_errors = verify_run(run_dir)
        artifact_errors = _artifact_errors(run_dir, state, expected, registry)
        requirement_errors = _requirement_errors(run_dir, expected)
        state_errors = _state_errors(state, expected)
        submitter_errors = _submitter_errors(events, expected)
        product_tests = _product_tests(run_dir, expected.get("product_tests"))
        checks = _run_checks(run_dir, bundle["checks"])
        observed = _observed(
            run_dir,
            drive,
            product_tests,
            checks,
            verification_errors,
            state,
            events,
            usage_rows,
        )
        observed["artifact_errors"] = artifact_errors
        observed["requirement_errors"] = requirement_errors
        observed["state_errors"] = state_errors
        observed["submitter_errors"] = submitter_errors
        errors = _case_errors(observed, verification_errors)
        try:
            health = compute_metrics(run_dir)
        except FactoryError as exc:
            health = {"error": str(exc)}

    expected_status = str(expected.get("status", "complete"))
    if drive["status"] != expected_status:
        errors.insert(0, f"run finished as {drive['status']!r}, expected {expected_status!r}")
    return {
        "case": case["id"],
        "title": case["title"],
        "mode": case["mode"],
        "run_id": run_id,
        "run_dir": str(run_dir),
        "ok": not errors,
        "status": drive["status"],
        "message": drive["message"],
        "errors": errors,
        "observed": observed,
        "metrics": health,
    }


def _baseline_record(result: dict[str, Any]) -> dict[str, Any]:
    observed = result["observed"]
    health = result.get("metrics") or {}
    return {
        "status": observed["status"],
        "steps": observed["steps"],
        "loop_steps": observed["loop_steps"],
        "events": observed["events"],
        "rework_events": observed["rework_events"],
        "tasks": observed["tasks"],
        "gates_closed": observed["gates_closed"],
        "pass_at_1": observed["pass_at_1"],
        "tokens": observed["tokens"],
        "tokens_billable": observed["tokens_billable"],
        "usd": observed["usd"],
        "seconds": observed["seconds"],
        "prompt_chars": observed["prompt_chars"],
        "verification_coverage": health.get("verification_coverage"),
        "traceability_coverage": health.get("traceability_coverage"),
    }


def _growth(value: float | int | None, base: float | int | None) -> float | None:
    if value is None or base is None:
        return None
    if base == 0:
        return 0.0 if value == 0 else float("inf")
    return (float(value) - float(base)) / float(base)


def compare_case_to_baseline(
    record: dict[str, Any], baseline: dict[str, Any], thresholds: dict[str, Any] | None = None
) -> list[str]:
    """Regression rules: steps may not grow, events may not fall, cost may not
    grow more than the threshold.  Missing numbers are reported, not invented."""
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    regressions: list[str] = []
    if record.get("status") != baseline.get("status"):
        regressions.append(
            f"status changed: {baseline.get('status')!r} -> {record.get('status')!r}"
        )
    steps_growth = int(record.get("steps", 0)) - int(baseline.get("steps", 0))
    if steps_growth > int(limits["steps_growth"]):
        regressions.append(
            f"steps grew: {baseline.get('steps')} -> {record.get('steps')} (+{steps_growth})"
        )
    events_fall = int(baseline.get("events", 0)) - int(record.get("events", 0))
    if events_fall > int(limits["events_fall"]):
        regressions.append(
            f"events fell: {baseline.get('events')} -> {record.get('events')} (-{events_fall})"
        )
    for key, limit_key in (("tokens_billable", "tokens_growth"), ("usd", "usd_growth")):
        growth = _growth(record.get(key), baseline.get(key))
        if growth is not None and growth > float(limits[limit_key]):
            regressions.append(
                f"{key} grew {growth:.1%}: {baseline.get(key)} -> {record.get(key)} "
                f"(limit {float(limits[limit_key]):.0%})"
            )
    return regressions


def _load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = load_json(path)
    validate_against_schema(data, "eval-baseline.schema.json", str(path))
    return data


def write_baseline(path: Path, results: list[dict[str, Any]]) -> None:
    payload = {
        "schema_version": "1.0.0",
        "generated_at": _now(),
        "cases": {result["case"]: _baseline_record(result) for result in results},
    }
    validate_against_schema(payload, "eval-baseline.schema.json", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_eval_set(
    cases_dir: Path,
    executor: Executor,
    *,
    root: str | Path | None = None,
    baseline: Path | None = None,
    update_baseline: Path | None = None,
    stamp: str | None = None,
    budget: dict[str, Any] | None = None,
    progress: Any = None,
    only: set[str] | None = None,
) -> dict[str, Any]:
    bundles = load_cases(cases_dir, only)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    runs_root = Path(root).expanduser().resolve() if root else default_runs_root()
    results: list[dict[str, Any]] = []
    for index, bundle in enumerate(bundles, start=1):
        if progress is not None:
            progress(index, bundle["case"])
        try:
            results.append(run_case(bundle, executor, runs_root, stamp, budget))
        except FactoryError as exc:
            results.append(
                {
                    "case": bundle["case"]["id"],
                    "title": bundle["case"]["title"],
                    "mode": bundle["case"]["mode"],
                    "run_id": _case_run_id(bundle["case"]["id"], stamp),
                    "run_dir": "",
                    "ok": False,
                    "status": "error",
                    "message": str(exc),
                    "errors": [str(exc)],
                    "observed": None,
                    "metrics": None,
                }
            )

    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "cases_dir": str(Path(cases_dir).resolve()),
        "stamp": stamp,
        "executor": getattr(executor, "name", type(executor).__name__),
        "cases": results,
        "baseline": {
            "path": None,
            "present": False,
            "regressions": [],
            "new_cases": [],
            "missing_cases": [],
        },
        "baseline_written": None,
        "ok": all(result["ok"] for result in results),
    }

    if update_baseline is not None:
        if not report["ok"]:
            failed = ", ".join(result["case"] for result in results if not result["ok"])
            raise FactoryError(
                f"refusing to write a baseline from failing case(s): {failed}"
            )
        write_baseline(update_baseline, results)
        report["baseline_written"] = str(Path(update_baseline).resolve())
        report["baseline"]["path"] = report["baseline_written"]
        return report

    path = Path(baseline) if baseline is not None else Path(cases_dir).parent / "baseline.json"
    existing = _load_baseline(path)
    report["baseline"]["path"] = str(path.resolve())
    if existing is None:
        return report
    report["baseline"]["present"] = True
    known = existing["cases"]
    regressions: list[str] = []
    for result in results:
        record = known.get(result["case"])
        case_cfg = next(
            bundle["case"] for bundle in bundles if bundle["case"]["id"] == result["case"]
        )
        thresholds = case_cfg.get("thresholds")
        if record is None:
            report["baseline"]["new_cases"].append(result["case"])
            continue
        if result.get("observed") is None:
            regressions.append(f"{result['case']}: case did not produce metrics")
            continue
        for message in compare_case_to_baseline(_baseline_record(result), record, thresholds):
            regressions.append(f"{result['case']}: {message}")
    report["baseline"]["missing_cases"] = sorted(
        set(known) - {result["case"] for result in results}
    )
    report["baseline"]["regressions"] = regressions
    report["ok"] = report["ok"] and not regressions
    return report


def format_eval_report(report: dict[str, Any]) -> str:
    """Human table: one line per case plus the first errors."""
    header = (
        f"{'CASE':22} {'STATUS':10} {'STEPS':>5} {'EVENTS':>6} {'TASKS':>5} "
        f"{'TOKENS':>8} {'USD':>7} {'CHECKS':>6} {'BASELINE':>9}"
    )
    lines = [header, "-" * len(header)]
    baseline_by_case: dict[str, str] = {}
    for message in report["baseline"]["regressions"]:
        case_id = message.split(":", 1)[0]
        baseline_by_case[case_id] = "REGRESSED"
    for result in report["cases"]:
        observed = result.get("observed") or {}
        checks = "ok" if result["ok"] else "FAIL"
        baseline_cell = baseline_by_case.get(result["case"], "match")
        if not report["baseline"]["present"]:
            baseline_cell = "-"
        elif result["case"] in report["baseline"]["new_cases"]:
            baseline_cell = "new"
        tokens = observed.get("tokens_billable")
        usd = observed.get("usd")
        lines.append(
            f"{result['case'][:22]:22} {result['status'][:10]:10} "
            f"{str(observed.get('steps', '-')):>5} {str(observed.get('events', '-')):>6} "
            f"{str(observed.get('tasks', '-')):>5} "
            f"{(str(tokens) if tokens is not None else '-'):>8} "
            f"{(f'{usd:.2f}' if isinstance(usd, (int, float)) else '-'):>7} "
            f"{checks:>6} {baseline_cell:>9}"
        )
    failures = [result for result in report["cases"] if not result["ok"]]
    if failures:
        lines.append("")
        for result in failures:
            lines.append(f"{result['case']}:")
            for error in result["errors"][:8]:
                lines.append(f"  - {error}")
    regressions = report["baseline"]["regressions"]
    if regressions:
        lines.append("")
        lines.append("BASELINE REGRESSIONS:")
        for message in regressions:
            lines.append(f"  - {message}")
    if report["baseline"]["new_cases"]:
        lines.append(
            "new cases without a baseline: " + ", ".join(report["baseline"]["new_cases"])
        )
    if report["baseline"]["missing_cases"]:
        lines.append(
            "baseline cases not run: " + ", ".join(report["baseline"]["missing_cases"])
        )
    if not report["baseline"]["present"] and not report["baseline_written"]:
        lines.append(
            "no baseline: pass --update-baseline eval/baseline.json to record one"
        )
    if report["baseline_written"]:
        lines.append(f"baseline written: {report['baseline_written']}")
    lines.append("EVAL OK" if report["ok"] else "EVAL FAILED")
    return "\n".join(lines)
