"""Host-side replay of the independent verifier's checks.

A verification report is prose: a command, the exit code the verifier says it
saw, and the output it says was printed.  Until this module existed, the engine
believed all three and checked only their shape -- a report could pass a gate
without a single test having run.

The host cannot read the verifier's mind, but it can re-run the commands.  At
gate closure every check whose argv starts with an entry from `verify_commands`
in `budget.yaml` is executed in a fresh bubblewrap:

* the whole filesystem is read-only, so the replay cannot change the product;
* the network namespace is unshared, so a test cannot reach a provider or an
  exfiltration endpoint;
* the operator's secret home paths are masked by the same `home_mask_args`
  the executor sandbox uses, so a reported `cat` sees what a sandboxed `cat`
  would have seen;
* writable space is a tmpfs `/tmp`, with the per-tool cache variables (pytest,
  Go, Cargo, npm, pip) pointed there -- that is what those tools need to build.

The allowlist is an argv prefix list, not a shell.  The command text is split
with `shlex` and passed to `execve` as a list; `;`, `|`, `$()` and quotes are
ordinary characters and never syntax.  Shells and `env` are refused by the
configuration validator: a shell inside the allowlist would undo exactly the
property the allowlist exists for.

What is compared:

* the exit code -- exactly; and a PASS may only replay commands that returned 0;
* the output -- line by line.  Every non-empty line the report claims must
  appear in the real combined stdout/stderr, in order.  The report may omit
  lines (a verifier rarely pastes a whole coverage table) and may use an
  ellipsis line for an omitted region, but it may not invent one.  Timing
  decorations, hex addresses, ISO timestamps and temporary paths are
  normalised on both sides before the comparison: pytest prints a different
  duration on every run.

If bubblewrap is not installed the replay is skipped and said so in evidence:
the factory supports Ubuntu, where it is a package dependency, and the
alternative -- running reported commands unsandboxed -- is worse than admitting
the check did not happen.  `factory doctor` reports this as unavailable.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import verification_checks
from .config import load_budget
from .runtime.sandbox import sandbox_available, sandbox_command, sandbox_env

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# `Ran 3 tests in 0.003s`, `1 passed in 0.08s`, `finished in 1.2 seconds`:
# only the clock decoration changes between two runs of the same suite.
DURATION_RE = re.compile(
    r"\bin \d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds?|min|mins|minutes?)\b"
)
HEX_ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]{6,}\b")
ISO_TIME_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
POSIX_TMP_RE = re.compile(r"/(?:tmp|var/tmp)/pytest-of-[^\s/]+/pytest-\d+/[^\s:]+")
POSIX_MKDTEMP_RE = re.compile(r"/(?:tmp|var/tmp)/tmp[0-9A-Za-z_.-]{8,}")
WINDOWS_TMP_RE = re.compile(r"[A-Za-z]:\\[^\s]*\\Temp\\[^\s:]+")
OMISSION_RE = re.compile(r"^(?:\.{3,}|…+|\[\.\.\.\]|<\.\.\.>)$")
OUTPUT_LIMIT = 4 * 1024 * 1024

CACHE_ROOT = "/tmp/factory-verify-cache"


def config() -> dict[str, Any]:
    """The operator's replay policy, already validated by `load_budget`."""
    data = load_budget()
    commands = data.get("verify_commands") or []
    return {
        "commands": [list(entry) for entry in commands],
        "timeout_s": float(data.get("verify_timeout_s", 600)),
        "max_commands": int(data.get("verify_max_commands", 10)),
        "require_for_pass": bool(data.get("verify_require_for_pass", True)),
        "output": str(data.get("verify_output", "strict")),
    }


def _parse_command(text: str) -> list[str] | None:
    """`shlex` only; no shell is ever involved after this point."""
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    if any("\x00" in part for part in parts):
        return None
    return parts


def _matches(argv: list[str], prefix: list[str]) -> bool:
    return len(argv) >= len(prefix) and argv[: len(prefix)] == prefix


def _product_has_files(run_dir: Path, registry: dict[str, Any] | None = None) -> bool:
    """Does the run carry a product tree at all?

    A spec/architecture stage has no `product/`; there is nothing to test and
    the mandatory replay does not apply.  The roots come from the mode's
    artifact registry, because `brownfield` remaps them to the seeded tree
    (`app/src-tauri/...`) instead of the greenfield `product/` layout.
    `os.walk` does not follow directory symlinks, so a product link cannot
    point the walk at the operator's home.
    """
    roots: list[Path] = []
    artifacts = (registry or {}).get("artifacts") or {}
    for name in ("product_src", "product_tests"):
        spec = artifacts.get(name) or {}
        raw = str(spec.get("path") or "").rstrip("/")
        if raw and "{" not in raw:
            roots.append(run_dir / raw)
    if not roots:
        roots = [run_dir / "product"]
    for root in roots:
        if not root.is_dir():
            continue
        for _root, _dirs, files in os.walk(root, followlinks=False):
            if files:
                return True
    return False


def _normalise(text: str) -> list[str]:
    text = ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw in text.split("\n"):
        line = " ".join(raw.split())
        if not line:
            continue
        line = DURATION_RE.sub("in <time>", line)
        line = HEX_ADDRESS_RE.sub("<addr>", line)
        line = ISO_TIME_RE.sub("<timestamp>", line)
        line = POSIX_TMP_RE.sub("<tmp>", line)
        line = POSIX_MKDTEMP_RE.sub("<tmp>", line)
        line = WINDOWS_TMP_RE.sub("<tmp>", line)
        lines.append(line)
    return lines


def _output_mismatch(claimed: str, actual: str) -> str | None:
    """The first claimed line the command never printed, if any."""
    actual_lines = _normalise(actual)
    position = 0
    for line in _normalise(claimed):
        if OMISSION_RE.match(line):
            continue
        found = position
        while found < len(actual_lines) and actual_lines[found] != line:
            found += 1
        if found == len(actual_lines):
            return (
                "the report claims the command printed "
                f"{line!r}, but the host's run did not produce that line"
            )
        position = found + 1
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


@dataclass
class ReplayResult:
    """The outcome of re-running one report, ready for `VerificationError`."""

    errors: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    executed: int = 0
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _execution_env() -> dict[str, str]:
    env = sandbox_env()
    cache = CACHE_ROOT
    # The report says `python3 -m pytest`; the interpreter that answers must be
    # the one the factory itself runs under, or the replay tests a different
    # installation and disagrees with a truthful verifier.
    interpreter_dir = str(Path(sys.executable).parent)
    env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": f"{cache}/pycache",
            "PYTEST_ADDOPTS": (env.get("PYTEST_ADDOPTS", "") + " -p no:cacheprovider").strip(),
            "XDG_CACHE_HOME": f"{cache}/xdg",
            "GOCACHE": f"{cache}/go-build",
            "GOTMPDIR": "/tmp",
            "GOPROXY": "off",
            "CARGO_TARGET_DIR": f"{cache}/cargo-target",
            "CARGO_NET_OFFLINE": "true",
            "npm_config_cache": f"{cache}/npm",
            "npm_config_offline": "true",
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    return env


def _run(argv: list[str], run_dir: Path, timeout: float) -> tuple[int | None, str, str | None]:
    """`(exit code, combined output, problem)` for one argv inside bwrap."""
    sandboxed = sandbox_command(argv, run_dir, enabled=True, network=False)
    try:
        completed = subprocess.run(
            sandboxed,
            cwd=run_dir,
            env=_execution_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return None, _text(exc.output), f"did not finish within {timeout:g}s"
    except OSError as exc:
        return None, "", f"cannot be started: {exc}"
    except subprocess.SubprocessError as exc:
        return None, "", f"failed: {exc}"
    output = completed.stdout or ""
    if len(output) > OUTPUT_LIMIT:
        output = output[:OUTPUT_LIMIT] + "\n[host replay: output truncated]\n"
    return completed.returncode, output, None


def replay_report(
    report_path: Path,
    run_dir: Path,
    *,
    require_executable: bool = True,
    registry: dict[str, Any] | None = None,
) -> ReplayResult:
    """Re-run the report's allowlisted checks; return errors that block PASS.

    `require_executable` is true at a stage gate, where a PASS must rest on at
    least one command the host can re-run if the run has a product.  A task
    report is accepted inside `s4_build`, where the suite is still being
    assembled and a later task's test may legitimately fail; there the replay
    binds the claims a report makes but does not demand that it make one.  The
    stage gate is the chokepoint that demands evidence for the whole product.

    The caller is a PASS path: a PASS is refused for any replay failure.  A
    report that says FAIL is not replayed, because a failure is not evidence
    for the work and running the adversarial commands again on a rejection
    path spends the budget for nothing.
    """
    result = ReplayResult()
    cfg = config()
    if not cfg["commands"]:
        result.evidence.append(
            "host replay: verify_commands is empty in budget.yaml; "
            "the report's commands were not re-run"
        )
        return result

    checks = verification_checks(report_path)
    if not checks:
        return result

    planned: list[tuple[Any, list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for check in checks:
        if not check.command:
            result.skipped += 1
            continue
        argv = _parse_command(check.command)
        if argv is None or not any(_matches(argv, prefix) for prefix in cfg["commands"]):
            result.skipped += 1
            continue
        key = tuple(argv)
        if key in seen:
            result.evidence.append(
                f"host replay: check {check.number} repeats the same command; the first run stands"
            )
            continue
        seen.add(key)
        planned.append((check, argv))

    if not planned:
        if require_executable and cfg["require_for_pass"] and _product_has_files(run_dir, registry):
            if sandbox_available():
                result.errors.append(
                    "the verification report has no replayable check: a PASS on a run with "
                    "product files must carry at least one '- Команда:' whose argv starts with "
                    "an entry from budget.yaml verify_commands"
                )
            else:
                result.evidence.append(
                    "host replay skipped: bubblewrap (bwrap) is not installed on this machine"
                )
        return result

    if len(planned) > cfg["max_commands"]:
        result.errors.append(
            f"the verification report asks the host to replay {len(planned)} commands; "
            f"budget.yaml verify_max_commands is {cfg['max_commands']}"
        )
        return result

    if not sandbox_available():
        result.evidence.append(
            "host replay skipped: bubblewrap (bwrap) is not installed on this machine"
        )
        return result

    for check, argv in planned:
        command = " ".join(argv)
        result.executed += 1
        exit_code, output, problem = _run(argv, run_dir, cfg["timeout_s"])
        if problem is not None:
            result.errors.append(
                f"verification check {check.number} ({command}): the host's replay {problem}"
            )
            continue
        assert exit_code is not None
        if check.exit_code is None:
            result.errors.append(
                f"verification check {check.number} ({command}): "
                "the report has no valid exit code to compare"
            )
            continue
        if exit_code != check.exit_code:
            result.errors.append(
                f"verification check {check.number} ({command}): the host ran it and got exit "
                f"{exit_code}, the report claims exit {check.exit_code}"
            )
            continue
        if check.exit_code != 0:
            result.errors.append(
                f"verification check {check.number} ({command}): a PASS report replays a command "
                f"that exited {exit_code}; a failing check cannot be the evidence for PASS"
            )
            continue
        if cfg["output"] == "strict" and check.output:
            mismatch = _output_mismatch(check.output, output)
            if mismatch is not None:
                result.errors.append(
                    f"verification check {check.number} ({command}): {mismatch}"
                )
                continue
        result.evidence.append(
            f"host replay {result.executed}: {command} -> exit {exit_code}, report exit "
            f"{check.exit_code}, output {'matched' if cfg['output'] == 'strict' else 'ignored'}"
        )
    return result
